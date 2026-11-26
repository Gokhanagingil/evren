"""Bağlayıcı yönetimi uçları (U5.3b, ADR-0021): şimdi çalıştır, iptal, kimlik verme/iptal.

* Uçlar ``data_source/connector_admin`` ister (Veri sorumlusu); Denetçi ve okuyucu göremez/yapamaz.
* İstek yalnızca çalıştırıcının bildirdiği, canlı örnek için kabul edilir; örnek başına tek etkin istek.
* Belirteç yanıtta bir kez görünür (``no-store``), tekrar güvenliği kaydına ve veritabanına düz metin girmez.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from evren_core.credentials import token_sha256

from .conftest import CSRF, make_client

RUNNER = "apitest-runner:1"
INSTANCE = "apitest-zbx"


@pytest.fixture(scope="module")
def steward():
    with make_client(("evren_reader", "evren_data_steward"), "veri-sorumlusu") as c:
        yield c


@pytest.fixture(scope="module")
def auditor():
    with make_client(("evren_reader", "evren_auditor"), "denetci") as c:
        yield c


@pytest.fixture
def instance():
    """Çalıştırıcının bildirdiği örnek + canlı nabız (çalıştırıcı servisinin yaptığı, sahibi rolüyle)."""
    with psycopg.connect(autocommit=True) as c:
        c.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind)
                     VALUES (%s, now(), now(), 15, 'connector')
                     ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(), stopped_at = NULL""", (RUNNER,))
        c.execute("""INSERT INTO ingest.connector_instance (name, connector, data_source, schedule, schedule_tz,
                                                           next_run_at, max_runtime_minutes, runner_id, seen_at)
                     VALUES (%s, 'zabbix', 'Zabbix', '15 */2 * * *', 'Europe/Istanbul', now() + interval '1 hour',
                             120, %s, now())
                     ON CONFLICT (name) DO UPDATE SET seen_at = now(), enabled = true""", (INSTANCE, RUNNER))
    yield INSTANCE
    with psycopg.connect(autocommit=True) as c:
        c.execute("DELETE FROM ingest.connector_run_request WHERE instance = %s", (INSTANCE,))
        c.execute("DELETE FROM ingest.connector_instance WHERE name = %s", (INSTANCE,))
        c.execute("DELETE FROM ingest.worker_heartbeat WHERE worker_id = %s", (RUNNER,))


def key() -> str:
    return f"t-{uuid.uuid4().hex}"


def run_body(**extra):
    return {"instance": INSTANCE, "mode": "snapshot", "idempotency_key": key(), **extra}


def test_run_request_requires_connector_admin_and_csrf(instance, steward, auditor, reader):
    url = "/api/v1/sources/Zabbix/run-requests"
    assert steward.post(url, json=run_body()).json()["error"]["code"] == "csrf"
    for c in (auditor, reader):
        assert c.post(url, json=run_body(), headers=CSRF).status_code == 403
    assert auditor.post("/api/v1/sources/Zabbix/credentials", json={"name": "x-deneme", "idempotency_key": key()},
                        headers=CSRF).status_code == 403
    assert reader.post("/api/v1/credentials/x-deneme/revoke", headers=CSRF).status_code == 403
    assert reader.post(f"/api/v1/run-requests/{uuid.uuid4()}/cancel", headers=CSRF).status_code == 403


def test_run_request_lifecycle_replay_conflict_and_cancel(instance, steward):
    url = "/api/v1/sources/Zabbix/run-requests"
    body = run_body(note="yeni eşleme denemesi")
    r = steward.post(url, json=body, headers=CSRF)
    assert r.status_code == 202 and r.json()["status"] == "pending" and r.json()["replayed"] is False
    rid = r.json()["id"]
    again = steward.post(url, json=body, headers=CSRF)
    assert again.status_code == 202 and again.json()["replayed"] is True and again.json()["id"] == rid
    conflict = steward.post(url, json=run_body(), headers=CSRF)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "etkin_istek"
    with psycopg.connect(autocommit=True) as c:
        row = c.execute("SELECT origin, requested_by, note FROM ingest.connector_run_request WHERE id = %s",
                        (rid,)).fetchone()
    assert row == ("manual", "dev:veri-sorumlusu", "yeni eşleme denemesi")
    doc = steward.get("/api/v1/sources").json()
    zbx = next(s for s in doc["sources"] if s["name"] == "Zabbix")
    assert doc["can"]["connector_admin"] is True
    inst = next(i for i in zbx["connectors"] if i["name"] == INSTANCE)
    assert inst["available"] is True and inst["schedule"] == "15 */2 * * *" and inst["schedule_tz"] == "Europe/Istanbul"
    req = next(q for q in zbx["requests"] if q["id"] == rid)
    assert req["status_label"] == "Bekliyor" and req["origin"] == "manual" and req["cancel_requested"] is False
    cancel = steward.post(f"/api/v1/run-requests/{rid}/cancel", headers=CSRF)
    assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
    assert steward.post(f"/api/v1/run-requests/{rid}/cancel", headers=CSRF).status_code == 409
    assert steward.post(f"/api/v1/run-requests/{uuid.uuid4()}/cancel", headers=CSRF).status_code == 404
    assert steward.post("/api/v1/run-requests/olmayan/cancel", headers=CSRF).status_code == 404
    with psycopg.connect(autocommit=True) as c:
        assert c.execute("""SELECT count(*) FROM audit.security_event WHERE principal = 'dev:veri-sorumlusu'
                             AND action IN ('connector_run_request', 'connector_run_cancel')
                             AND (detail->>'id' = %s OR resource = %s)""",
                         (rid, f"connector_run_request:{rid}")).fetchone()[0] >= 2


def test_run_request_is_refused_for_unknown_stale_or_mismatched_instance(instance, steward):
    url = "/api/v1/sources/Zabbix/run-requests"
    assert steward.post(url, json=run_body(instance="baska-ornek"), headers=CSRF).json()["error"]["code"] == "ornek_yok"
    assert steward.post("/api/v1/sources/BMC/run-requests", json=run_body(),
                        headers=CSRF).json()["error"]["code"] == "ornek_yok"
    assert steward.post("/api/v1/sources/Yok/run-requests", json=run_body(), headers=CSRF).status_code == 404
    bad = steward.post(url, json=run_body(since="2026-11-26T07:00:00+00:00"), headers=CSRF)  # since yalnızca delta
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "gecersiz_istek"
    assert steward.post(url, json=run_body(mode="delta", since="2026-11-26T07:00:00"),  # saat dilimsiz
                        headers=CSRF).status_code == 422
    with psycopg.connect(autocommit=True) as c:
        c.execute("UPDATE ingest.worker_heartbeat SET seen_at = now() - interval '10 minutes' WHERE worker_id = %s",
                  (RUNNER,))
    stale = steward.post(url, json=run_body(), headers=CSRF)
    assert stale.status_code == 422 and stale.json()["error"]["code"] == "calistirici_yok"
    doc = steward.get("/api/v1/sources").json()
    inst = next(i for s in doc["sources"] if s["name"] == "Zabbix" for i in s["connectors"] if i["name"] == INSTANCE)
    assert inst["available"] is False


def test_connector_rows_hidden_without_connector_read(instance, reader):
    doc = reader.get("/api/v1/sources").json()
    zbx = next(s for s in doc["sources"] if s["name"] == "Zabbix")
    assert zbx["connectors"] is None and zbx["requests"] is None and doc["can"]["connector_admin"] is False


def test_credential_token_is_shown_once_and_only_its_hash_is_stored(steward):
    name = f"apitest-{uuid.uuid4().hex[:8]}"
    body = {"name": name, "expires_days": 30, "per_minute": 1000, "idempotency_key": key()}
    url = "/api/v1/sources/Zabbix/credentials"
    try:
        r = steward.post(url, json=body, headers=CSRF)
        assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
        doc = r.json()
        token = doc["token"]
        assert token and doc["token_hint"] == token[-4:] and doc["per_minute"] == 1000
        with psycopg.connect(autocommit=True) as c:
            stored = c.execute("SELECT token_sha256, created_by FROM ingest.connector_credential WHERE name = %s",
                               (name,)).fetchone()
            remembered = c.execute("SELECT response::text FROM api.idempotency WHERE key = %s",
                                   (body["idempotency_key"],)).fetchone()[0]
            audit = c.execute("SELECT detail::text FROM audit.security_event WHERE resource = %s",
                              (f"connector_credential:{name}",)).fetchall()
        assert stored == (token_sha256(token), "dev:veri-sorumlusu")
        assert token not in remembered and token not in str(audit) and len(audit) == 1
        replayed = steward.post(url, json=body, headers=CSRF).json()
        assert replayed["replayed"] is True and replayed["token"] is None and "ilk yanıtta" in replayed["message"]
        listed = steward.get("/api/v1/sources").json()
        assert token not in str(listed)
        cred = next(c for s in listed["sources"] if s["name"] == "Zabbix" for c in s["credentials"] if c["name"] == name)
        assert cred["state"] == "active" and cred["created_by"] == "dev:veri-sorumlusu"
        dup = steward.post(url, json={**body, "idempotency_key": key()}, headers=CSRF)
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "kimlik_adi_var"
        rev = steward.post(f"/api/v1/credentials/{name}/revoke", headers=CSRF)
        assert rev.status_code == 200 and rev.json()["state"] == "revoked"
        assert steward.post(f"/api/v1/credentials/{name}/revoke", headers=CSRF).json()["error"]["code"] == "zaten_iptal"
        assert steward.post("/api/v1/credentials/olmayan-kimlik/revoke", headers=CSRF).status_code == 404
    finally:
        with psycopg.connect(autocommit=True) as c:
            c.execute("DELETE FROM ingest.connector_credential WHERE name = %s", (name,))


@pytest.mark.parametrize(("source", "body", "status", "code"), [
    ("Manual", {}, 422, "baglayicisiz_kaynak"),
    ("Zabbix", {"expires_days": 731}, 422, "gecersiz_istek"),
    ("Zabbix", {"name": "-kotu"}, 422, "gecersiz_istek"),
    ("Zabbix", {"token": "x"}, 422, "gecersiz_istek"),  # istemci belirteç veremez
    ("Yok", {}, 404, "bulunamadi"),
])
def test_credential_issue_is_validated(steward, source, body, status, code):
    payload = {"name": f"apitest-{uuid.uuid4().hex[:8]}", "idempotency_key": key(), **body}
    r = steward.post(f"/api/v1/sources/{source}/credentials", json=payload, headers=CSRF)
    assert r.status_code == status and r.json()["error"]["code"] == code
