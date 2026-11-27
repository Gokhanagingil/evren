"""Görev çalışanı (U8.4, ADR-0035).

Tur (``poll_seconds``'ta bir veya ``LISTEN evren_task`` bildirimiyle hemen):

1. **Teslim:** ServiceNow kanalındaki açık ve teslim bekleyen görev için ServiceNow'da kayıt açılır (önce
   ``correlation_id`` ile aranır; yeniden denemede ikinci kayıt açılmaz). ServiceNow'da atanacak karşılık yoksa
   (kişi ve grup ServiceNow'dan gelmiyor, varsayılan grup yok) görev Evren'de kalır (``kept_in_evren``).
2. **Eşitleme:** teslim edilmiş açık görevin ServiceNow kaydı okunur; ServiceNow'da kapatılmışsa görev Evren'de açık
   kalır, "kanalda kapatıldı" diye işaretlenir (karar Evren'de verilir).
3. **Kapatma:** Evren'de kapanan görevin ServiceNow kaydı kararla birlikte kapatılır.

Hata: ServiceNow'a ulaşılamazsa görev ``failed`` olur ve sonraki turda yeniden denenir (en çok ``max_attempts``).
Hata iletisi sır ve sunucu gövdesi içermez. Çalışan CMDB'ye yazamaz; yalnızca görevin teslim alanlarını yazar.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from evren_core.connectors.http import HttpClient, HttpError, Transport, UrllibTransport
from evren_core.governance import OUTCOMES, ChannelSettings, ServiceNowTaskChannel, ServiceNowTaskSimulator, TaskForChannel

from .config import GovernanceConfig

__all__ = ["CHANNEL", "GovernanceWorker"]

log = logging.getLogger("evren.governance")
CHANNEL = "evren_task"
ACTOR = "evren:gorev-calisani"


class GovernanceWorker:
    def __init__(self, config: GovernanceConfig, *, transport: Transport | None = None,
                 connect: Callable[[], psycopg.Connection[Any]] | None = None) -> None:
        self.config = config
        self.stop = threading.Event()
        self.started_at = time.time()
        self.processed = 0
        self.failed = 0
        self._connect = connect or (lambda: psycopg.connect(config.conninfo))
        self._last_cycle = 0.0
        self._simulator: ServiceNowTaskSimulator | None = None
        if transport is None and config.simulate:
            self._simulator = ServiceNowTaskSimulator()
            transport = self._simulator
        self.transport = transport
        self._channel: tuple[str, ServiceNowTaskChannel] | None = None

    # ------------------------------------------------------------------ bağlantı ve kanal
    def open(self) -> psycopg.Connection[Any]:
        conn = self._connect()
        conn.autocommit = True
        if self.config.role:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.config.role)))
        conn.execute("SET application_name = 'evren-governance'")
        return conn

    @property
    def sn_ready(self) -> bool:
        return self.transport is not None or bool(self.config.sn_url)

    @property
    def channel_mode(self) -> str:
        """Sistem durumunda görünen kanal durumu: simülatör | servicenow | kapalı (ServiceNow ayarı yok)."""
        if self._simulator is not None:
            return "simülatör"
        return "servicenow" if self.sn_ready else "kapalı"

    def channel(self, settings_doc: dict[str, Any]) -> ServiceNowTaskChannel:
        """Kanal ayarı veritabanından (Konfigürasyon yöneticisi değiştirir); bağlantı ayarı ortamdan."""
        settings = ChannelSettings.from_json(settings_doc)
        key = repr(settings.to_json())
        if self._channel and self._channel[0] == key:
            return self._channel[1]
        c = self.config
        if c.sn_token:
            auth = HttpClient.bearer(c.sn_token)
        elif c.sn_user and c.sn_password:
            auth = HttpClient.basic(c.sn_user, c.sn_password)
        else:
            auth = HttpClient.basic("simulator", "simulator")  # yalnızca simülatör (simulate=1)
        transport = self.transport or UrllibTransport(ca_file=c.sn_ca_file, allow_http=c.sn_allow_http)
        http = HttpClient(c.sn_url or "https://servicenow.simulator", transport=transport, auth_header=auth,
                          retries=2, backoff=0.5)
        ch = ServiceNowTaskChannel(http, settings)
        self._channel = (key, ch)
        return ch

    # ------------------------------------------------------------------ tur
    def cycle(self, conn: psycopg.Connection[Any]) -> dict[str, int]:
        self._last_cycle = time.monotonic()
        totals = {"delivered": 0, "kept": 0, "failed": 0, "synced": 0, "external_closed": 0, "closed": 0}
        sn = self._sn_settings(conn)
        if sn is not None and self.sn_ready:
            ch = self.channel(sn)
            self.deliver(conn, ch, totals)
            self.sync(conn, ch, totals)
            self.close(conn, ch, totals)
        self.heartbeat(conn, failed_now=totals["failed"] > 0)
        if self.config.health_file:
            Path(self.config.health_file).touch()
        return totals

    @staticmethod
    def _sn_settings(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
        row = conn.execute("SELECT settings FROM gov.task_channel WHERE kind = 'servicenow' AND lifecycle <> 'retired' "
                           "ORDER BY name LIMIT 1").fetchone()
        return dict(row[0]) if row else None

    def _event(self, conn: psycopg.Connection[Any], task: str, action: str, detail: dict[str, Any]) -> None:
        conn.execute("INSERT INTO gov.task_event (task, actor, action, detail) VALUES (%s, %s, %s, %s)",
                     (task, ACTOR, action, Jsonb(detail)))

    def deliver(self, conn: psycopg.Connection[Any], ch: ServiceNowTaskChannel, totals: dict[str, int]) -> None:
        rows = conn.execute(
            """SELECT t.sys_id, t.number, t.title, t.description, t.due_at, t.delivery_attempts,
                      CASE WHEN u.source = 'servicenow' AND u.active THEN u.sys_id END,
                      CASE WHEN g.source = 'servicenow' AND g.active THEN g.sys_id END,
                      CASE WHEN EXISTS (SELECT 1 FROM ingest.identity_link l JOIN ingest.data_source d
                                          ON d.name = l.data_source
                                         WHERE l.ci_sys_id = t.ci_sys_id AND l.active AND d.role = 'itsm'
                                           AND l.source_key = t.ci_sys_id) THEN t.ci_sys_id END
                 FROM gov.task t
                 JOIN gov.task_channel ch ON ch.name = t.channel AND ch.kind = 'servicenow'
                 LEFT JOIN ref.sys_user u ON u.sys_id = t.assignee_user
                 LEFT JOIN ref.sys_user_group g ON g.sys_id = t.assignee_group
                WHERE t.state = 'open' AND t.delivery_status IN ('pending', 'failed') AND t.delivery_attempts < %s
                ORDER BY t.created_at LIMIT %s""", (self.config.max_attempts, self.config.batch)).fetchall()
        for sys_id, number, title, desc, due, attempts, user, group, ci in rows:
            link = f"{self.config.public_url}/#/gorevler/{sys_id}" if self.config.public_url else None
            task = TaskForChannel(sys_id, number, title, desc or "", due, user, group, ci, link)
            if not ch.deliverable(task):
                conn.execute("""UPDATE gov.task SET delivery_status = 'kept_in_evren', delivery_note = %s
                                 WHERE sys_id = %s AND delivery_status IN ('pending', 'failed')""",
                             ("ServiceNow'da karşılığı olan sorumlu yok; görev Evren'de açık.", sys_id))
                self._event(conn, sys_id, "kept_in_evren", {"reason": "servicenow_sorumlu_yok"})
                totals["kept"] += 1
                continue
            try:
                rec = ch.create(task)
            except HttpError as exc:
                conn.execute("""UPDATE gov.task SET delivery_status = 'failed', delivery_note = %s,
                                       delivery_attempts = delivery_attempts + 1 WHERE sys_id = %s""",
                             (str(exc)[:300], sys_id))
                self._event(conn, sys_id, "delivery_failed", {"error": str(exc)[:300], "attempt": attempts + 1})
                totals["failed"] += 1
                self.failed += 1
                continue
            conn.execute("""UPDATE gov.task SET delivery_status = 'delivered', delivery_note = NULL,
                                   delivery_attempts = delivery_attempts + 1, external_ref = %s, external_number = %s,
                                   external_state = %s, external_closed = false, last_synced_at = now()
                             WHERE sys_id = %s""", (rec.sys_id, rec.number, rec.state, sys_id))
            self._event(conn, sys_id, "delivered", {"number": rec.number, "table": ch.settings.table})
            totals["delivered"] += 1
            self.processed += 1

    def sync(self, conn: psycopg.Connection[Any], ch: ServiceNowTaskChannel, totals: dict[str, int]) -> None:
        rows = conn.execute(
            """SELECT sys_id, external_ref FROM gov.task
                WHERE state = 'open' AND delivery_status = 'delivered' AND NOT external_closed
                  AND (last_synced_at IS NULL OR last_synced_at < now() - make_interval(mins => %s))
                ORDER BY last_synced_at NULLS FIRST LIMIT %s""", (self.config.sync_minutes, self.config.batch)).fetchall()
        for sys_id, ref in rows:
            try:
                rec = ch.get(ref)
            except HttpError as exc:
                log.warning("görev eşitlenemedi: %s (%s)", sys_id, exc)
                totals["failed"] += 1
                continue
            closed = ch.interpret(rec) != "open"
            conn.execute("""UPDATE gov.task SET external_state = %s, external_closed = %s, last_synced_at = now()
                             WHERE sys_id = %s""", (rec.state, closed, sys_id))
            totals["synced"] += 1
            if closed:
                self._event(conn, sys_id, "external_closed", {"number": rec.number, "state": rec.state})
                totals["external_closed"] += 1

    def close(self, conn: psycopg.Connection[Any], ch: ServiceNowTaskChannel, totals: dict[str, int]) -> None:
        rows = conn.execute(
            """SELECT sys_id, external_ref, outcome, coalesce(outcome_note, '') FROM gov.task
                WHERE state <> 'open' AND delivery_status = 'delivered' AND external_ref IS NOT NULL
                ORDER BY closed_at LIMIT %s""", (self.config.batch,)).fetchall()
        for sys_id, ref, outcome, note in rows:
            text = f"Evren'de kapatıldı: {OUTCOMES.get(outcome, outcome)}" + (f" — {note}" if note else "")
            try:
                rec = ch.close(ref, outcome=outcome, note=text)
            except HttpError as exc:
                log.warning("ServiceNow kaydı kapatılamadı: %s (%s)", sys_id, exc)
                totals["failed"] += 1
                continue
            conn.execute("""UPDATE gov.task SET delivery_status = 'closed_sent', external_state = %s,
                                   external_closed = true, last_synced_at = now() WHERE sys_id = %s""",
                         (rec.state, sys_id))
            self._event(conn, sys_id, "closed_sent", {"number": rec.number, "state": rec.state})
            totals["closed"] += 1

    def heartbeat(self, conn: psycopg.Connection[Any], *, stopped: bool = False, failed_now: bool = False) -> None:
        row = conn.execute("""SELECT count(*) FILTER (WHERE delivery_status = 'pending'),
                                     count(*) FILTER (WHERE delivery_status = 'failed'),
                                     count(*) FILTER (WHERE external_closed AND state = 'open')
                                FROM gov.task WHERE state = 'open'""").fetchone()
        details = {"channel": self.channel_mode, "pending": row[0], "failed": row[1],
                   "external_closed": row[2]}
        conn.execute(
            """INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed, failed,
                                                   last_error_at, poll_seconds, kind, details)
               VALUES (%(id)s, to_timestamp(%(started)s), now(), CASE WHEN %(stopped)s THEN now() END, %(processed)s,
                       %(failed)s, CASE WHEN %(failed_now)s THEN now() END, %(poll)s, 'governance', %(details)s)
               ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(),
                 stopped_at = CASE WHEN %(stopped)s THEN now() END,
                 processed = EXCLUDED.processed, failed = EXCLUDED.failed,
                 last_error_at = COALESCE(EXCLUDED.last_error_at, ingest.worker_heartbeat.last_error_at),
                 poll_seconds = EXCLUDED.poll_seconds, details = EXCLUDED.details""",
            {"id": self.config.worker_id, "started": self.started_at, "stopped": stopped, "processed": self.processed,
             "failed": self.failed, "failed_now": failed_now, "poll": self.config.poll_seconds,
             "details": Jsonb(details)})

    # ------------------------------------------------------------------ döngü
    def run_once(self) -> dict[str, int]:
        with self.open() as conn:
            totals = self.cycle(conn)
            self.heartbeat(conn, stopped=True)
        return totals

    def serve(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                with self.open() as conn:
                    conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(CHANNEL)))
                    log.info("görev çalışanı hazır: kimlik=%s kanal=%s", self.config.worker_id, self.channel_mode)
                    backoff = 1.0
                    self.cycle(conn)
                    while not self.stop.is_set():
                        notified = any(True for _ in conn.notifies(timeout=1.0, stop_after=1))
                        if notified or time.monotonic() - self._last_cycle >= self.config.poll_seconds:
                            self.cycle(conn)
                    self.heartbeat(conn, stopped=True)
            except psycopg.OperationalError as exc:
                log.warning("veritabanı bağlantısı yok (%s); %.0f sn sonra yeniden denenecek", type(exc).__name__,
                            backoff)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        log.info("görev çalışanı durdu: teslim=%d başarısız=%d", self.processed, self.failed)
