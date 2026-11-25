"""Kafka G/Ç adaptörü (U2, ADR-0017) — çekirdekte Kafka'ya dokunan TEK modül; geri kalanı saf kalır.

Mesajlar ancak aracı (broker) onaylayınca "teslim edildi" sayılır (idempotent üretici, ``acks=all``). Alım geçidi,
outbox yayıncısı ve DLQ aynı üreticiyi kullanır. ``confluent_kafka`` yalnızca gerçek üretici kurulurken yüklenir;
testler ``Publisher`` arayüzünü taklit eder.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any, Protocol

from .messaging import KafkaSettings

__all__ = ["KafkaPublisher", "Message", "PublishError", "Publisher"]

Message = tuple[str, bytes, bytes, list[tuple[str, bytes]] | None]  # konu, anahtar, değer, başlıklar


class PublishError(RuntimeError):
    """Mesajların tamamı teslim edilemedi; istemci aynı toplu gönderimi yeniden dener (tekrar zararsızdır)."""


class Publisher(Protocol):
    def publish(self, messages: Sequence[Message], timeout: float) -> None: ...
    def ready(self, timeout: float = 2.0) -> bool: ...
    def close(self) -> None: ...


class KafkaPublisher:
    def __init__(self, settings: KafkaSettings, client_id: str) -> None:
        from confluent_kafka import Producer  # yalnızca gerçek çalışmada yüklenir

        self._producer: Any = Producer(settings.producer_config(client_id))

    def publish(self, messages: Sequence[Message], timeout: float) -> None:
        state = {"pending": len(messages), "errors": 0}
        lock = threading.Lock()

        def delivered(err: Any, _msg: Any) -> None:
            with lock:
                state["pending"] -= 1
                if err is not None:
                    state["errors"] += 1

        deadline = time.monotonic() + timeout
        for topic, key, value, headers in messages:
            while True:
                try:
                    self._producer.produce(topic, value=value, key=key, headers=headers, on_delivery=delivered)
                    break
                except BufferError:  # yerel kuyruk dolu: teslimleri işle ve yeniden dene
                    if time.monotonic() > deadline:
                        raise PublishError("Kafka üretici kuyruğu dolu") from None
                    self._producer.poll(0.1)
        while state["pending"] > 0 and time.monotonic() < deadline:
            self._producer.poll(0.05)
        if state["pending"] > 0 or state["errors"]:
            raise PublishError(f"Teslim edilemeyen mesaj: {state['pending'] + state['errors']}")

    def ready(self, timeout: float = 2.0) -> bool:
        try:
            meta = self._producer.list_topics(timeout=timeout)
        except Exception:  # aracıya ulaşılamıyor; ayrıntı sızdırılmaz
            return False
        return bool(meta.brokers)

    def close(self) -> None:
        self._producer.flush(10)
