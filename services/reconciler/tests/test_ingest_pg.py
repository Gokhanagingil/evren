"""Olay omurgası (U2, ADR-0017): alım tüketicisi, çalışma kapanışı, outbox yayını — gerçek PostgreSQL, taklit Kafka.

Kafka nesneleri taklittir (aracı gerektirmez); gerçek aracıyla uçtan uca akış ``tools/dev_stack.py smoke`` ve CI
"Yığın" işinde sınanır.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from test_store_pg import fetch

from evren_core.envelope import IngestEnvelope, IngestRecord
from evren_core.kafka_client import PublishError
from evren_core.messaging import TOPIC_ACTIVITY, TOPIC_CI_CHANGED, TOPIC_DLQ, TOPIC_RECORD, encode_record, record_key
from evren_reconciler import ReconcilerStore
from evren_reconciler.consumer import IngestConsumer, TransientError
from evren_reconciler.outbox import OutboxPublisher

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


class Msg:
    def __init__(self, value: bytes | None, key: bytes | None = None, offset: int = 0, partition: int = 0) -> None:
        self._v, self._k, self._o, self._p = value, key, offset, partition

    def value(self) -> bytes | None:
        return self._v

    def key(self) -> bytes | None:
        return self._k

    def offset(self) -> int:
        return self._o

    def partition(self) -> int:
        return self._p

    def topic(self) -> str:
        return TOPIC_RECORD

    def error(self) -> None:
        return None


class FakeConsumer:
    def __init__(self, msgs: list[Msg]) -> None:
        self.queue, self.stored, self.seeks = list(msgs), [], []

    def consume(self, num_messages: int, timeout: float) -> list[Msg]:
        out, self.queue = self.queue[:num_messages], self.queue[num_messages:]
        return out

    def store_offsets(self, message: Msg) -> None:
        self.stored.append(message.offset())

    def seek(self, tp: Any) -> None:
        self.seeks.append(tp)


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes, Any]] = []
        self.fail = False

    def publish(self, messages: Any, timeout: float) -> None:
        if self.fail:
            raise PublishError("test")
        self.sent.extend(messages)

    def ready(self, timeout: float = 2.0) -> bool:
        return True

    def close(self) -> None:
        pass


def open_run(conn, data_source: str, *, started: datetime = NOW, opened_by: str | None = "test-baglayici",
             mode: str = "snapshot") -> uuid.UUID:
    rid = uuid.uuid4()
    conn.execute("INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, opened_by) "
                 "VALUES (%s, %s, %s, %s, %s)", (rid, data_source, mode, started, opened_by))
    return rid


def message(data_source: str, run_id: uuid.UUID, key: str, offset: int = 0, **attrs: Any) -> Msg:
    rec = IngestRecord(data_source=data_source, run_id=run_id, source_key=key, class_hint="cmdb_ci_linux_server",
                       observed_at=NOW, attributes=attrs or {"name": key})
    env = IngestEnvelope(id=str(uuid.uuid4()), source="urn:test", time=NOW, data=rec)
    return Msg(encode_record(env), record_key(data_source, key), offset)


def consumer_for(store: ReconcilerStore, msgs: list[Msg]) -> tuple[IngestConsumer, FakeConsumer, FakePublisher]:
    fc, dlq = FakeConsumer(msgs), FakePublisher()
    return IngestConsumer(store, fc, dlq, topic_partition=lambda t, p, o: (t, p, o)), fc, dlq


# ------------------------------------------------------------------ tüketici
def test_records_are_applied_and_offsets_stored_after_commit(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = open_run(as_reconciler, "BMC")
    msgs = [message("BMC", rid, "ing-1", 0, name="ing-1", serial_number="CZING0001"),
            message("BMC", rid, "ing-2", 1, name="ing-2", serial_number="CZING0002")]
    ic, fc, dlq = consumer_for(store, msgs)
    assert ic.poll_once() == 2 and fc.stored == [0, 1] and not dlq.sent
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_link WHERE source_key IN ('ing-1', 'ing-2') "
                                "AND active")[0][0] == 2
    assert ic.counters.processed == 2


def test_redelivery_has_no_second_effect_and_is_counted_once(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = open_run(as_reconciler, "BMC")
    m = message("BMC", rid, "ing-dup", 0, name="ing-dup", serial_number="CZING0009")
    ic, _fc, _dlq = consumer_for(store, [m, m, m])
    ic.poll_once()
    rows = fetch(as_reconciler, "SELECT count(*) FROM ingest.source_record WHERE source_key = 'ing-dup'")[0][0]
    with as_reconciler.cursor() as cur:
        assert store.run_counts(cur, rid, "BMC") == (1, 1, 0)
    assert rows == 1


def test_poison_messages_go_to_dlq_without_blocking(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    zbx = open_run(as_reconciler, "Zabbix")
    good = message("Zabbix", zbx, "ok-1", 3)
    other_run = message("BMC", zbx, "x-1", 2)                 # BMC kaydı Zabbix çalışmasına yazmaya çalışıyor
    no_run = message("Zabbix", uuid.uuid4(), "x-2", 1)        # geçit dışından: çalışma yok
    msgs = [Msg(b"{bozuk", b"k", 0), no_run, other_run, good]
    ic, fc, dlq = consumer_for(store, msgs)
    assert ic.poll_once() == 4 and fc.stored == [0, 1, 2, 3]
    reasons = [dict(h)["evren-dlq-reason"].decode() for _t, _k, _v, h in dlq.sent]
    assert [t for t, *_ in dlq.sent] == [TOPIC_DLQ] * 3
    assert reasons[0].startswith("Zarf doğrulanamadı") and "Çalışma bulunamadı" in reasons[1]
    assert "başka veri kaynağına" in reasons[2]
    assert dlq.sent[0][2] == b"{bozuk" and dict(dlq.sent[1][3])["evren-origin-offset"] == b"1"
    assert ic.counters.dead_lettered == 3 and ic.counters.processed == 1


def test_transient_database_error_rewinds_and_stores_nothing(as_reconciler, metadata, monkeypatch):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = open_run(as_reconciler, "Zabbix")
    ic, fc, dlq = consumer_for(store, [message("Zabbix", rid, "tr-1", 7)])

    def deadlock(*_a: Any, **_k: Any) -> None:
        raise psycopg.errors.DeadlockDetected("geçici (test)")

    monkeypatch.setattr(store, "apply", deadlock)
    with pytest.raises(TransientError):
        ic.poll_once()
    assert fc.stored == [] and fc.seeks == [(TOPIC_RECORD, 0, 7)] and not dlq.sent


def test_unexpected_error_dead_letters_with_error_id(as_reconciler, metadata, monkeypatch):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = open_run(as_reconciler, "Zabbix")
    ic, fc, dlq = consumer_for(store, [message("Zabbix", rid, "ue-1", 4)])
    monkeypatch.setattr(store, "apply", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("test")))
    ic.poll_once()
    headers = dict(dlq.sent[0][3])
    assert headers["evren-dlq-reason"] == b"Uygulanamad\xc4\xb1: ValueError" and len(headers["evren-error-id"]) == 12
    assert fc.stored == [4]


def test_dlq_outage_is_transient_not_data_loss(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ic, fc, dlq = consumer_for(store, [Msg(b"{bozuk", b"k", 9)])
    dlq.fail = True
    with pytest.raises(TransientError):
        ic.poll_once()
    assert fc.stored == [] and fc.seeks == [(TOPIC_RECORD, 0, 9)]


# ------------------------------------------------------------------ çalışma kapanışı
def _apply(store: ReconcilerStore, ds: str, rid: uuid.UUID, keys: list[str]) -> None:
    for k in keys:
        store.apply(IngestRecord(data_source=ds, run_id=rid, source_key=k, class_hint="cmdb_ci_linux_server",
                                 observed_at=NOW, attributes={"name": k},
                                 lookup={"mac_addresses": [f"00:50:56:aa:00:{len(k):02x}"]}))


def _close(conn, rid: uuid.UUID, reported: int, completed: bool = True, at: datetime = NOW) -> None:
    conn.execute("UPDATE ingest.source_run SET reported_count = %s, reported_completed = %s, "
                 "reported_error_count = 0, close_requested_at = %s WHERE run_id = %s", (reported, completed, at, rid))


def test_run_closes_when_all_reported_records_are_processed(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = open_run(as_reconciler, "Zabbix")
    _apply(store, "Zabbix", rid, ["fin-a"])
    _close(as_reconciler, rid, reported=2)
    assert [c.run_id for c in store.finalize_ready_runs()] == []          # 1/2 işlendi, süre dolmadı
    _apply(store, "Zabbix", rid, ["fin-bb"])
    [closure] = [c for c in store.finalize_ready_runs() if c.run_id == rid]
    assert closure.reason == "completed" and closure.processed == 2
    row = fetch(as_reconciler, "SELECT completed, record_count, new_count, finished_at IS NOT NULL FROM "
                               "ingest.source_run WHERE run_id = %s", rid)[0]
    assert row == (True, 2, 2, True)


def test_run_times_out_as_incomplete_and_breaker_blocks_destruction(as_reconciler, metadata):
    clock = {"now": NOW}
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: clock["now"])
    rid = open_run(as_reconciler, "Zabbix")
    _apply(store, "Zabbix", rid, ["to-a"])
    _close(as_reconciler, rid, reported=5)
    clock["now"] = NOW + timedelta(minutes=16)
    [closure] = [c for c in store.finalize_ready_runs() if c.run_id == rid]
    assert closure.reason == "timed_out" and closure.verdict.state.value == "open"
    completed, notes = fetch(as_reconciler, "SELECT completed, notes FROM ingest.source_run WHERE run_id = %s", rid)[0]
    assert completed is False and "Bildirilen 5 kaydın 1 tanesi işlendi" in notes


def test_small_delta_run_is_not_quarantined_by_snapshot_history(as_reconciler, metadata):
    """Delta çalışması tam çalışma geçmişiyle kıyaslanmaz (yanlış karantina ve anomali üretmemeli)."""
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    for k in range(5):
        as_reconciler.execute("INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, finished_at, "
                              "completed, record_count, breaker_state) VALUES (%s, 'Zabbix', 'snapshot', %s, %s, true, "
                              "4000, 'ok')", (uuid.uuid4(), NOW - timedelta(days=k + 1), NOW - timedelta(days=k + 1)))
    rid = open_run(as_reconciler, "Zabbix", mode="delta")
    _apply(store, "Zabbix", rid, ["delta-a"])
    _close(as_reconciler, rid, reported=1)
    [closure] = [c for c in store.finalize_ready_runs() if c.run_id == rid]
    assert closure.reason == "completed" and closure.verdict.state.value == "ok"
    assert fetch(as_reconciler, "SELECT count(*) FROM insight.anomaly WHERE details->>'run_id' = %s", str(rid))[0][0] == 0


def test_abandoned_run_is_closed_as_incomplete(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = open_run(as_reconciler, "Zabbix", started=NOW - timedelta(days=2))
    internal = open_run(as_reconciler, "Zabbix", started=NOW - timedelta(days=2), opened_by=None)  # iç yükleme
    closed = {c.run_id: c for c in store.finalize_ready_runs()}
    assert closed[rid].reason == "abandoned" and internal not in closed
    with pytest.raises(ValueError, match="zaten kapatılmış"):
        store.finish_run(rid)


# ------------------------------------------------------------------ outbox
def _outbox(conn, ci: str, event_type: str, payload: dict[str, Any]) -> int:
    return conn.execute("INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload) "
                        "VALUES ('cmdb_ci', %s, %s, %s) RETURNING id", (ci, event_type, json.dumps(payload))
                        ).fetchone()[0]


def test_outbox_publishes_in_order_marks_rows_and_redacts(as_reconciler, metadata):
    ci = "f" * 32
    a = _outbox(as_reconciler, ci, "cmdb.ci.changed.v1",
                {"sys_id": ci, "changes": [{"attribute": "comments", "old": "eski-gizli", "new": "yeni-gizli"}]})
    b = _outbox(as_reconciler, ci, "evren.ci.edit.requested.v1", {"id": "x"})
    pub = FakePublisher()
    outbox = OutboxPublisher(pub, metadata, batch=2)
    outbox.hidden = frozenset({"comments"})
    assert outbox.publish_pending(as_reconciler) >= 2
    mine = [(t, json.loads(v)) for t, k, v, _h in pub.sent if k == ci.encode()]
    assert [(t, e["id"]) for t, e in mine] == [(TOPIC_CI_CHANGED, f"outbox-{a}"), (TOPIC_ACTIVITY, f"outbox-{b}")]
    assert mine[0][1]["data"]["changes"][0]["redacted"] is True
    assert all(b"gizli" not in v for _t, k, v, _h in pub.sent if k == ci.encode())
    assert fetch(as_reconciler, "SELECT count(*) FROM events.outbox WHERE id IN (%s, %s) AND published_at IS NULL",
                 a, b)[0][0] == 0


def test_outbox_rows_stay_pending_when_kafka_is_down(as_reconciler, metadata):
    ci = "e" * 32
    a = _outbox(as_reconciler, ci, "cmdb.ci.changed.v1", {"sys_id": ci, "changes": []})
    pub = FakePublisher()
    pub.fail = True
    with pytest.raises(PublishError):
        OutboxPublisher(pub, metadata).publish_pending(as_reconciler)
    assert fetch(as_reconciler, "SELECT published_at IS NULL FROM events.outbox WHERE id = %s", a)[0][0] is True
