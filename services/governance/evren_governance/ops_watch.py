"""Platform uyarıları (U8.9, ADR-0040): anlık görüntü, kural değerlendirmesi, uyarı yaşam döngüsü.

Her turda platformun anlık görüntüsü alınır (salt okuma; yalnızca gereken sütunlar), ``metadata/alerts.yaml``
kuralları değerlendirilir ve ``ops.alert`` güncellenir. Ateşlenme ve çözülme geçişleri bildirim üreticisine döner.
İki çalışan aynı anda değerlendirmesin diye tur danışma kilidiyle yapılır (kilidi alamayan tur atlanır).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from evren_core.ops import AlertRule, Beat, Condition, OpsSnapshot, evaluate, next_state

__all__ = ["Transition", "snapshot", "watch"]

log = logging.getLogger("evren.governance.ops")
#: pg_try_advisory_xact_lock anahtarı ("evren uyarı")
LOCK_KEY = 0x45564F50


@dataclass(frozen=True)
class Transition:
    alert_id: str
    action: str          # fired | resolved
    rule: AlertRule
    title: str
    detail: str


def snapshot(conn: psycopg.Connection[Any], now: datetime | None = None) -> OpsSnapshot:
    now = now or conn.execute("SELECT now()").fetchone()[0]
    beats = [Beat(kind, wid, seen, poll, stopped, details or {}) for kind, wid, seen, poll, stopped, details in
             conn.execute("""SELECT kind, worker_id, seen_at, poll_seconds, stopped_at, details
                               FROM ingest.worker_heartbeat
                              WHERE seen_at > %s - interval '1 day'
                                 OR worker_id IN (SELECT DISTINCT ON (kind) worker_id FROM ingest.worker_heartbeat
                                                   ORDER BY kind, seen_at DESC)""", (now,)).fetchall()]
    backlog, oldest = conn.execute("SELECT count(*), min(created_at) FROM events.outbox WHERE published_at IS NULL"
                                   ).fetchone()
    waiting = [r[0] for r in conn.execute(
        """SELECT coalesce(scheduled_for, requested_at) FROM ingest.connector_run_request
            WHERE status = 'pending' AND coalesce(scheduled_for, requested_at) <= %s
            ORDER BY 1 LIMIT 200""", (now,)).fetchall()]
    breakers = [r[0] for r in conn.execute(
        """SELECT d.name FROM ingest.data_source d
            WHERE d.lifecycle IN ('active', 'shadow', 'retiring')
              AND (SELECT r.breaker_state FROM ingest.source_run r
                    WHERE r.data_source = d.name AND r.finished_at IS NOT NULL
                    ORDER BY r.finished_at DESC LIMIT 1) = 'open'""").fetchall()]
    seal_end = conn.execute("SELECT max(period_end) FROM audit.seal").fetchone()[0]
    seal_failed = conn.execute("SELECT count(*) FROM audit.seal_check WHERE NOT ok AND checked_at > %s - interval "
                               "'24 hours'", (now,)).fetchone()[0]
    return OpsSnapshot(now=now, beats=beats, outbox_backlog=backlog, outbox_oldest=oldest, requests_waiting=waiting,
                       breakers_open=breakers, seal_last_end=seal_end, seal_failed_24h=seal_failed)


def watch(conn: psycopg.Connection[Any], rules: list[AlertRule], *, now: datetime | None = None) -> list[Transition]:
    """Kuralları değerlendirir, uyarıları günceller; ateşlenen ve çözülen uyarılar döner."""
    if not rules:
        return []
    out: list[Transition] = []
    with conn.transaction():
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            return []
        snap = snapshot(conn, now)
        now = snap.now
        present: dict[tuple[str, str], Condition] = {c.key: c for c in evaluate(snap, rules)}
        by_id = {r.id: r for r in rules}
        current = {(r[1], r[2]): r for r in conn.execute(
            """SELECT sys_id, rule, subject, state, pending_since, title, detail FROM ops.alert
                WHERE state <> 'resolved'""").fetchall()}
        for key in sorted(set(current) | set(present)):
            row, cond = current.get(key), present.get(key)
            rule = by_id.get(key[0])
            if rule is None:  # kural dosyadan kaldırıldı: açık uyarısı çözülür
                if row:
                    _resolve(conn, row, now, out, None)
                continue
            state = next_state(row[3] if row else None, cond is not None, row[4] if row else None, rule.for_minutes,
                               now)
            if row is None:
                if state is None or cond is None:
                    continue
                sys_id = conn.execute(
                    """INSERT INTO ops.alert (rule, subject, severity, state, title, detail, value, audience,
                                              pending_since, fired_at, last_seen_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                    (rule.id, cond.subject, rule.severity, state, cond.title, cond.detail, cond.value,
                     list(rule.audience), now, now if state == "firing" else None, now)).fetchone()[0]
                if state == "firing":
                    out.append(Transition(sys_id, "fired", rule, cond.title, cond.detail))
            elif state == "resolved":
                _resolve(conn, row, now, out, rule)
            elif cond is not None:
                fired = state == "firing"
                conn.execute(
                    """UPDATE ops.alert SET severity = %s, title = %s, detail = %s, value = %s, last_seen_at = %s,
                              state = CASE WHEN %s THEN 'firing' ELSE state END,
                              fired_at = CASE WHEN %s THEN %s ELSE fired_at END
                        WHERE sys_id = %s""",
                    (rule.severity, cond.title, cond.detail, cond.value, now, fired, fired, now, row[0]))
                if fired:
                    out.append(Transition(row[0], "fired", rule, cond.title, cond.detail))
    for t in out:
        log.warning("platform uyarısı %s: %s (%s)", "ateşlendi" if t.action == "fired" else "çözüldü", t.title,
                    t.rule.id)
    return out


def _resolve(conn: psycopg.Connection[Any], row: Any, now: datetime, out: list[Transition],
             rule: AlertRule | None) -> None:
    conn.execute("UPDATE ops.alert SET state = 'resolved', resolved_at = %s WHERE sys_id = %s", (now, row[0]))
    if row[3] == "firing" and rule is not None:
        out.append(Transition(row[0], "resolved", rule, row[5], row[6]))
