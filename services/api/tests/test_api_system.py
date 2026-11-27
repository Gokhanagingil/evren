"""Sistem durumu (U8.1, ADR-0032): bileşen durumları servis nabızlarından ve veritabanından türetilir; Kafka'nın durumu
onu kullanan servislerin bildirdiğidir; yetki yalnızca "platform" okuma izniyle. Nabızlar test içinde sahip rolüyle
yazılır (servislerin yazdığı gibi) ve sonda silinir. Veriler sentetiktir."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from evren_api import create_app
from evren_api.config import Settings
from evren_core.opensearch_client import OpenSearchError

from .conftest import make_client

COMPONENTS = {"api", "database", "migrations", "kafka", "opensearch", "svc_requests", "svc_ingest", "svc_indexer",
              "svc_connector", "svc_gateway", "outbox", "queues", "sources"}


@pytest.fixture
def beats() -> Iterator[Any]:
    created: list[str] = []

    def make(kind: str, *, seen_ago: int = 0, stopped: bool = False, details: dict[str, Any] | None = None,
             processed: int = 0, failed: int = 0) -> str:
        wid = f"systest-{kind}-{uuid.uuid4().hex[:8]}"
        with psycopg.connect(autocommit=True) as c:
            c.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed,
                                                               failed, poll_seconds, kind, details)
                         VALUES (%s, now() - interval '1 hour', now() - %s * interval '1 second',
                                 CASE WHEN %s THEN now() END, %s, %s, 10, %s, %s)""",
                      (wid, seen_ago, stopped, processed, failed, kind, Jsonb(details or {})))
        created.append(wid)
        return wid

    yield make
    with psycopg.connect(autocommit=True) as c:
        c.execute("DELETE FROM ingest.worker_heartbeat WHERE worker_id = ANY(%s)", (created,))


@pytest.fixture(scope="module")
def operator() -> Iterator[TestClient]:
    with make_client(("evren_operator",), "operator") as c:
        yield c


def status(c: TestClient) -> dict[str, Any]:
    r = c.get("/api/v1/system/status")
    assert r.status_code == 200, r.text
    doc = r.json()
    return {**doc, "by_id": {x["id"]: x for x in doc["components"]}}


def test_every_component_is_reported_and_states_follow_heartbeats(operator, beats):
    beats("requests", details={"outbox_publisher": True}, processed=12)
    beats("ingest", details={"kafka": {"ok": True, "lag": 5, "partitions": 3}}, processed=900)
    beats("indexer", details={"kafka": {"ok": True, "lag": 25_000, "partitions": 3}})
    beats("gateway", details={"kafka": {"ok": False}})
    beats("connector", seen_ago=600)  # nabız 10 dakikadır yok, düzgün kapanmadı
    doc = status(operator)
    by = doc["by_id"]
    assert set(by) == COMPONENTS
    assert sum(doc["counts"].values()) == len(COMPONENTS)
    assert by["database"]["state"] == "ok" and by["database"]["summary"].startswith("PostgreSQL ")
    assert by["svc_requests"]["state"] == "ok"
    assert {"label": "Olay yayıncısı", "value": "açık"} in by["svc_requests"]["items"]
    assert by["svc_ingest"]["state"] == "ok" and {"label": "Kafka gecikmesi", "value": "5 kayıt"} in by["svc_ingest"]["items"]
    assert by["svc_indexer"]["state"] == "warn" and "25.000" in by["svc_indexer"]["note"]
    assert by["svc_gateway"]["state"] == "bad" and by["svc_gateway"]["note"] == "Kafka'ya ulaşılamıyor."
    assert by["kafka"]["state"] == "bad" and "Alım geçidi" in by["kafka"]["summary"]
    assert by["svc_connector"]["state"] == "bad" and by["svc_connector"]["summary"].startswith("Yanıt vermiyor")
    assert by["opensearch"]["state"] == "off"  # bu testte OpenSearch yapılandırılmamış: PostgreSQL yedeği
    assert doc["overall"] == "bad" and doc["overall_label"] == "Sorun var"
    assert all(c["state_label"] and c["summary"] for c in doc["components"])


def test_kafka_ok_when_users_report_it_and_off_without_them(operator, beats):
    doc = status(operator)
    if not any(x["state"] == "ok" for x in [doc["by_id"]["svc_gateway"], doc["by_id"]["svc_ingest"],
                                            doc["by_id"]["svc_indexer"]]):
        assert doc["by_id"]["kafka"]["state"] == "off"
    beats("gateway", details={"kafka": {"ok": True}})
    beats("ingest", details={"kafka": {"ok": True, "lag": 0}})
    kafka = status(operator)["by_id"]["kafka"]
    assert kafka["state"] == "ok" and kafka["summary"] == "2 servis bağlı"


def test_stopped_service_and_old_unpublished_events(operator, beats):
    beats("indexer", stopped=True, seen_ago=30)
    beats("requests", details={"outbox_publisher": True})
    with psycopg.connect(autocommit=True) as c:
        (old,), = c.execute("""INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload, created_at)
                               VALUES ('cmdb_ci', 'systest', 'cmdb.ci.changed.v1', '{}', now() - interval '2 hours')
                               RETURNING id""").fetchall()
    try:
        by = status(operator)["by_id"]
        assert by["svc_indexer"]["state"] == "warn" and by["svc_indexer"]["summary"].startswith("Durduruldu")
        assert by["outbox"]["state"] == "bad" and "olay bekliyor" in by["outbox"]["summary"]
    finally:
        with psycopg.connect(autocommit=True) as c:
            c.execute("DELETE FROM events.outbox WHERE id = %s", (old,))


def test_pending_requests_without_a_running_reconciler_are_flagged(operator):
    with psycopg.connect(autocommit=True) as c:
        (wid,), = c.execute("SELECT sys_id FROM cmdb.cmdb_ci LIMIT 1").fetchall()
        (req,), = c.execute("""INSERT INTO ingest.manual_edit (ci_sys_id, attributes, requested_by)
                               VALUES (%s, '{"comments": "systest"}', 'systest') RETURNING sys_id""",
                            (wid,)).fetchall()
    try:
        by = status(operator)["by_id"]
        if by["svc_requests"]["state"] != "ok":
            assert by["queues"]["state"] == "warn" and "uzlaştırıcı çalışmıyor" in by["queues"]["summary"]
    finally:
        with psycopg.connect(autocommit=True) as c:
            c.execute("DELETE FROM ingest.manual_edit WHERE sys_id = %s", (req,))


def test_migration_ledger_detects_pending_and_changed_files(operator):
    with psycopg.connect(autocommit=True) as c:
        if c.execute("SELECT count(*) FROM meta.schema_migration").fetchone()[0]:
            pytest.skip("Göç defteri dolu (göç aracıyla kurulmuş veritabanı): bu senaryo boş defter ister")
        assert status(operator)["by_id"]["migrations"]["state"] == "warn"  # boş defter: izlenmiyor
        c.execute("INSERT INTO meta.schema_migration (version, sha256) VALUES ('0001_core', repeat('0', 64))")
        try:
            mig = status(operator)["by_id"]["migrations"]
            assert mig["state"] == "bad" and "0001_core" in mig["summary"]  # uygulanmış dosya değişmiş
        finally:
            c.execute("DELETE FROM meta.schema_migration WHERE version = '0001_core'")


class FakeSearch:
    def __init__(self, status: str = "green", docs: int | None = None, fail: bool = False) -> None:
        self.status, self.docs, self.fail = status, docs, fail

    def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        if self.fail:
            raise OpenSearchError("test", 503)
        if path == "/_cluster/health":
            return {"status": self.status, "number_of_nodes": 1}
        return {"count": self.docs}


def test_opensearch_health_and_index_drift():
    env = {"EVREN_AUTH_MODE": "dev", "EVREN_DEV_ROLES": "evren_operator", "EVREN_DB_POOL_SIZE": "2"}
    with psycopg.connect(autocommit=True) as c:
        cis = c.execute("SELECT count(*) FROM cmdb.cmdb_ci").fetchone()[0]
    cases = [(FakeSearch(docs=cis), "ok"), (FakeSearch("yellow", cis), "warn"), (FakeSearch(docs=cis - 500), "warn"),
             (FakeSearch("red", cis), "bad"), (FakeSearch(fail=True), "bad")]
    for fake, expected in cases:
        with TestClient(create_app(Settings.from_env(env), search_client=fake)) as c:
            os_ = status(c)["by_id"]["opensearch"]
            assert os_["state"] == expected, (fake.status, fake.docs, os_)
    assert "geride" in os_["summary"] or os_["summary"] == "Ulaşılamıyor"


def test_only_platform_readers_see_system_status():
    for roles, code in ((("evren_reader",), 403), (("evren_cmdb_editor",), 403), (("evren_data_steward",), 200),
                        (("evren_auditor",), 200), (("evren_operator",), 200)):
        with make_client(roles, "yetki-testi") as c:
            assert c.get("/api/v1/system/status").status_code == code, roles
            assert c.get("/api/v1/me").json()["can"]["system"] is (code == 200)
