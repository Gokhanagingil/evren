"""Outbox yayıncısı (U2, ADR-0017) — ``events.outbox`` → Kafka (``evren.cmdb.ci.changed.v1``, ``evren.cmdb.activity.v1``).

Olay, golden record değişikliğiyle AYNI veritabanı işleminde outbox'a yazılır (uzlaştırıcı, API); yayıncı onu Kafka'ya
taşır. Böylece "değişti ama olay yok" veya "olay var ama değişiklik geri alındı" durumu olmaz.

* Satırlar ``FOR UPDATE SKIP LOCKED`` ile alınır; Kafka onaylayınca ``published_at`` yazılır ve işlem tamamlanır.
  Kafka'ya ulaşılamazsa işlem geri alınır, satırlar yayınlanmamış kalır ve sonraki turda yeniden denenir.
* En az bir kez: çökme anında bir olay iki kez yayınlanabilir; tüketiciler olay kimliğiyle (``outbox-<id>``) ayıklar.
* Hassasiyeti ``confidential``/``restricted`` olan alanların değerleri olaya konmaz (``evren_core.messaging``).
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from evren_core.kafka_client import Publisher
from evren_core.messaging import outbox_message
from evren_core.metadata import Metadata
from evren_core.search import hidden_fields

__all__ = ["OutboxPublisher", "hidden_fields"]

log = logging.getLogger("evren.reconciler.outbox")


class OutboxPublisher:
    def __init__(self, publisher: Publisher, metadata: Metadata, *, batch: int = 500, timeout: float = 30) -> None:
        self.publisher = publisher
        self.hidden = hidden_fields(metadata)
        self.batch = batch
        self.timeout = timeout
        self.published = 0

    def publish_pending(self, conn: psycopg.Connection[Any]) -> int:
        """Yayınlanmamış olayları sırayla yayınlar; Kafka hatasında ``PublishError`` (satırlar beklemede kalır)."""
        total = 0
        while True:
            with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""SELECT id, aggregate_type, aggregate_id, event_type, payload, created_at
                                 FROM events.outbox WHERE published_at IS NULL
                                ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""", (self.batch,))
                rows = cur.fetchall()
                if not rows:
                    break
                messages = []
                for row in rows:
                    topic, key, value = outbox_message(row, self.hidden)
                    messages.append((topic, key, value, [("evren-event-type", str(row["event_type"]).encode())]))
                self.publisher.publish(messages, timeout=self.timeout)
                cur.execute("UPDATE events.outbox SET published_at = now() WHERE id = ANY(%s)",
                            ([r["id"] for r in rows],))
            total += len(rows)
            if len(rows) < self.batch:
                break
        if total:
            self.published += total
            log.info("outbox: %d olay yayınlandı", total)
        return total
