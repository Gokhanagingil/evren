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

Olay omurgası (U2, ADR-0017):

* Her turda alım geçidinden kapanışı istenen çalışmalar kapatılır (``finalize_ready_runs``).
* Her turda yetkisiz değişiklik gözlemleri değerlendirilir (``evaluate_config_changes``, ADR-0025).
* Her turda süresi dolan sahiplik onayları geçersiz kılınır (``expire_attestations``, ADR-0027).
* Eskime önerileri ``EVREN_STALENESS_SECONDS`` aralığıyla değerlendirilir (``evaluate_staleness``, ADR-0028).
* ``EVREN_KAFKA_BOOTSTRAP`` tanımlıysa outbox olayları Kafka'ya yayınlanır (outbox'a yazım çalışanı uyandırır).
* Kaynak kayıtlarının Kafka'dan alımı ayrı süreçtir: ``python -m evren_reconciler.worker --ingest`` (``IngestWorker``);
  bölüm sayısı kadar örnek paralel çalışabilir.
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

from evren_core.kafka_client import KafkaPublisher, PublishError
from evren_core.messaging import TOPIC_RECORD, KafkaConfigError, KafkaSettings
from evren_core.metadata import Metadata, load_metadata
from evren_core.sources import SourceRegistryError

from .consumer import IngestConsumer, TransientError
from .outbox import OutboxPublisher
from .store import ReconcilerStore

__all__ = ["IngestWorker", "Worker", "WorkerConfig", "main"]

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
    health_file: str | None = None
    kafka: KafkaSettings | None = None
    consumer_group: str = "evren-reconciler"
    staleness_seconds: int = 600  # eskime değerlendirme aralığı (ADR-0028)

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
        staleness = int(env.get("EVREN_STALENESS_SECONDS", "600"))
        if not 0 <= staleness <= 86400:
            raise ConfigError("EVREN_STALENESS_SECONDS 0–86400 aralığında olmalı")
        kafka = None
        if env.get("EVREN_KAFKA_BOOTSTRAP"):
            try:
                kafka = KafkaSettings.from_env(env)
            except KafkaConfigError as exc:
                raise ConfigError(str(exc)) from None
        values: dict[str, Any] = {
            "conninfo": conninfo, "role": env.get("EVREN_DB_ROLE", "evren_app_reconciler"), "poll_seconds": poll,
            "metadata_dir": Path(env.get("EVREN_METADATA_DIR", str(REPO_ROOT / "metadata"))),
            "health_file": env.get("EVREN_WORKER_HEALTH_FILE") or None, "kafka": kafka,
            "consumer_group": env.get("EVREN_KAFKA_CONSUMER_GROUP", "evren-reconciler"), "staleness_seconds": staleness}
        values.update(overrides)
        return cls(**values)


class Worker:
    kind = "requests"

    def __init__(self, config: WorkerConfig, metadata: Metadata | None = None,
                 connect: Callable[[], psycopg.Connection[Any]] | None = None,
                 outbox: OutboxPublisher | None = None) -> None:
        self.config = config
        self.md = metadata or load_metadata(config.metadata_dir)
        self._connect = connect or (lambda: psycopg.connect(config.conninfo, autocommit=True))
        self.stop = threading.Event()
        self.processed = 0
        self.failed = 0
        self.started_at: float | None = None
        self._staleness_at: float | None = None  # son eskime değerlendirmesi (monotonic)
        if outbox is None and config.kafka is not None and self.kind == "requests":
            outbox = OutboxPublisher(KafkaPublisher(config.kafka, f"evren-outbox-{config.worker_id}"), self.md)
        self.outbox = outbox

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
        totals = {"create": 0, "manual": 0, "relation": 0, "attest": 0, "retire": 0, "decision": 0, "split": 0,
                  "failed": 0, "runs_closed": 0, "published": 0, "config_changes": 0, "attestations_expired": 0,
                  "staleness": 0}
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
        totals["runs_closed"] = len(store.finalize_ready_runs())
        # Yetkisiz değişiklik: bekleme süresi dolan gözlemler ve açık anomalilerin yeniden denenmesi (ADR-0025)
        evaluated = store.evaluate_config_changes()
        totals["config_changes"] = sum(evaluated.values())
        if evaluated["unauthorized"] or evaluated["late_authorized"]:
            log.info("değişiklik denetimi: onaylı=%d yetkisiz=%d bastırılan=%d sonradan onaylı=%d",
                     evaluated["authorized"], evaluated["unauthorized"], evaluated["suppressed"],
                     evaluated["late_authorized"])
        # Sahiplik onayı: süresi dolan veya onaylayanı artık sorumlu olmayan onaylar (ADR-0027)
        expired = store.expire_attestations()
        totals["attestations_expired"] = sum(expired.values())
        if totals["attestations_expired"]:
            log.info("sahiplik onayı geçersiz kılındı: süre=%d sahip değişti=%d", expired["expired"],
                     expired["owner_changed"])
        # Eskime (ADR-0028): öneriler dönemsel açılır/ilerler/kapanır; 0 = her tur
        mono = time.monotonic()
        if self._staleness_at is None or mono - self._staleness_at >= self.config.staleness_seconds:
            self._staleness_at = mono
            stale = store.evaluate_staleness()
            totals["staleness"] = sum(stale.values())
            if stale["opened"] or stale["advanced"] or stale["reappeared"]:
                log.info("eskime: açılan=%d ilerleyen=%d kapanan=%d bastırılan=%d yeniden görülen emekli=%d",
                         stale["opened"], stale["advanced"], stale["recovered"], stale["suppressed"],
                         stale["reappeared"])
        if self.outbox is not None:
            try:
                totals["published"] = self.outbox.publish_pending(conn)
            except PublishError as exc:
                log.warning("outbox yayınlanamadı (%s); olaylar beklemede, sonraki turda yeniden denenecek", exc)
        if self.config.health_file:
            # Konteyner sağlık denetimi: dosyanın değişme zamanı son turu gösterir (tools/../deploy/evren.py).
            Path(self.config.health_file).touch()
        handled = (totals["create"] + totals["manual"] + totals["relation"] + totals["attest"] + totals["retire"]
                   + totals["decision"] + totals["split"])
        self.processed += handled
        self.failed += totals["failed"]
        self._heartbeat(conn, failed_now=totals["failed"] > 0)
        if handled:
            log.info("istekler uygulandı: oluşturma=%d elle=%d ilişki=%d onay=%d karar=%d ayırma=%d başarısız=%d",
                     totals["create"], totals["manual"], totals["relation"], totals["attest"], totals["decision"],
                     totals["split"], totals["failed"])
        return totals

    def _heartbeat(self, conn: psycopg.Connection[Any], *, failed_now: bool = False, stopped: bool = False) -> None:
        conn.execute(
            """INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed, failed,
                                                   last_error_at, poll_seconds, kind)
               VALUES (%(id)s, to_timestamp(%(started)s), now(), CASE WHEN %(stopped)s THEN now() END, %(processed)s,
                       %(failed)s, CASE WHEN %(failed_now)s THEN now() END, %(poll)s, %(kind)s)
               ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(),
                 stopped_at = CASE WHEN %(stopped)s THEN now() END,
                 processed = EXCLUDED.processed, failed = EXCLUDED.failed,
                 last_error_at = COALESCE(EXCLUDED.last_error_at, ingest.worker_heartbeat.last_error_at),
                 poll_seconds = EXCLUDED.poll_seconds""",
            {"id": self.config.worker_id, "started": self.started_at or time.time(), "stopped": stopped,
             "processed": self.processed, "failed": self.failed, "failed_now": failed_now,
             "poll": self.config.poll_seconds, "kind": self.kind})

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


class IngestWorker(Worker):
    """Kafka alım tüketicisi süreci: ``IngestConsumer`` + bağlantı yönetimi + nabız (``kind='ingest'``)."""

    kind = "ingest"

    def __init__(self, config: WorkerConfig, metadata: Metadata | None = None,
                 connect: Callable[[], psycopg.Connection[Any]] | None = None,
                 consumer_factory: Callable[[], Any] | None = None, dlq: Any | None = None) -> None:
        super().__init__(config, metadata, connect)
        if config.kafka is None and (consumer_factory is None or dlq is None):
            raise ConfigError("Alım tüketicisi için EVREN_KAFKA_BOOTSTRAP gerekli")
        self._consumer_factory = consumer_factory or self._kafka_consumer
        self._dlq = dlq or KafkaPublisher(config.kafka, f"evren-dlq-{config.worker_id}")  # type: ignore[arg-type]

    def _kafka_consumer(self) -> Any:
        from confluent_kafka import Consumer

        assert self.config.kafka is not None
        return Consumer(self.config.kafka.consumer_config(f"evren-ingest-{self.config.worker_id}",
                                                          self.config.consumer_group))

    def serve(self) -> None:
        self.started_at = time.time()
        consumer = self._consumer_factory()
        consumer.subscribe([TOPIC_RECORD])
        backoff = 1.0
        try:
            while not self.stop.is_set():
                try:
                    with self._open() as conn:
                        log.info("alım tüketicisi hazır: kimlik=%s grup=%s", self.config.worker_id,
                                 self.config.consumer_group)
                        ingest = IngestConsumer(ReconcilerStore(conn, self.md), consumer, self._dlq)
                        backoff = self._loop(conn, ingest)
                        self._heartbeat(conn, stopped=self.stop.is_set())
                except psycopg.OperationalError as exc:
                    log.warning("veritabanı bağlantısı yok (%s); %.0f sn sonra yeniden denenecek", type(exc).__name__,
                                backoff)
                    self.stop.wait(backoff)
                    backoff = min(backoff * 2, 60.0)
        finally:
            consumer.close()  # saklanan ofsetler işlenir; grup yeniden dengelenir
        log.info("alım tüketicisi durdu: işlenen=%d DLQ=%d", self.processed, self.failed)

    def _loop(self, conn: psycopg.Connection[Any], ingest: IngestConsumer) -> float:
        backoff = 1.0
        last_beat = 0.0
        while not self.stop.is_set():
            try:
                ingest.poll_once()
                backoff = 1.0
            except TransientError as exc:
                if conn.broken:
                    raise psycopg.OperationalError("bağlantı koptu") from None
                log.warning("geçici hata (%s); %.0f sn sonra yeniden denenecek", exc, backoff)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
            self.processed, self.failed = ingest.counters.processed, ingest.counters.dead_lettered
            if time.monotonic() - last_beat >= 10:
                self._heartbeat(conn, failed_now=False)
                if self.config.health_file:
                    Path(self.config.health_file).touch()
                last_beat = time.monotonic()
        return backoff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evren_reconciler.worker", description="Evren uzlaştırıcı çalışanı")
    parser.add_argument("--once", action="store_true", help="bekleyen istekleri bir tur uygula ve çık")
    parser.add_argument("--ingest", action="store_true",
                        help="Kafka alım tüketicisi olarak çalış (evren.ingest.record.v1; EVREN_KAFKA_* gerekir)")
    parser.add_argument("--poll", type=int, default=None, help="tarama aralığı (sn); varsayılan ortamdan veya 30")
    parser.add_argument("--rereconcile", metavar="KAYNAK", default=None,
                        help="veri kaynağının yaşam döngüsü/rol sırası değişikliği dağıtıldıktan sonra bağlı CI'ları "
                             "yeniden uzlaştır ve çık (ADR-0009, ADR-0015)"),
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("EVREN_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = WorkerConfig.from_env(**({"poll_seconds": args.poll} if args.poll else {}))
    except (ConfigError, ValueError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    if args.ingest:
        try:
            ingest = IngestWorker(config)
        except ConfigError as exc:
            print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
            return 2
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: ingest.stop.set())
        ingest.serve()
        return 0
    worker = Worker(config)
    if args.rereconcile:
        with worker._open() as conn:
            try:
                totals = ReconcilerStore(conn, worker.md).rereconcile_source(args.rereconcile)
            except SourceRegistryError:
                print("Tanımsız veri kaynağı.", file=sys.stderr)
                return 2
        print(totals)
        return 0
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
