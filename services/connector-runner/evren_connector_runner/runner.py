"""Bağlayıcı çalıştırıcı servisi (U5.3b, ADR-0021 §3–4).

    EVREN_CONNECTORS_FILE=/etc/evren/connectors.yaml EVREN_GATEWAY_URL=https://gecit:8089 \\
    EVREN_DATABASE_URL_FILE=/run/secrets/connector_dsn python -m evren_connector_runner
    python -m evren_connector_runner --once      # bir tur (zamanlama + bekleyen istekler), çalışmaları bekle, çık

Tek iş parçacığı; her çalışma ayrı alt süreçtir (``tools/connector_run.py``): süre sınırı ve iptal gerçek, bozuk
yanıt çalıştırıcıyı düşürmez. Alt sürecin ortamı en az hâliyle kurulur: çalıştırıcının veritabanı adresi aktarılmaz;
yalnızca o örneğin ``EVREN_SOURCE_*``/``EVREN_GATEWAY_*`` değişkenleri (dosya YOLLARI, sır değil).

Tur (``poll_seconds``'ta bir veya ``LISTEN evren_connector`` bildirimiyle hemen):
nabız → örnekleri bildir → zamanlanmış istekleri aç → kurtarma (yanıt vermeyen çalıştırıcı, süresi dolan istek) →
boş yuva kadar bekleyen isteği al ve başlat. Her saniye: biten süreçleri sonuçlandır, iptal ve süre sınırını uygula.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal

# alt süreç: sabit argüman listesi, kabuk yok
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from evren_core.metadata import Metadata, load_metadata

from .config import REPO_ROOT, InstanceConfig, RunnerConfig, load_instances

__all__ = ["Runner"]

log = logging.getLogger("evren.connector_runner")
CHANNEL = "evren_connector"
SCHEDULER = "zamanlayici"
CONNECTOR_RUN = REPO_ROOT / "tools" / "connector_run.py"
DELTA_OVERLAP = timedelta(minutes=10)
PENDING_TTL = "24 hours"
MAX_CATCHUP_MINUTES = 60
KILL_GRACE_SECONDS = 10
PASS_ENV = ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
            "http_proxy", "NO_PROXY", "no_proxy")
SUMMARY_KEYS = ("fetched", "mapped", "skipped", "sent", "errors", "completed", "gateway_state")
_URL = re.compile(r"https?://[^\s\"'<>]+")


@dataclass
class Job:
    request_id: uuid.UUID
    instance: InstanceConfig
    run_id: uuid.UUID
    effective_mode: str
    proc: subprocess.Popen[bytes]
    stdout: IO[bytes]
    stderr: IO[bytes]
    deadline: float
    started: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    timed_out: bool = False
    term_at: float | None = None


def redact(text: str, values: list[str]) -> str:
    """Sonuç notundan adres, kullanıcı adı ve dosya yollarını çıkarır (ADR-0021 §3); en çok 300 karakter."""
    for v in values:
        text = text.replace(v, "‹yapılandırma›")
    text = _URL.sub("‹adres›", text)
    text = " ".join(text.split())
    return text[:297] + "…" if len(text) > 300 else text


class Runner:
    kind = "connector"

    def __init__(self, config: RunnerConfig, metadata: Metadata | None = None,
                 instances: list[InstanceConfig] | None = None,
                 connect: Callable[[], psycopg.Connection[Any]] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 command: Callable[[InstanceConfig], list[str]] | None = None) -> None:
        self.config = config
        self.md = metadata or load_metadata(config.metadata_dir)
        self.instances = {i.name: i for i in (instances if instances is not None else load_instances(
            config.connectors_file, self.md, allow_simulate=config.allow_simulate))}
        self._connect = connect or (lambda: psycopg.connect(config.conninfo, autocommit=True))
        self.clock = clock
        self._command = command
        self.stop = threading.Event()
        self.jobs: dict[uuid.UUID, Job] = {}
        self.processed = 0
        self.failed = 0
        self.started_at = time.time()
        self._last_minute: datetime | None = None
        self._last_cycle = 0.0

    # ------------------------------------------------------------------ bağlantı
    def open(self) -> psycopg.Connection[Any]:
        conn = self._connect()
        conn.autocommit = True
        if self.config.role:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.config.role)))
        conn.execute("SET application_name = 'evren-connector-runner'")
        return conn

    # ------------------------------------------------------------------ tur
    def cycle(self, conn: psycopg.Connection[Any]) -> dict[str, int]:
        self._last_cycle = time.monotonic()
        self.heartbeat(conn)
        self.register(conn)
        opened = self.schedule(conn)
        recovered = self.recover(conn)
        started = self.claim(conn)
        if self.config.health_file:
            Path(self.config.health_file).touch()
        return {"scheduled": opened, "recovered": recovered, "started": started}

    def heartbeat(self, conn: psycopg.Connection[Any], *, stopped: bool = False, failed_now: bool = False) -> None:
        conn.execute(
            """INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed, failed,
                                                   last_error_at, poll_seconds, kind)
               VALUES (%(id)s, to_timestamp(%(started)s), now(), CASE WHEN %(stopped)s THEN now() END, %(processed)s,
                       %(failed)s, CASE WHEN %(failed_now)s THEN now() END, %(poll)s, 'connector')
               ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(),
                 stopped_at = CASE WHEN %(stopped)s THEN now() END,
                 processed = EXCLUDED.processed, failed = EXCLUDED.failed,
                 last_error_at = COALESCE(EXCLUDED.last_error_at, ingest.worker_heartbeat.last_error_at),
                 poll_seconds = EXCLUDED.poll_seconds""",
            {"id": self.config.worker_id, "started": self.started_at, "stopped": stopped, "processed": self.processed,
             "failed": self.failed, "failed_now": failed_now, "poll": self.config.poll_seconds})

    def _next_run(self, inst: InstanceConfig) -> datetime | None:
        if not inst.schedule or not inst.enabled:
            return None
        return inst.schedule.next_after(self.clock().astimezone(self.config.tz))

    def register(self, conn: psycopg.Connection[Any]) -> None:
        """Örneklerin sırsız özeti (ADR-0021 §2): adres, kullanıcı ve dosya yolu yazılmaz."""
        with conn.cursor() as cur:
            for inst in self.instances.values():
                cur.execute(
                    """INSERT INTO ingest.connector_instance (name, connector, data_source, schedule, schedule_mode,
                                                             schedule_tz, next_run_at, enabled, max_runtime_minutes,
                                                             runner_id, seen_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                       ON CONFLICT (name) DO UPDATE SET connector = EXCLUDED.connector,
                         data_source = EXCLUDED.data_source, schedule = EXCLUDED.schedule,
                         schedule_mode = EXCLUDED.schedule_mode, schedule_tz = EXCLUDED.schedule_tz,
                         next_run_at = EXCLUDED.next_run_at, enabled = EXCLUDED.enabled,
                         max_runtime_minutes = EXCLUDED.max_runtime_minutes, runner_id = EXCLUDED.runner_id,
                         seen_at = now()""",
                    (inst.name, inst.connector, inst.data_source,
                     inst.schedule.expression if inst.schedule else None, inst.schedule_mode, self.config.tz.key,
                     self._next_run(inst), inst.enabled, inst.max_runtime_minutes, self.config.worker_id))

    # ------------------------------------------------------------------ zamanlama (ADR-0021 §4)
    def due_minutes(self) -> list[datetime]:
        """Son turdan bu yana geçen yerel dakikalar (en çok 60); ilk turda yalnızca şimdiki dakika."""
        now = self.clock().astimezone(self.config.tz).replace(second=0, microsecond=0)
        last = self._last_minute
        self._last_minute = now
        if last is None or now <= last:
            return [now] if last is None else []
        minutes = []
        cur = now
        while cur > last and len(minutes) < MAX_CATCHUP_MINUTES:
            minutes.append(cur)
            cur -= timedelta(minutes=1)
        if cur > last:
            log.warning("çalıştırıcı %d dakikadan uzun süre tur atmadı; eski zamanlamalar telafi edilmez",
                        MAX_CATCHUP_MINUTES)
        return sorted(minutes)

    def schedule(self, conn: psycopg.Connection[Any]) -> int:
        minutes = self.due_minutes()
        opened = 0
        for inst in self.instances.values():
            if not inst.schedule or not inst.enabled:
                continue
            due = [m for m in minutes if inst.schedule.matches(m)]
            if not due:
                continue
            if len(due) > 1:
                log.info("%s: %d zamanlama kaçırıldı; yalnızca sonuncusu çalışır", inst.name, len(due) - 1)
            mode, since = inst.schedule_mode, None
            if mode == "delta":
                since = self._delta_since(conn, inst.name)
                if since is None:
                    mode = "snapshot"  # başarılı çalışma yok: tam liste
            row = conn.execute(
                """INSERT INTO ingest.connector_run_request (instance, data_source, mode, since, origin, scheduled_for,
                                                            requested_by)
                   VALUES (%s, %s, %s, %s, 'schedule', %s, %s) ON CONFLICT DO NOTHING RETURNING id""",
                (inst.name, inst.data_source, mode, since, due[-1], SCHEDULER)).fetchone()
            if row:
                opened += 1
            else:
                log.info("%s: zamanlanmış çalışma açılmadı (önceki çalışma sürüyor veya dakika zaten açıldı)",
                         inst.name)
        return opened

    @staticmethod
    def _delta_since(conn: psycopg.Connection[Any], instance: str) -> datetime | None:
        row = conn.execute("""SELECT max(started_at) FROM ingest.connector_run_request
                               WHERE instance = %s AND status = 'succeeded'""", (instance,)).fetchone()
        return row[0] - DELTA_OVERLAP if row and row[0] else None

    # ------------------------------------------------------------------ kurtarma
    def recover(self, conn: psycopg.Connection[Any]) -> int:
        """Yanıt vermeyen çalıştırıcının yürüyen isteği ve 24 saati geçen bekleyen istek kapatılır."""
        n = 0
        with conn.transaction():
            n += conn.execute(
                """UPDATE ingest.connector_run_request r
                      SET status = 'failed', finished_at = now(),
                          result_note = 'Çalıştırıcı yanıt vermiyor; çalışma yarıda kaldı.'
                    WHERE r.status = 'running' AND r.claimed_by <> %s
                      AND NOT EXISTS (SELECT 1 FROM ingest.worker_heartbeat h
                                       WHERE h.worker_id = r.claimed_by AND h.stopped_at IS NULL
                                         AND h.seen_at > now() - 3 * h.poll_seconds * interval '1 second')""",
                (self.config.worker_id,)).rowcount
            # kendi kaydımız olup elimizde olmayan (ör. çökme sonrası aynı kimlik) istek
            n += conn.execute(
                """UPDATE ingest.connector_run_request SET status = 'failed', finished_at = now(),
                          result_note = 'Çalıştırıcı yeniden başladı; çalışma yarıda kaldı.'
                    WHERE status = 'running' AND claimed_by = %s AND NOT (id = ANY(%s))""",
                (self.config.worker_id, list(self.jobs))).rowcount
            n += conn.execute(
                """UPDATE ingest.connector_run_request SET status = 'rejected', finished_at = now(),
                          result_note = 'Süresi doldu: 24 saat içinde çalıştırılmadı.'
                    WHERE status = 'pending' AND requested_at < now() - %s::interval""", (PENDING_TTL,)).rowcount
        return n

    # ------------------------------------------------------------------ alma ve başlatma
    def claim(self, conn: psycopg.Connection[Any]) -> int:
        free = self.config.max_parallel - len(self.jobs)
        if free <= 0 or not self.instances:
            return 0
        started = 0
        lifecycle = dict(conn.execute("SELECT name, lifecycle FROM ingest.data_source").fetchall())
        with conn.transaction():
            rows = conn.execute(
                """SELECT id, instance, mode, since, origin, requested_by FROM ingest.connector_run_request
                    WHERE status = 'pending' AND instance = ANY(%s) ORDER BY requested_at LIMIT %s
                    FOR UPDATE SKIP LOCKED""", (sorted(self.instances), free)).fetchall()
            launch: list[tuple[uuid.UUID, InstanceConfig, str, datetime | None, uuid.UUID, str, str]] = []
            for rid, name, mode, since, origin, by in rows:
                inst = self.instances[name]
                reason = None
                if lifecycle.get(inst.data_source) == "retired":
                    reason = "Veri kaynağı emekliye ayrılmış; çalıştırılmadı."
                elif not inst.enabled:
                    reason = "Bağlayıcı örneği çalıştırıcıda devre dışı; çalıştırılmadı."
                if reason:
                    conn.execute("""UPDATE ingest.connector_run_request SET status = 'rejected', finished_at = now(),
                                           result_note = %s WHERE id = %s""", (reason, rid))
                    continue
                if mode == "delta" and since is None:
                    since = self._delta_since(conn, name)
                effective = mode if (mode == "snapshot" or since is not None) else "snapshot"
                run_id = uuid.uuid4()
                conn.execute("""UPDATE ingest.connector_run_request SET status = 'running', claimed_by = %s,
                                       started_at = now(), run_id = %s WHERE id = %s""",
                             (self.config.worker_id, run_id, rid))
                conn.execute("""INSERT INTO audit.security_event (principal, action, resource, decision, detail)
                                VALUES (%s, 'connector_run_start', %s, 'allow', %s)""",
                             (self.config.worker_id, f"connector_run_request:{rid}",
                              Jsonb({"instance": name, "origin": origin, "requested_by": by, "run_id": str(run_id),
                                     "mode": effective})))
                launch.append((rid, inst, effective, since, run_id, origin, by))
        for rid, inst, effective, since, run_id, _origin, _by in launch:
            try:
                self.jobs[rid] = self._spawn(rid, inst, effective, since, run_id)
                started += 1
            except OSError as exc:
                self._finish(conn, rid, "failed", None, {"effective_mode": effective},
                             f"Bağlayıcı süreci başlatılamadı ({type(exc).__name__}).")
        return started

    def command(self, inst: InstanceConfig) -> list[str]:
        if self._command:
            return self._command(inst)
        return [sys.executable, str(CONNECTOR_RUN)]

    def child_env(self, inst: InstanceConfig) -> dict[str, str]:
        """Alt sürecin ortamı: geçiş listesindeki sistem değişkenleri + örneğin yolları. DB adresi YOK."""
        env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "EVREN_LOG_LEVEL": "WARNING",
                    "EVREN_GATEWAY_URL": self.config.gateway_url})
        if self.config.gateway_ca_file:
            env["EVREN_GATEWAY_CA_FILE"] = self.config.gateway_ca_file
        if self.config.gateway_allow_http:
            env["EVREN_GATEWAY_ALLOW_HTTP"] = "1"
        env.update(inst.source_env())
        return env

    def argv(self, inst: InstanceConfig, mode: str, since: datetime | None, run_id: uuid.UUID) -> list[str]:
        args = [*self.command(inst), "--connector", inst.connector, "--name", inst.name, "--mode", mode,
                "--run-id", str(run_id)]
        if mode == "delta" and since is not None:
            args += ["--since", since.astimezone(UTC).isoformat()]
        if inst.simulate is not None:
            args += ["--simulate", str(inst.simulate)]
        if inst.page_size:
            args += ["--page-size", str(inst.page_size)]
        if inst.api_release:
            args += ["--api-release", inst.api_release]
        return args

    def _spawn(self, rid: uuid.UUID, inst: InstanceConfig, mode: str, since: datetime | None,
               run_id: uuid.UUID) -> Job:
        out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()  # boru tıkanması yok
        # kabuk yok, argümanlar sabit liste; ortam en az hâliyle (child_env)
        proc = subprocess.Popen(self.argv(inst, mode, since, run_id), env=self.child_env(inst),
                                stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=str(REPO_ROOT),
                                start_new_session=True)
        log.info("%s: çalışma başladı (istek=%s, çalışma=%s, mod=%s)", inst.name, rid, run_id, mode)
        return Job(rid, inst, run_id, mode, proc, out, err, time.monotonic() + inst.max_runtime_minutes * 60)

    # ------------------------------------------------------------------ izleme ve sonuçlandırma
    def reap(self, conn: psycopg.Connection[Any]) -> int:
        """Biten süreçleri sonuçlandırır; iptal isteğini ve süre sınırını uygular. Dönen: sonuçlanan sayısı."""
        if not self.jobs:
            return 0
        cancel = {r[0] for r in conn.execute(
            """SELECT id FROM ingest.connector_run_request WHERE id = ANY(%s) AND cancel_requested_at IS NOT NULL""",
            (list(self.jobs),)).fetchall()}
        done = 0
        now = time.monotonic()
        for rid, job in list(self.jobs.items()):
            if job.proc.poll() is None:
                if rid in cancel and not job.cancelled:
                    job.cancelled = True
                    self._terminate(job, now)
                elif now > job.deadline and not job.timed_out:
                    job.timed_out = True
                    self._terminate(job, now)
                elif job.term_at is not None and now - job.term_at > KILL_GRACE_SECONDS:
                    job.proc.kill()
                continue
            self._complete(conn, rid, job)
            del self.jobs[rid]
            done += 1
        return done

    @staticmethod
    def _terminate(job: Job, now: float) -> None:
        job.term_at = now
        try:
            os.killpg(job.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def _complete(self, conn: psycopg.Connection[Any], rid: uuid.UUID, job: Job) -> None:
        rc = job.proc.returncode
        report = self._report(job.stdout)
        first_err = self._first_line(job.stderr)
        job.stdout.close()
        job.stderr.close()
        summary: dict[str, Any] = {k: report[k] for k in SUMMARY_KEYS if k in report}
        if isinstance(report.get("by_class"), dict):
            summary["by_class"] = {str(k)[:80]: v for k, v in list(report["by_class"].items())[:50]
                                   if isinstance(v, int)}
        summary["effective_mode"] = job.effective_mode
        summary["duration_s"] = round(time.monotonic() - job.started, 1)
        if job.cancelled:
            status, note = "cancelled", "İptal edildi; çalışma yarıda kesildi."
        elif job.timed_out:
            status, note = "failed", f"Süre sınırı aşıldı ({job.instance.max_runtime_minutes} dk); süreç durduruldu."
        elif rc == 0:
            status, note = "succeeded", None
        else:
            status = "failed"
            note = report.get("failure") if isinstance(report.get("failure"), str) else None
            note = note or first_err or f"Bağlayıcı çıkış kodu {rc}."
        if note:
            note = redact(note, job.instance.redactions())
        self._finish(conn, rid, status, rc, summary, note)
        log.info("%s: çalışma bitti (istek=%s, durum=%s, çıkış=%s)", job.instance.name, rid, status, rc)

    def _finish(self, conn: psycopg.Connection[Any], rid: uuid.UUID, status: str, rc: int | None,
                summary: dict[str, Any], note: str | None) -> None:
        conn.execute("""UPDATE ingest.connector_run_request SET status = %s, finished_at = now(), exit_code = %s,
                               summary = %s, result_note = %s WHERE id = %s AND status = 'running'""",
                     (status, max(-255, min(255, rc)) if rc is not None else None, Jsonb(summary), note, rid))
        self.processed += 1
        if status == "failed":
            self.failed += 1

    @staticmethod
    def _report(fh: IO[bytes]) -> dict[str, Any]:
        fh.seek(0)
        data = fh.read(1_000_000).decode("utf-8", "replace")
        for line in reversed(data.splitlines()):
            if line.strip().startswith("{"):
                try:
                    out = json.loads(line)
                except ValueError:
                    return {}
                return out if isinstance(out, dict) else {}
        return {}

    @staticmethod
    def _first_line(fh: IO[bytes]) -> str | None:
        fh.seek(0)
        data = fh.read(64_000).decode("utf-8", "replace")
        return next((ln.strip() for ln in data.splitlines() if ln.strip()), None)

    def shutdown(self, conn: psycopg.Connection[Any] | None) -> None:
        """Durdurma: yürüyen süreçleri sonlandırır ve isteklerini 'failed' olarak kapatır."""
        for job in self.jobs.values():
            self._terminate(job, time.monotonic())
        deadline = time.monotonic() + KILL_GRACE_SECONDS
        for job in self.jobs.values():
            try:
                job.proc.wait(max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                job.proc.kill()
                job.proc.wait()
        if conn is not None:
            for rid, job in list(self.jobs.items()):
                job.stdout.close()
                job.stderr.close()
                self._finish(conn, rid, "failed", job.proc.returncode, {"effective_mode": job.effective_mode},
                             "Çalıştırıcı durduruldu; çalışma yarıda kesildi.")
            self.heartbeat(conn, stopped=True)
        self.jobs.clear()

    # ------------------------------------------------------------------ döngü
    def run_once(self, timeout: float | None = None) -> dict[str, int]:
        """Bir tur + başlatılan çalışmaların bitmesini bekler (``--once``; dış zamanlayıcı veya bakım)."""
        with self.open() as conn:
            totals = self.cycle(conn)
            limit = time.monotonic() + (timeout if timeout is not None else 24 * 3600)
            while self.jobs and time.monotonic() < limit and not self.stop.is_set():
                self.reap(conn)
                time.sleep(0.2)
            if self.jobs:
                self.shutdown(conn)
            else:
                self.heartbeat(conn, stopped=True)
        return totals

    def serve(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                with self.open() as conn:
                    conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(CHANNEL)))
                    log.info("bağlayıcı çalıştırıcısı hazır: kimlik=%s örnek=%d paralel=%d", self.config.worker_id,
                             len(self.instances), self.config.max_parallel)
                    backoff = 1.0
                    self.cycle(conn)
                    while not self.stop.is_set():
                        notified = any(True for _ in conn.notifies(timeout=1.0, stop_after=1))
                        finished = self.reap(conn)
                        if notified or finished or time.monotonic() - self._last_cycle >= self.config.poll_seconds:
                            self.cycle(conn)
                    self.shutdown(conn)
            except psycopg.OperationalError as exc:
                log.warning("veritabanı bağlantısı yok (%s); %.0f sn sonra yeniden denenecek", type(exc).__name__,
                            backoff)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        if self.jobs:  # bağlantısız durdurma: süreçleri yine de sonlandır
            self.shutdown(None)
        log.info("bağlayıcı çalıştırıcısı durdu: sonuçlanan=%d başarısız=%d", self.processed, self.failed)
