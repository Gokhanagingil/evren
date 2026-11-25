"""Alım tüketicisi (U2, ADR-0017) — ``evren.ingest.record.v1`` → kimlik çözümleme ve uzlaştırma → golden record.

Teslim garantisi: **en az bir kez** okunur, etkisi **bir kez** olur.

* Ofset yalnızca kaydın veritabanı işlemi tamamlanınca (veya kayıt DLQ'ya yazılınca) saklanır. Çökme veya yeniden
  dengeleme sonrası aynı kayıt yeniden gelebilir; içerik özeti aynı olduğundan "değişmedi" yolundan geçer ve çalışma
  sayaçları kaynak kayıtlarından hesaplandığı için iki kez sayılmaz.
* Sözleşmeye uymayan (zehirli) mesaj kuyruğu kilitlemez: değiştirilmeden ``evren.dlq.v1``'e, nedeni başlıkta
  yazılır ve geçilir. Beklenmeyen hata da hata kimliğiyle DLQ'ya gider (ayrıntı günlükte).
* Geçici veritabanı veya DLQ hatası: mesaja geri sarılır, üstel beklemeyle yeniden denenir (kayıp yok).
* Tüketici kaydın çalışmasını doğrular: çalışma yoksa veya başka kaynağa aitse DLQ (geçidi atlatma girişimi).
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg

from evren_core.kafka_client import Publisher, PublishError
from evren_core.messaging import TOPIC_DLQ, TOPIC_RECORD, MessageError, decode_record, dlq_headers
from evren_core.sources import SourceRegistryError

from .store import ReconcilerStore

__all__ = ["IngestConsumer", "TransientError"]

log = logging.getLogger("evren.reconciler.consumer")


class TransientError(RuntimeError):
    """Geçici sorun (veritabanı veya DLQ): mesaj işlenmedi, yeniden denenecek."""


@dataclass
class Counters:
    processed: int = 0
    dead_lettered: int = 0
    retried: int = 0


class IngestConsumer:
    """Kafka tüketicisi + DLQ üreticisi + uzlaştırıcı deposu. Kafka nesneleri dışarıdan verilir (testte taklit)."""

    def __init__(self, store: ReconcilerStore, consumer: Any, dlq: Publisher, *, batch: int = 200,
                 poll_timeout: float = 1.0, topic_partition: Callable[[str, int, int], Any] | None = None) -> None:
        self.store = store
        self.consumer = consumer
        self.dlq = dlq
        self.batch = batch
        self.poll_timeout = poll_timeout
        self.counters = Counters()
        self._run_sources: dict[uuid.UUID, str] = {}
        self._tp = topic_partition or _confluent_tp

    def subscribe(self) -> None:
        self.consumer.subscribe([TOPIC_RECORD])

    # ------------------------------------------------------------------ tek mesaj
    def handle(self, msg: Any) -> str:
        """``processed`` | ``dlq``; geçici sorunda ``TransientError``."""
        try:
            env = decode_record(msg.value(), msg.key())
        except MessageError as exc:
            return self._dead_letter(msg, str(exc))
        rec = env.data
        try:
            owner = self._run_source(rec.run_id)
        except psycopg.OperationalError as exc:
            raise TransientError(type(exc).__name__) from None
        if owner is None:
            return self._dead_letter(msg, "Çalışma bulunamadı (geçit dışından yazım?)")
        if owner != rec.data_source:
            return self._dead_letter(msg, "Çalışma başka veri kaynağına ait")
        try:
            self.store.apply(rec)
        except psycopg.OperationalError as exc:
            raise TransientError(type(exc).__name__) from None
        except (psycopg.Error, SourceRegistryError, ValueError, KeyError, TypeError) as exc:
            error_id = uuid.uuid4().hex[:12]
            log.error("Kayıt uygulanamadı (hata kimliği %s): %s %s:%s", error_id, type(exc).__name__,
                      rec.data_source, rec.source_key, exc_info=exc)
            return self._dead_letter(msg, f"Uygulanamadı: {type(exc).__name__}", error_id=error_id)
        self.counters.processed += 1
        return "processed"

    def _run_source(self, run_id: uuid.UUID) -> str | None:
        if run_id not in self._run_sources:
            with self.store.conn.cursor() as cur:
                cur.execute("SELECT data_source FROM ingest.source_run WHERE run_id = %s", (run_id,))
                row = cur.fetchone()
            if row is None:
                return None
            if len(self._run_sources) > 10_000:
                self._run_sources.clear()
            self._run_sources[run_id] = row[0]
        return self._run_sources[run_id]

    def _dead_letter(self, msg: Any, reason: str, *, error_id: str | None = None) -> str:
        headers = dlq_headers(reason, origin_topic=msg.topic(), partition=msg.partition(), offset=msg.offset(),
                              error_id=error_id)
        try:
            self.dlq.publish([(TOPIC_DLQ, msg.key() or b"", msg.value() or b"", headers)], timeout=15)
        except PublishError:
            raise TransientError("DLQ yazılamadı") from None
        log.warning("DLQ: %s (bölüm %s ofset %s)", reason, msg.partition(), msg.offset())
        self.counters.dead_lettered += 1
        return "dlq"

    # ------------------------------------------------------------------ döngü
    def poll_once(self) -> int:
        """Bir toplu okuma: her mesaj işlenince ofseti saklanır. Geçici hatada mesaja geri sarılır."""
        msgs = self.consumer.consume(num_messages=self.batch, timeout=self.poll_timeout)
        handled = 0
        for msg in msgs:
            err = msg.error()
            if err is not None:
                log.warning("Kafka mesaj hatası: %s", err)
                continue
            try:
                self.handle(msg)
            except TransientError:
                self.counters.retried += 1
                self.consumer.seek(self._tp(msg.topic(), msg.partition(), msg.offset()))
                raise
            self.consumer.store_offsets(message=msg)
            handled += 1
        return handled

    def serve(self, stop: threading.Event, *, on_cycle: Callable[[int], None] | None = None) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                n = self.poll_once()
                backoff = 1.0
                if on_cycle:
                    on_cycle(n)
            except TransientError as exc:
                log.warning("geçici hata (%s); %.0f sn sonra yeniden denenecek", exc, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
                if on_cycle:
                    on_cycle(0)


def _confluent_tp(topic: str, partition: int, offset: int) -> Any:
    from confluent_kafka import TopicPartition

    return TopicPartition(topic, partition, offset)

