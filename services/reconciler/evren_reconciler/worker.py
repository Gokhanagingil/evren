"""Uzlaştırıcı çalışanı — insan isteklerini (kuyruk kararı, ayırma, elle düzenleme) sürekli uygular (F0-10).

Çalıştırma::

    EVREN_DATABASE_URL_FILE=/run/secrets/evren_db python -m evren_reconciler.worker
    python -m evren_reconciler.worker --once      # bir tur uygula ve çık (cron / bakım)

Davranış:

* PostgreSQL bildirimi (``LISTEN evren_work``, göç 0009) yeni istekte çalışanı hemen uyandırır; bildirim kaçsa
  bile ``--poll`` saniyede bir tablolar taranır. Bildirim yük taşımaz, iş her zaman tablolardan okunur.
* Her istek kendi işleminde uygulanır; beklenmeyen hata yalnızca o isteği reddeder (``ReconcilerStore._drain``).
* Bağlantı koparsa üstel bekleme ile yeniden bağlanır. SIGTERM/SIGINT: elindeki turu bitirir, nabza durduğunu
  yazar ve çıkar.
* Nabız (``ingest.worker_heartbeat``) her turda güncellenir; arayüz "uzlaştırıcı çalışıyor mu" bilgisini buradan alır.
* Bağlantı adresi yalnızca ortam değişkeninden veya dosyadan okunur ve hiçbir zaman günlüğe yazılmaz.

Kafka tüketicisi (kaynak kayıtlarının alımı) hedef ortam kararından sonra bu döngüye eklenecek.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from evren_core.metadata import Metadata, load_metadata

from .store import ReconcilerStore

__all__ = ["Worker", "WorkerConfig", "main"]

log = logging.getLogger("evren.reconciler.worker")
CHANNEL = "evren_work"
REPO_ROOT = Path(__file__).resolve().parents[3]


class ConfigError(ValueError):
    """Yapılandırma hatası (ileti sır içermez)."""


@dataclass(frozen=True)
class WorkerConfig:
    conninfo: str = field(repr=False)  # sır içerebilir: repr/günlükte görünmez
    role: str = "evren_app_reconciler"
    poll_seconds: int = 30
    batch: int = 100
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    metadata_dir: Path = REPO_ROOT / "metadata"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> WorkerConfig:
        env = os.environ if env is None else env
        conninfo = env.get("EVREN_DATABASE_URL", "")
        path = env.get("EVREN_DATABASE_URL_FILE")
        if path:
            try:
                conninfo = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                raise ConfigError("EVREN_DATABASE_URL_FILE okunamadı") from None
        poll = int(env.get("EVREN_WORKER_POLL_SECONDS", "30"))
        if not 1 <= poll <= 3600:
            raise ConfigError("EVREN_WORKER_POLL_SECONDS 1–3600 aralığında olmalı")
        values: dict[str, Any] = {
            "conninfo": conninfo, "role": env.get("EVREN_DB_ROLE", "evren_app_reconciler"), "poll_seconds": poll,
            "metadata_dir": Path(env.get("EVREN_METADATA_DIR", str(REPO_ROOT / "metadata")))}
        values.update(overrides)
        return cls(**values)


class Worker:
    def __init__(self, config: WorkerConfig, metadata: Metadata | None = None,
                 connect: Callable[[], psycopg.Connection[Any]] | None = None) -> None:
        self.config = config
        self.md = metadata or load_metadata(config.metadata_dir)
        self._connect = connect or (lambda: psycopg.connect(config.conninfo, autocommit=True))
        self.stop = threading.Event()
        self.processed = 0
        self.failed = 0
        self.started_at: float | None = None

    # ------------------------------------------------------------------ bağlantı
    def _open(self) -> psycopg.Connection[Any]:
        conn = self._connect()
        conn.autocommit = True
        if self.config.role:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.config.role)))
        conn.execute("SET application_name = 'evren-reconciler'")
        return conn

    # ------------------------------------------------------------------ iş
    def run_once(self, conn: psycopg.Connection[Any]) -> dict[str, int]:
        """Bekleyen tüm istekleri uygular (her tür ``batch`` kadar, iş kalmayana dek)."""
        store = ReconcilerStore(conn, self.md)
        totals = {"manual": 0, "decision": 0, "split": 0, "failed": 0}
        while not self.stop.is_set():
            results = store.apply_requests(self.config.batch)
            done = 0
            for kind, items in results.items():
                totals[kind] += len(items)
                totals["failed"] += sum(1 for r in items if r.outcome.endswith("_failed"))
                done += len(items)
            if not any(len(items) == self.config.batch for items in results.values()):
                break
            if done == 0:  # pragma: no cover - savunma
                break
        handled = totals["manual"] + totals["decision"] + totals["split"]
        self.processed += handled
        self.failed += totals["failed"]
        self._heartbeat(conn, failed_now=totals["failed"] > 0)
        if handled:
            log.info("istekler uygulandı: elle=%d karar=%d ayırma=%d başarısız=%d",
                     totals["manual"], totals["decision"], totals["split"], totals["failed"])
        return totals

    def _heartbeat(self, conn: psycopg.Connection[Any], *, failed_now: bool = False, stopped: bool = False) -> None:
        conn.execute(
            """INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed, failed,
                                                   last_error_at, poll_seconds)
               VALUES (%(id)s, to_timestamp(%(started)s), now(), CASE WHEN %(stopped)s THEN now() END, %(processed)s,
                       %(failed)s, CASE WHEN %(failed_now)s THEN now() END, %(poll)s)
               ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(),
                 stopped_at = CASE WHEN %(stopped)s THEN now() END,
                 processed = EXCLUDED.processed, failed = EXCLUDED.failed,
                 last_error_at = COALESCE(EXCLUDED.last_error_at, ingest.worker_heartbeat.last_error_at),
                 poll_seconds = EXCLUDED.poll_seconds""",
            {"id": self.config.worker_id, "started": self.started_at or time.time(), "stopped": stopped,
             "processed": self.processed, "failed": self.failed, "failed_now": failed_now,
             "poll": self.config.poll_seconds})

    # ------------------------------------------------------------------ döngü
    def serve(self) -> None:
        """Durdurulana kadar çalışır. Bildirim gelince hemen, gelmezse ``poll_seconds``'ta bir tur."""
        self.started_at = time.time()
        backoff = 1.0
        while not self.stop.is_set():
            try:
                with self._open() as conn:
                    conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(CHANNEL)))
                    log.info("uzlaştırıcı çalışanı hazır: kimlik=%s tarama=%ss", self.config.worker_id,
                             self.config.poll_seconds)
                    backoff = 1.0
                    self.run_once(conn)
                    while not self.stop.is_set():
                        self._wait(conn)  # bildirim gelince erken döner
                        if not self.stop.is_set():
                            self.run_once(conn)  # istekler + nabız
                    self._heartbeat(conn, stopped=True)
            except psycopg.OperationalError as exc:
                # İleti bağlantı ayrıntısı içerebilir; yalnızca hata sınıfı günlüğe yazılır.
                log.warning("veritabanı bağlantısı yok (%s); %.0f sn sonra yeniden denenecek", type(exc).__name__,
                            backoff)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        log.info("uzlaştırıcı çalışanı durdu: işlenen=%d başarısız=%d", self.processed, self.failed)

    def _wait(self, conn: psycopg.Connection[Any]) -> bool:
        """``poll_seconds`` boyunca bildirim bekler; durdurma isteğini en geç 1 sn'de fark eder."""
        deadline = time.monotonic() + self.config.poll_seconds
        while not self.stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if any(True for _ in conn.notifies(timeout=min(1.0, remaining), stop_after=1)):
                return True
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evren_reconciler.worker", description="Evren uzlaştırıcı çalışanı")
    parser.add_argument("--once", action="store_true", help="bekleyen istekleri bir tur uygula ve çık")
    parser.add_argument("--poll", type=int, default=None, help="tarama aralığı (sn); varsayılan ortamdan veya 30")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("EVREN_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = WorkerConfig.from_env(**({"poll_seconds": args.poll} if args.poll else {}))
    except (ConfigError, ValueError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    worker = Worker(config)
    if args.once:
        worker.started_at = time.time()
        with worker._open() as conn:
            totals = worker.run_once(conn)
            worker._heartbeat(conn, stopped=True)
        print(totals)
        return 0
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop.set())
    worker.serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
