"""Kafka tüketici gecikmesi (sistem durumu, U8.1): nabız ayrıntısı hata yükseltmez, konumu olmayan bölümde alt su
işareti esas alınır. confluent_kafka gerekmez (taklit tüketici)."""
from __future__ import annotations

from dataclasses import dataclass

from evren_core.kafka_client import consumer_lag


@dataclass
class TP:
    partition: int
    offset: int


class FakeConsumer:
    def __init__(self, positions: dict[int, int], marks: dict[int, tuple[int, int]], fail: bool = False) -> None:
        self.positions, self.marks, self.fail = positions, marks, fail

    def assignment(self) -> list[TP]:
        return [TP(p, -1001) for p in self.positions]

    def position(self, tps: list[TP]) -> list[TP]:
        return [TP(tp.partition, self.positions[tp.partition]) for tp in tps]

    def get_watermark_offsets(self, tp: TP, timeout: float, cached: bool) -> tuple[int, int]:
        if self.fail:
            raise RuntimeError("KafkaException: broker yok")
        return self.marks[tp.partition]


def test_lag_is_high_watermark_minus_position_and_unpositioned_partition_uses_low_mark():
    c = FakeConsumer({0: 90, 1: -1001, 2: 500}, {0: (0, 100), 1: (40, 70), 2: (0, 500)})
    assert consumer_lag(c) == {"ok": True, "partitions": 3, "lag": 10 + 30 + 0}


def test_no_assignment_is_zero_and_errors_are_reported_without_detail():
    assert consumer_lag(FakeConsumer({}, {})) == {"ok": True, "partitions": 0, "lag": 0}
    assert consumer_lag(FakeConsumer({0: 1}, {0: (0, 2)}, fail=True)) == {"ok": False, "error": "RuntimeError"}
    assert consumer_lag(object()) == {"ok": False, "error": "AttributeError"}
