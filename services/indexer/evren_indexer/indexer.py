"""Arama dizinleyicisi (U3, ADR-0018) — CI olayları (Kafka) → PostgreSQL'den güncel CI → OpenSearch.

    python -m evren_indexer                 # sürekli: açılışta dizini doğrula/kur, sonra CI olaylarını izle
    python -m evren_indexer --reindex       # tam yeniden dizinleme (yeni sürümlü dizin + atomik takma ad) ve çık

* Olayın içeriği değil, CI'ın veritabanındaki **güncel** hâli dizinlenir: olaylar sırasız veya tekrar gelse de dizin
  doğru kalır (en az bir kez teslim yeterli). Ofset yalnızca OpenSearch yazımı başarılı olunca saklanır.
* Dizin kayıt sistemi değildir; kaybolursa ``--reindex`` ile PostgreSQL'den yeniden üretilir. Eşleme sürümü
  (``MAPPING_VERSION``) değişince açılışta kendiliğinden yeniden dizinlenir.
* Gizli hassasiyetteki alanlar dizine girmez. Rol ``evren_app_indexer``: okur, yalnızca nabzını yazar (göç 0013).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from evren_core.kafka_client import consumer_lag
from evren_core.messaging import TOPIC_CI_CHANGED, KafkaConfigError, KafkaSettings
from evren_core.metadata import Metadata, load_metadata
from evren_core.opensearch_client import OpenSearch, OpenSearchConfigError, OpenSearchError, OpenSearchSettings
from evren_core.search import ALIAS, INDEX_BODY, ci_document, current_version, hidden_fields, index_name

__all__ = ["CiIndexer", "IndexerConfig", "IndexerWorker", "main"]

log = logging.getLogger("evren.indexer")
REPO_ROOT = Path(__file__).resolve().parents[3]
_SYSID = re.compile(r"^[0-9a-f]{32}$")
ROW_SQL = """SELECT ci.sys_id, ci.name, ci.sys_class_name, ci.ip_address, ci.fqdn, ci.mac_address, ci.serial_number,
                    ci.asset_tag, ci.class_attributes->>'os' AS os, ci.used_for, ci.install_status,
                    ci.operational_status, ci.sys_updated_on AS updated_at,
                    ARRAY(SELECT DISTINCT l.data_source FROM ingest.identity_link l
                           WHERE l.ci_sys_id = ci.sys_id AND l.active ORDER BY 1) AS sources
               FROM cmdb.cmdb_ci ci"""


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class IndexerConfig:
    conninfo: str = field(repr=False)
    opensearch: OpenSearchSettings
    kafka: KafkaSettings | None = None
    role: str = "evren_app_indexer"
    consumer_group: str = "evren-indexer"
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    metadata_dir: Path = REPO_ROOT / "metadata"
    health_file: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> IndexerConfig:
        env = os.environ if env is None else env
        conninfo = env.get("EVREN_DATABASE_URL", "")
        if path := env.get("EVREN_DATABASE_URL_FILE"):
            try:
                conninfo = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                raise ConfigError("EVREN_DATABASE_URL_FILE okunamadı") from None
        try:
            search = OpenSearchSettings.from_env(env)
            kafka = KafkaSettings.from_env(env) if env.get("EVREN_KAFKA_BOOTSTRAP") else None
        except (OpenSearchConfigError, KafkaConfigError) as exc:
            raise ConfigError(str(exc)) from None
        return cls(conninfo=conninfo, opensearch=search, kafka=kafka, role=env.get("EVREN_DB_ROLE", "evren_app_indexer"),
                   consumer_group=env.get("EVREN_KAFKA_CONSUMER_GROUP", "evren-indexer"),
                   metadata_dir=Path(env.get("EVREN_METADATA_DIR") or REPO_ROOT / "metadata"),
                   health_file=env.get("EVREN_WORKER_HEALTH_FILE") or None)


class CiIndexer:
    """Veritabanı bağlantısı + OpenSearch istemcisi ile dizin işlemleri (Kafka'dan bağımsız; testte doğrudan)."""

    def __init__(self, conn: psycopg.Connection[Any], md: Metadata, client: OpenSearch, *, alias: str = ALIAS,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.conn, self.client, self.alias = conn, client, alias
        self.labels = {c["name"]: c.get("label_tr") or c["name"] for c in md.classes}
        self.hidden = hidden_fields(md)
        self.clock = clock or (lambda: datetime.now(UTC))

    def _rows(self, where: sql.Composable, params: Iterable[Any]) -> list[dict[str, Any]]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL(ROW_SQL + " WHERE {}").format(where), tuple(params))
            return cur.fetchall()

    def _docs(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = self.clock()
        return [ci_document(r, class_label=self.labels.get(r["sys_class_name"], r["sys_class_name"]), hidden=self.hidden,
                            indexed_at=now) for r in rows]

    def index_ids(self, sys_ids: Iterable[str], *, target: str | None = None, refresh: bool = False) -> int:
        ids = sorted({i for i in sys_ids if _SYSID.fullmatch(i)})
        if not ids:
            return 0
        rows = self._rows(sql.SQL("ci.sys_id = ANY(%s)"), (ids,))
        return self.client.bulk_index(target or self.alias, self._docs(rows), refresh=refresh)

    def reindex(self, *, batch: int = 1000) -> tuple[str, int]:
        """Yeni sürümlü dizini baştan doldurur, takma adı atomik taşır, eski dizinleri siler.

        Tarama sırasında değişen CI'lar, takma ad taşındıktan sonra bir kez daha yazılır (kayıp aralığı kapanır).
        """
        started = self.clock()
        name = index_name(started, alias=self.alias)
        self.client.create_index(name, INDEX_BODY)
        total, last = 0, ""
        while True:
            rows = self._rows(sql.SQL("ci.sys_id > %s ORDER BY ci.sys_id LIMIT %s"), (last, batch))
            if not rows:
                break
            total += self.client.bulk_index(name, self._docs(rows))
            last = rows[-1]["sys_id"]
        self.client.request("POST", f"/{name}/_refresh")
        old = self.client.point_alias(self.alias, name)
        late = self._rows(sql.SQL("ci.sys_updated_on >= %s"), (started - timedelta(seconds=5),))
        if late:
            self.client.bulk_index(self.alias, self._docs(late))
        for o in old:
            self.client.delete_index(o)
        log.info("yeniden dizinlendi: %s (%d CI, sonradan güncellenen %d, silinen eski dizin %d)", name, total, len(late),
                 len(old))
        return name, total

    def ensure(self) -> str | None:
        """Takma ad yoksa veya eski eşleme sürümündeyse yeniden dizinler; dizin adını döndürür."""
        if current_version(self.client.alias_targets(self.alias), alias=self.alias):
            return None
        return self.reindex()[0]


class IndexerWorker:
    """Kafka tüketicisi (``evren.cmdb.ci.changed.v1``) + bağlantı yönetimi + nabız (``kind='indexer'``)."""

    def __init__(self, config: IndexerConfig, metadata: Metadata | None = None, *,
                 connect: Callable[[], psycopg.Connection[Any]] | None = None,
                 consumer_factory: Callable[[], Any] | None = None, client: OpenSearch | None = None,
                 topic_partition: Callable[[str, int, int], Any] | None = None) -> None:
        if config.kafka is None and consumer_factory is None:
            raise ConfigError("Dizinleyici için EVREN_KAFKA_BOOTSTRAP gerekli")
        self.config = config
        self.md = metadata or load_metadata(config.metadata_dir)
        self.client = client or OpenSearch(config.opensearch)
        self._connect = connect or (lambda: psycopg.connect(config.conninfo, autocommit=True))
        self._consumer_factory = consumer_factory or self._kafka_consumer
        self._tp = topic_partition or _confluent_tp
        self.stop = threading.Event()
        self.indexed = 0
        self.failed = 0
        self.started_at = time.time()

    def _kafka_consumer(self) -> Any:
        from confluent_kafka import Consumer

        assert self.config.kafka is not None
        return Consumer(self.config.kafka.consumer_config(f"evren-indexer-{self.config.worker_id}",
                                                          self.config.consumer_group))

    def open(self) -> psycopg.Connection[Any]:
        conn = self._connect()
        conn.autocommit = True
        if self.config.role:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.config.role)))
        conn.execute("SET application_name = 'evren-indexer'")
        return conn

    def _heartbeat(self, conn: psycopg.Connection[Any], *, stopped: bool = False, consumer: Any = None) -> None:
        # Ayrıntı (sistem durumu, U8.1): Kafka tüketici gecikmesi; OpenSearch'e yazabiliyor olmak nabzın kendisidir
        details = {"kafka": consumer_lag(consumer)} if consumer is not None else {}
        conn.execute(
            """INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed, failed,
                                                   poll_seconds, kind, details)
               VALUES (%(id)s, to_timestamp(%(started)s), now(), CASE WHEN %(stopped)s THEN now() END, %(done)s,
                       %(failed)s, 10, 'indexer', %(details)s)
               ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(), stopped_at = CASE WHEN %(stopped)s THEN now() END,
                 processed = EXCLUDED.processed, failed = EXCLUDED.failed, details = EXCLUDED.details""",
            {"id": self.config.worker_id, "started": self.started_at, "stopped": stopped, "done": self.indexed,
             "failed": self.failed, "details": Jsonb(details)})
        if self.config.health_file:
            Path(self.config.health_file).touch()

    def poll_once(self, consumer: Any, indexer: CiIndexer) -> int:
        """Bir toplu okuma: CI'lar tek toplu yazımla dizinlenir; başarısızsa bölümler ilk mesaja geri sarılır."""
        msgs = [m for m in consumer.consume(num_messages=500, timeout=1.0) if m.error() is None]
        if not msgs:
            return 0
        ids = {(m.key() or b"").decode(errors="replace") for m in msgs}
        try:
            n = indexer.index_ids(ids)
        except (OpenSearchError, psycopg.OperationalError):
            first: dict[tuple[str, int], int] = {}
            for m in msgs:
                k = (m.topic(), m.partition())
                first[k] = min(first.get(k, m.offset()), m.offset())
            for (topic, part), off in first.items():
                consumer.seek(self._tp(topic, part, off))
            self.failed += 1
            raise
        for m in msgs:
            consumer.store_offsets(message=m)
        self.indexed += n
        return n

    def serve(self) -> None:
        consumer = None
        backoff = 1.0
        try:
            while not self.stop.is_set():
                try:
                    with self.open() as conn:
                        indexer = CiIndexer(conn, self.md, self.client)
                        made = indexer.ensure()
                        log.info("dizinleyici hazır: takma ad=%s%s", ALIAS, f" (yeni dizin {made})" if made else "")
                        if consumer is None:
                            consumer = self._consumer_factory()
                            consumer.subscribe([TOPIC_CI_CHANGED])
                        backoff, last_beat = 1.0, 0.0
                        while not self.stop.is_set():
                            self.poll_once(consumer, indexer)
                            if time.monotonic() - last_beat >= 10:
                                self._heartbeat(conn, consumer=consumer)
                                last_beat = time.monotonic()
                        self._heartbeat(conn, stopped=True)
                except (OpenSearchError, psycopg.OperationalError) as exc:
                    log.warning("geçici hata (%s); %.0f sn sonra yeniden denenecek", type(exc).__name__, backoff)
                    self.stop.wait(backoff)
                    backoff = min(backoff * 2, 60.0)
        finally:
            if consumer is not None:
                consumer.close()
        log.info("dizinleyici durdu: dizinlenen=%d", self.indexed)


def _confluent_tp(topic: str, partition: int, offset: int) -> Any:
    from confluent_kafka import TopicPartition

    return TopicPartition(topic, partition, offset)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evren_indexer", description="Evren arama dizinleyicisi (OpenSearch)")
    ap.add_argument("--reindex", action="store_true", help="tam yeniden dizinleme yap ve çık")
    args = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("EVREN_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = IndexerConfig.from_env()
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    if args.reindex:
        md = load_metadata(config.metadata_dir)
        try:
            with psycopg.connect(config.conninfo, autocommit=True) as conn:
                conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(config.role)))
                name, total = CiIndexer(conn, md, OpenSearch(config.opensearch)).reindex()
        except (OpenSearchError, psycopg.Error) as exc:
            print(f"Yeniden dizinleme başarısız: {type(exc).__name__}", file=sys.stderr)
            return 1
        print(f"Yeniden dizinlendi: {name} ({total} CI)")
        return 0
    try:
        worker = IndexerWorker(config)
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop.set())
    worker.serve()
    return 0
