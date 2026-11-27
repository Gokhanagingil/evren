"""Alım geçidi (U2, ADR-0017): kimlik, kaynak yetkisi, zarf, çalışma yaşam döngüsü, sınırlar, Kafka teslimi."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import db, issue, record_env, run_env

from evren_core.messaging import TOPIC_RECORD, TOPIC_RUN, record_key


def open_run(client, headers, ds="Zabbix"):
    rid = uuid.uuid4()
    r = client.post("/ingest/v1/runs", json=run_env(ds, rid), headers=headers)
    assert r.status_code == 201, r.text
    return rid


# ------------------------------------------------------------------ kimlik
@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer kisa", "Bearer evr_" + "A" * 43])
def test_requests_without_valid_token_are_rejected(gateway, header):
    client, pub = gateway
    r = client.post("/ingest/v1/runs", json=run_env("Zabbix", uuid.uuid4()),
                    headers={"Authorization": header} if header else {})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.json()["error"]["code"] in {"kimlik_gerekli", "kimlik_gecersiz"} and not pub.sent


@pytest.mark.parametrize("kw", [{"revoked": True}, {"expires": timedelta(seconds=-1)}])
def test_revoked_or_expired_credentials_are_rejected(gateway, kw):
    client, _pub = gateway
    _name, headers = issue(**kw)
    assert client.post("/ingest/v1/runs", json=run_env("Zabbix", uuid.uuid4()), headers=headers).status_code == 401


def test_credential_writes_only_its_own_source_and_denial_is_audited(gateway):
    client, pub = gateway
    name, headers = issue("Zabbix")
    r = client.post("/ingest/v1/runs", json=run_env("vCenter", uuid.uuid4()), headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "kaynak_yetkisi_yok"
    assert db("SELECT decision, resource FROM audit.security_event WHERE principal = %s ORDER BY id DESC LIMIT 1",
              f"connector:{name}") == [("deny", "data_source:vCenter")]
    assert not pub.sent


def test_retired_source_cannot_send(retired_gateway):
    _name, headers = issue("Defender")
    r = retired_gateway.post("/ingest/v1/runs", json=run_env("Defender", uuid.uuid4()), headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "kaynak_emekli"


# ------------------------------------------------------------------ çalışma ve kayıtlar
def test_run_open_is_idempotent_and_run_id_cannot_be_hijacked(gateway):
    client, pub = gateway
    name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    again = client.post("/ingest/v1/runs", json=run_env("Zabbix", rid), headers=headers)
    assert again.status_code == 200 and again.json()["replayed"] is True
    _other, other_headers = issue("Zabbix")
    assert client.post("/ingest/v1/runs", json=run_env("Zabbix", rid), headers=other_headers).status_code == 409
    assert db("SELECT data_source, opened_by, finished_at FROM ingest.source_run WHERE run_id = %s", rid) == [
        ("Zabbix", name, None)]
    assert pub.sent[0][0] == TOPIC_RUN and pub.sent[0][1] == b"Zabbix"


def test_records_are_delivered_with_object_key_and_counted(gateway):
    client, pub = gateway
    name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    pub.sent.clear()
    batch = {"records": [record_env("Zabbix", rid, "h-1"), record_env("Zabbix", rid, "h-2")]}
    r = client.post(f"/ingest/v1/runs/{rid}/records", json=batch, headers=headers)
    assert r.status_code == 202 and r.json()["accepted"] == 2
    assert [(t, k) for t, k, _v, _h in pub.sent] == [(TOPIC_RECORD, record_key("Zabbix", "h-1")),
                                                     (TOPIC_RECORD, record_key("Zabbix", "h-2"))]
    assert dict(pub.sent[0][3])["evren-connector"] == name.encode()
    assert json.loads(pub.sent[0][2])["data"]["source_key"] == "h-1"
    assert db("SELECT accepted_count FROM ingest.source_run WHERE run_id = %s", rid) == [(2,)]


@pytest.mark.parametrize(("mutate", "field", "kind"), [
    (lambda d, rid: d.update(data_source="vCenter"), "data.data_source", "kaynak_uyumsuz"),
    (lambda d, rid: d.update(run_id=str(uuid.uuid4())), "data.run_id", "calisma_uyumsuz"),
    (lambda d, rid: d.update(observed_at=(datetime.now(UTC) + timedelta(hours=2)).isoformat()), "data.observed_at",
     "gelecek_tarih"),
])
def test_batch_is_all_or_nothing(gateway, mutate, field, kind):
    client, pub = gateway
    _name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    pub.sent.clear()
    bad = record_env("Zabbix", rid, "h-2")
    mutate(bad["data"], rid)
    r = client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "h-1"), bad]},
                    headers=headers)
    assert r.status_code == 400 and r.json()["error"]["details"] == [{"index": 1, "field": field, "type": kind}]
    assert not pub.sent and db("SELECT accepted_count FROM ingest.source_run WHERE run_id = %s", rid) == [(0,)]


def test_schema_errors_report_location_without_echoing_input(gateway):
    client, _pub = gateway
    _name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    rec = record_env("Zabbix", rid, "h-1")
    rec["data"]["gizli_alan"] = "SIR-DEGERI-123"
    r = client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [rec]}, headers=headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "gecersiz_zarf"
    assert "SIR-DEGERI-123" not in r.text and "gizli_alan" in r.text


def test_size_limits(gateway):
    client, _pub = gateway
    _name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    six = {"records": [record_env("Zabbix", rid, f"h-{i}") for i in range(6)]}
    assert client.post(f"/ingest/v1/runs/{rid}/records", json=six, headers=headers).json()["error"]["code"] == \
        "toplu_buyuk"
    big = {"records": [record_env("Zabbix", rid, "h-1", comments="x" * 70_000)]}
    assert client.post(f"/ingest/v1/runs/{rid}/records", json=big, headers=headers).status_code == 413

    def chunks():
        yield b'{"records": []}'

    r = client.post(f"/ingest/v1/runs/{rid}/records", content=chunks(), headers=headers)
    assert r.status_code == 411


def test_rate_limit_per_credential(gateway):
    client, _pub = gateway
    _name, headers = issue("Zabbix", per_minute=3)
    rid = open_run(client, headers)
    ok = client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "a"),
                                                                           record_env("Zabbix", rid, "b")]},
                     headers=headers)
    assert ok.status_code == 202
    r = client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "c"),
                                                                          record_env("Zabbix", rid, "d")]},
                    headers=headers)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_kafka_failure_is_503_and_nothing_is_counted(gateway):
    client, pub = gateway
    _name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    pub.fail = True
    r = client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "a")]},
                    headers=headers)
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert db("SELECT accepted_count FROM ingest.source_run WHERE run_id = %s", rid) == [(0,)]


def test_completion_checks_counts_and_closes_the_run(gateway):
    client, _pub = gateway
    name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "a")]}, headers=headers)
    over = client.post(f"/ingest/v1/runs/{rid}/complete", json=run_env("Zabbix", rid, started=False, count=5),
                       headers=headers)
    assert over.status_code == 409 and over.json()["error"]["code"] == "sayi_uyumsuz"
    done = client.post(f"/ingest/v1/runs/{rid}/complete", json=run_env("Zabbix", rid, started=False, count=1),
                       headers=headers)
    assert done.status_code == 202 and done.json()["state"] == "closing" and done.json()["reported"] == 1
    again = client.post(f"/ingest/v1/runs/{rid}/complete", json=run_env("Zabbix", rid, started=False, count=1),
                        headers=headers)
    assert again.status_code == 200 and again.json()["replayed"] is True
    changed = client.post(f"/ingest/v1/runs/{rid}/complete",
                          json=run_env("Zabbix", rid, started=False, count=1, completed=False), headers=headers)
    assert changed.status_code == 409
    late = client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "b")]},
                       headers=headers)
    assert late.status_code == 409 and late.json()["error"]["code"] == "calisma_kapali"
    assert db("SELECT action FROM audit.security_event WHERE principal = %s AND decision = 'allow' ORDER BY id",
              f"connector:{name}") == [("ingest_run_open",), ("ingest_run_close",)]


def test_other_sources_runs_are_invisible(gateway):
    client, _pub = gateway
    _a, zabbix = issue("Zabbix")
    _b, vcenter = issue("vCenter")
    rid = open_run(client, zabbix)
    assert client.get(f"/ingest/v1/runs/{rid}", headers=vcenter).status_code == 404
    assert client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("vCenter", rid, "x")]},
                       headers=vcenter).status_code == 404
    status = client.get(f"/ingest/v1/runs/{rid}", headers=zabbix).json()
    assert status["state"] == "open" and status["state_label"] == "Açık"


def test_readiness_reflects_kafka_and_headers_are_hardened(gateway):
    client, pub = gateway
    assert client.get("/readyz").status_code == 200
    pub.is_ready = False
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["kafka"] is False
    h = client.get("/healthz").headers
    assert h["cache-control"] == "no-store" and h["x-content-type-options"] == "nosniff"
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


# ------------------------------------------------------------------ nabız (sistem durumu, U8.1, ADR-0032)
def test_heartbeat_reports_kafka_reachability_and_accepted_records(gateway):
    """Geçit kendi rolüyle (evren_app_ingest) nabız yazar: kabul edilen kayıt, Kafka teslim hatası, Kafka bağlantısı."""
    client, pub = gateway
    wid = client.app.state.worker_id
    _name, headers = issue("Zabbix")
    rid = open_run(client, headers)
    assert client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "hb-1")]},
                       headers=headers).status_code == 202
    pub.fail = True
    assert client.post(f"/ingest/v1/runs/{rid}/records", json={"records": [record_env("Zabbix", rid, "hb-2")]},
                       headers=headers).status_code == 503
    pub.is_ready = False
    client.app.state.beat()
    assert db("SELECT kind, processed, failed, details, stopped_at IS NULL FROM ingest.worker_heartbeat "
              "WHERE worker_id = %s", wid) == [("gateway", 1, 1, {"kafka": {"ok": False}}, True)]
    pub.is_ready = True
    client.app.state.beat(stopped=True)
    assert db("SELECT details, stopped_at IS NOT NULL FROM ingest.worker_heartbeat WHERE worker_id = %s",
              wid) == [({"kafka": {"ok": True}}, True)]
