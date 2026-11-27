"""Sihirbazlar (U8.7, ADR-0038): entegrasyon sihirbazı kataloğu, kurulum paketi (çalıştırıcı kurallarıyla doğrulanır,
saklanmaz, sır değeri reddedilir), kurulum durumu (kimlik → çalıştırıcı bildirimi → deneme çalışması); şema
sihirbazının kaydetmeden doğrulaması (yeni sınıf + alanlar + seçim değerleri tek sette). Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client

STEWARD = ("evren_reader", "evren_data_steward")


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


def plan(name: str, **extra):
    return {"name": name, "connector": "zabbix", "url": "https://zabbix.kurum.local/api_jsonrpc.php",
            "token_file": f"/run/secrets/{name}_token", "gateway_token_file": f"/run/secrets/evren_gateway_{name}",
            **extra}


@pytest.fixture(scope="module")
def steward():
    with make_client(STEWARD, "entegrasyon-yoneticisi") as c:
        yield c


def test_catalog_lists_connectors_by_role_with_auth_fields(steward):
    r = steward.get("/api/v1/integrations")
    assert r.status_code == 200, r.text
    cat = {c["id"]: c for c in r.json()["connectors"]}
    assert {"zabbix", "vcenter", "servicenow", "defender", "nnmi", "bmc_discovery"} <= set(cat)
    z = cat["zabbix"]
    assert z["role"] == "Monitoring" and z["auth"] == "bearer" and z["url_required"] is True
    assert [f["key"] for f in z["auth_fields"]] == ["token_file"] and z["auth_fields"][0]["secret"] is True
    assert cat["defender"]["url_required"] is False and [f["key"] for f in cat["defender"]["auth_fields"]] == [
        "tenant_id", "client_id", "client_secret_file"]
    with make_client(("evren_reader",), "okuyucu-ent") as reader:
        assert reader.get("/api/v1/integrations").status_code == 403


def test_plan_renders_runner_block_and_never_accepts_secret_values(steward):
    name = f"zabbix-{uuid.uuid4().hex[:6]}"
    r = steward.post("/api/v1/integrations/plan", json=plan(name, schedule_choice="custom", schedule="15 */2 * * *",
                                                            page_size=200), headers=CSRF)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["data_source"] == "Zabbix" and p["schedule"] == "15 */2 * * *" and len(p["next_runs"]) == 2
    assert p["yaml"].startswith(f"  - name: {name}\n") and "page_size: 200" in p["yaml"]
    assert [s["key"] for s in p["secrets"]] == ["token_file", "gateway_token_file"]
    manual = steward.post("/api/v1/integrations/plan", json=plan(name, schedule_choice="manual"), headers=CSRF).json()
    assert "schedule: null" in manual["yaml"] and manual["next_runs"] == []
    # Sır değeri alanı yok (extra="forbid"); göreli yol, http adres, geçersiz zamanlama reddedilir
    assert steward.post("/api/v1/integrations/plan", json={**plan(name), "token": "cok-gizli"},
                        headers=CSRF).status_code == 422
    for bad, needle in (({**plan(name), "token_file": "secrets/tok"}, "mutlak dosya yolu"),
                        (plan(name, url="http://zabbix.kurum.local"), "https"),
                        (plan(name, schedule_choice="custom", schedule="her gün"), "zamanlama"),
                        ({**plan(name), "token_file": None}, "token_file")):
        res = steward.post("/api/v1/integrations/plan", json=bad, headers=CSRF)
        assert res.status_code == 422 and needle in res.json()["error"]["message"], (bad, res.text)
    # Saklanmaz: veritabanında bu ad yok
    with psycopg.connect(autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM ingest.connector_instance WHERE name = %s", (name,)).fetchone()[0] == 0
    nothing = steward.get(f"/api/v1/integrations/{name}").json()
    assert nothing["started"] is False and nothing["steps"]["credential"]["state"] == "missing" and not nothing["complete"]


def test_status_follows_credential_runner_report_and_test_run(steward):
    name = f"zabbix-{uuid.uuid4().hex[:6]}"
    issued = steward.post("/api/v1/sources/Zabbix/credentials", json={"name": name, "idempotency_key": key()},
                          headers=CSRF)
    assert issued.status_code == 201
    s = steward.get(f"/api/v1/integrations/{name}").json()
    assert s["steps"]["credential"]["ok"] and not s["steps"]["instance"]["reported"] and not s["complete"]
    assert name in {p["name"] for p in steward.get("/api/v1/integrations").json()["pending"]}
    # Çalıştırıcı örneği bildirir (kurulum; çalıştırıcının yaptığı gibi)
    runner = f"test-runner-{uuid.uuid4().hex[:6]}"
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind)
                        VALUES (%s, now(), now(), 15, 'connector')""", (runner,))
        conn.execute("""INSERT INTO ingest.connector_instance (name, connector, data_source, schedule_tz,
                                                               max_runtime_minutes, runner_id, seen_at)
                        VALUES (%s, 'zabbix', 'Zabbix', 'Europe/Istanbul', 60, %s, now())""", (name, runner))
    s = steward.get(f"/api/v1/integrations/{name}").json()
    assert s["steps"]["instance"]["available"] is True and s["steps"]["run"]["status"] is None
    run = steward.post("/api/v1/sources/Zabbix/run-requests", json={"instance": name, "idempotency_key": key()},
                       headers=CSRF)
    assert run.status_code == 202, run.text
    s = steward.get(f"/api/v1/integrations/{name}").json()
    assert s["steps"]["run"]["status"] == "pending" and s["complete"] is False
    assert name not in {p["name"] for p in steward.get("/api/v1/integrations").json()["pending"]}
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("UPDATE ingest.connector_run_request SET status = 'cancelled', finished_at = now() WHERE instance = %s",
                     (name,))


def test_schema_wizard_validates_new_class_with_fields_and_choices_without_saving():
    tag = uuid.uuid4().hex[:6]
    cls = f"u_cmdb_ci_kiosk_{tag}"
    changes = [
        {"op": "add_class", "table": cls, "payload": {"parent": "cmdb_ci_hardware", "label_tr": f"Kiosk {tag}"}},
        {"op": "add_field", "table": cls, "element": "u_ekran_boyutu",
         "payload": {"type": "integer", "label_tr": "Ekran boyutu (inç)", "sensitivity": "internal"}},
        {"op": "add_field", "table": cls, "element": "u_konum_tipi",
         "payload": {"type": "string", "label_tr": "Konum tipi", "max_length": 40, "choice": True}},
        {"op": "add_choice", "table": cls, "element": "u_konum_tipi", "payload": {"value": "lobi", "label_tr": "Lobi"}},
    ]
    with make_client(("evren_reader", "evren_schema_designer"), "sema-tasarimci-sih") as c:
        r = c.post("/api/v1/schema/change-sets/validate", json={"changes": changes}, headers=CSRF)
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["ok"] is True and len(v["changes"]) == 4 and len(v["impact"]) == 4
        assert "u_cmdb_ci_kiosk" in v["changes"][0] and "Lobi" in v["changes"][3]
        bad = c.post("/api/v1/schema/change-sets/validate",
                     json={"changes": [{**changes[0], "table": "cmdb_ci_kiosk"}]}, headers=CSRF).json()
        assert bad["ok"] is False and "u_ ile başlamalı" in bad["errors"][0]
        dup = c.post("/api/v1/schema/change-sets/validate",
                     json={"changes": [changes[0], {**changes[1], "element": "u_ekran_boyutu"},
                                       {**changes[1], "element": "u_ekran_boyutu"}]}, headers=CSRF).json()
        assert dup["ok"] is False and "zaten kullanılıyor" in dup["errors"][0]
        with psycopg.connect(autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM meta.schema_change WHERE target_table = %s",
                                (cls,)).fetchone()[0] == 0
    with make_client(("evren_reader",), "okuyucu-sih") as reader:
        assert reader.post("/api/v1/schema/change-sets/validate", json={"changes": changes},
                           headers=CSRF).status_code == 403
