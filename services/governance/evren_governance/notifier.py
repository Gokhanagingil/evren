"""Bildirim üreticisi ve teslimcisi (U8.9, ADR-0040).

Tur sırası:

1. **Görev olayları** (``gov.task_event``, kaldığı yerden): atama, hatırlatma, süre aşımı → atanana; süre aşımında
   tırmanma (grubun yöneticisi, yoksa Konfigürasyon yöneticisi). Aynı olay için ikinci bildirim üretilmez.
2. **Uyarı geçişleri** (ateşlendi, çözüldü) → kuralın rollerine.
3. **Teslim planı:** yeni bildirimlerin dış kanal hedefleri (kişinin/grubun e-postası, rol ve grup yönlendirmeleri).
4. **Gönderim:** zamanı gelen teslimler; hata olursa artan aralıkla yeniden denenir, ``max_attempts`` sonra başarısız.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

import psycopg

from evren_core.notify import (
    Draft,
    Recipient,
    Route,
    TaskInfo,
    alert_drafts,
    delivery_targets,
    email_message,
    task_drafts,
    teams_card,
)

from .delivery import DeliveryError, EmailChannel, TeamsChannel
from .ops_watch import Transition

__all__ = ["Notifier"]

log = logging.getLogger("evren.governance.notify")
TASK_ACTIONS = ("created", "reassigned", "reminded", "overdue")


def insert_drafts(conn: psycopg.Connection[Any], drafts: Iterable[Draft], actor: str = "evren:bildirim") -> int:
    n = 0
    for d in drafts:
        row = conn.execute(
            """INSERT INTO notify.notification (kind, severity, audience_kind, audience, title, body, link, source,
                                                dedup_key, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (dedup_key) DO NOTHING RETURNING seq""",
            (d.kind, d.severity, d.audience_kind, d.audience, d.title, d.body, d.link, d.source, d.dedup_key,
             actor)).fetchone()
        n += row is not None
    return n


class Notifier:
    def __init__(self, *, email: EmailChannel | None, teams: TeamsChannel | None, base_url: str | None,
                 batch: int = 100, max_attempts: int = 6) -> None:
        self.email, self.teams, self.base_url = email, teams, base_url
        self.batch, self.max_attempts = batch, max_attempts

    # ------------------------------------------------------------------ üretim
    def from_tasks(self, conn: psycopg.Connection[Any]) -> int:
        made = 0
        with conn.transaction():
            conn.execute("INSERT INTO notify.cursor (stream, last_id) VALUES ('task_event', 0) ON CONFLICT DO NOTHING")
            last = conn.execute("SELECT last_id FROM notify.cursor WHERE stream = 'task_event' FOR UPDATE").fetchone()[0]
            rows = conn.execute(
                """SELECT e.id, e.action, t.sys_id, t.number, t.title, t.due_at, t.assignee_user, t.assignee_group,
                          g.manager, c.name, to_char(t.due_at AT TIME ZONE 'Europe/Istanbul', 'DD.MM.YYYY')
                     FROM gov.task_event e JOIN gov.task t ON t.sys_id = e.task
                     LEFT JOIN ref.sys_user_group g ON g.sys_id = t.assignee_group
                     LEFT JOIN cmdb.cmdb_ci c ON c.sys_id = t.ci_sys_id
                    WHERE e.id > %s ORDER BY e.id LIMIT %s""", (last, self.batch)).fetchall()
            for eid, action, *task, due_text in rows:
                if action in TASK_ACTIONS:
                    info = TaskInfo(*task)
                    made += insert_drafts(conn, task_drafts(eid, action, info, due_text=due_text or ""))
            if rows:
                conn.execute("UPDATE notify.cursor SET last_id = %s, updated_at = now() WHERE stream = 'task_event'",
                             (rows[-1][0],))
        return made

    def from_alerts(self, conn: psycopg.Connection[Any], transitions: Iterable[Transition]) -> int:
        made = 0
        for t in transitions:
            made += insert_drafts(conn, alert_drafts(t.alert_id, t.action, title=t.title, detail=t.detail,
                                                     advice=t.rule.advice, severity=t.rule.severity,
                                                     audience=t.rule.audience))
        return made

    # ------------------------------------------------------------------ teslim
    @property
    def email_on(self) -> bool:
        return self.email is not None

    def _teams_targets(self, routes: Iterable[Route]) -> set[str]:
        if self.teams is None:
            return set()
        known = self.teams.targets
        wanted = {r.target for r in routes if r.channel == "teams"}
        return wanted if known is None else wanted & set(known)

    def plan(self, conn: psycopg.Connection[Any]) -> int:
        planned = 0
        with conn.transaction():
            conn.execute("INSERT INTO notify.cursor (stream, last_id) VALUES ('delivery_plan', 0) ON CONFLICT DO NOTHING")
            last = conn.execute("SELECT last_id FROM notify.cursor WHERE stream = 'delivery_plan' FOR UPDATE"
                                ).fetchone()[0]
            rows = conn.execute(
                """SELECT n.seq, n.sys_id, n.kind, n.severity, n.audience_kind, n.audience, n.source,
                          u.email, g.email,
                          t.channel IS DISTINCT FROM 'evren' AND t.external_ref IS NOT NULL
                     FROM notify.notification n
                     LEFT JOIN ref.sys_user u ON n.audience_kind = 'user' AND u.sys_id = n.audience
                     LEFT JOIN ref.sys_user_group g ON n.audience_kind = 'group' AND g.sys_id = n.audience
                     LEFT JOIN gov.task t ON n.source = 'task:' || t.sys_id
                    WHERE n.seq > %s ORDER BY n.seq LIMIT %s""", (last, self.batch)).fetchall()
            for seq, nid, kind, severity, aud_kind, aud, _source, user_email, group_email, external in rows:
                routes = [Route(c, t, m) for c, t, m in conn.execute(
                    """SELECT channel, target, min_severity FROM notify.route
                        WHERE active AND audience_kind = %s AND audience = %s""", (aud_kind, aud)).fetchall()]
                rcpt = Recipient(user_email=user_email, group_email=group_email, routes=routes,
                                 external_task=bool(external) and kind != "test")
                for channel, target in delivery_targets(aud_kind, severity, rcpt, email_on=self.email_on,
                                                        teams_targets=self._teams_targets(routes)):
                    conn.execute("""INSERT INTO notify.delivery (notification, channel, target) VALUES (%s, %s, %s)
                                    ON CONFLICT DO NOTHING""", (nid, channel, target))
                    planned += 1
                last = seq
            if rows:
                conn.execute("UPDATE notify.cursor SET last_id = %s, updated_at = now() WHERE stream = 'delivery_plan'",
                             (last,))
        return planned

    def send_due(self, conn: psycopg.Connection[Any]) -> tuple[int, int]:
        """Zamanı gelen teslimleri gönderir: (gönderilen, başarısız sayılan)."""
        sent = failed = 0
        rows = conn.execute(
            """SELECT d.id, d.channel, d.target, d.attempts, n.title, n.body, n.link, n.severity
                 FROM notify.delivery d JOIN notify.notification n ON n.sys_id = d.notification
                WHERE d.status = 'pending' AND d.next_try_at <= now() ORDER BY d.id LIMIT %s""",
            (self.batch,)).fetchall()
        for did, channel, target, attempts, title, body, link, severity in rows:
            n = {"title": title, "body": body, "link": link, "severity": severity}
            try:
                if channel == "email":
                    if self.email is None:
                        raise DeliveryError("E-posta kanalı kapalı")
                    subject, text = email_message(n, self.base_url)
                    self.email.send(target, subject, text)
                else:
                    if self.teams is None:
                        raise DeliveryError("Teams kanalı kapalı")
                    self.teams.send(target, teams_card(n, self.base_url))
            except DeliveryError as exc:
                attempts += 1
                final = attempts >= self.max_attempts
                conn.execute(
                    """UPDATE notify.delivery SET attempts = %s, last_error = %s, next_try_at = now() + %s,
                              status = CASE WHEN %s THEN 'failed' ELSE 'pending' END WHERE id = %s""",
                    (attempts, str(exc)[:300], timedelta(minutes=2 ** min(attempts, 8)), final, did))
                failed += final
                log.warning("bildirim teslim edilemedi (%s, deneme %d): %s", channel, attempts, exc)
                continue
            conn.execute("UPDATE notify.delivery SET status = 'sent', sent_at = now(), attempts = %s, last_error = NULL "
                         "WHERE id = %s", (attempts + 1, did))
            sent += 1
        return sent, failed

    @staticmethod
    def stats(conn: psycopg.Connection[Any]) -> dict[str, int]:
        pending, failed = conn.execute(
            """SELECT count(*) FILTER (WHERE status = 'pending'),
                      count(*) FILTER (WHERE status = 'failed' AND created_at > now() - interval '1 day')
                 FROM notify.delivery""").fetchone()
        firing = conn.execute("SELECT count(*) FROM ops.alert WHERE state = 'firing'").fetchone()[0]
        return {"pending": pending, "failed_24h": failed, "alerts_firing": firing}
