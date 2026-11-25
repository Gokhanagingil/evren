"""Gölge mod raporu ve kanıt anlık görüntüsü (F0-13, göç 0010)."""
from __future__ import annotations

import hashlib
import json
import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client
from .scenarios import make_sn_pair


@pytest.fixture(scope="module")
def steward():
    with make_client(("evren_reader", "evren_data_steward"), "veri-sorumlusu") as c:
        yield c


def _key() -> str:
    return f"t-{uuid.uuid4()}"


def test_report_structure_and_role_based_coverage(reader):
    r = reader.get("/api/v1/reports/shadow").json()
    assert r["can_snapshot"] is False
    # Demo verisinde ağ keşfi çalışması kısmi (devre kesici açık) → rapor kesin değil, kanıt "kısmi"
    assert r["status"] == "inconclusive" and r["provenance"]["collection_status"] == "partial"
    assert "NNMi" in r["provenance"]["unhealthy_sources"]
    assert r["provenance"]["itsm_roles"] == ["ITSM"]  # ürün adı değil rol
    t = r["coverage"]["totals"]
    assert t["itsm"] == t["itsm_seen_by_discovery"] + t["itsm_only"]
    assert t["total"] == sum(m["total"] for m in r["coverage"]["matrix"])
    assert "ist-db-01" in {x["name"] for x in r["coverage"]["itsm_only"]}  # eski ServiceNow kaydı
    assert all("ITSM" not in x["roles"] for x in r["coverage"]["not_in_itsm"])


def test_duplicate_with_shared_mac_but_different_serial_is_strong_with_advice(reader):
    groups = reader.get("/api/v1/reports/shadow").json()["duplicates"]["groups"]
    [g] = [g for g in groups if {c["name"] for c in g["cis"]} == {"ist-db-01"}]
    assert g["verdict"] == "güçlü" and g["hardware_conflict"] is True
    assert {e["key_type"] for e in g["evidence"]} >= {"mac", "name"}
    assert sorted(c["seen_by_discovery"] for c in g["cis"]) == [False, True]
    assert "emekliye ayırma adayı" in g["advice"]


@pytest.mark.parametrize(("same_serial", "verdict"), [(True, "kesin"), (False, "çelişkili")])
def test_duplicate_verdicts_follow_identity_rules(reader, same_serial, verdict):
    a, b, _name = make_sn_pair(same_serial=same_serial)
    groups = reader.get("/api/v1/reports/shadow").json()["duplicates"]["groups"]
    [g] = [g for g in groups if {c["sys_id"] for c in g["cis"]} == {a, b}]
    assert g["verdict"] == verdict
    if verdict == "çelişkili":  # ad tek başına kimlik değildir (kural 7)
        assert "birleştirmeyin" in g["advice"]


def test_drift_separates_cosmetic_from_real_differences(reader):
    drift = reader.get("/api/v1/reports/shadow").json()["drift"]
    by = {d["attribute"]: d for d in drift["by_attribute"]}
    assert by["name"]["real"] == 0 and by["name"]["cosmetic"] > 0  # IST-DB-01 ↔ ist-db-01
    assert drift["real"] == sum(d["real"] for d in drift["by_attribute"])
    real = [s for s in drift["samples"] if not s["cosmetic"]]
    assert real and all(s["itsm_source"] == "ServiceNow" and s["itsm_value"] != s["golden_value"] for s in real)
    owner = [s for s in real if s["attribute"] == "owned_by"]
    assert owner and owner[0]["itsm_display"] != owner[0]["itsm_value"]  # referans sys_id değil, adıyla gösterilir


def test_snapshot_is_immutable_verifiable_and_idempotent(steward):
    body = {"note": "2026-Q4 kapsam denetimi", "idempotency_key": _key()}
    assert steward.post("/api/v1/reports/shadow/snapshots", json=body).status_code == 403  # CSRF yok
    r = steward.post("/api/v1/reports/shadow/snapshots", json=body, headers=CSRF)
    assert r.status_code == 201, r.text
    snap = r.json()
    again = steward.post("/api/v1/reports/shadow/snapshots", json=body, headers=CSRF)
    assert again.status_code == 201 and again.json()["replayed"] is True and again.json()["id"] == snap["id"]
    assert snap["id"] in {x["id"] for x in steward.get("/api/v1/reports/shadow/snapshots").json()["items"]}

    dl = steward.get(f"/api/v1/reports/shadow/snapshots/{snap['id']}")
    assert dl.status_code == 200 and dl.headers["content-disposition"].startswith("attachment;")
    assert hashlib.sha256(dl.content).hexdigest() == dl.headers["x-evren-content-sha256"] == snap["sha256"]
    content = json.loads(dl.content)
    assert content["captured_by"] == "dev:veri-sorumlusu" and content["note"] == body["note"]
    assert content["provenance"]["collection_status"] == snap["collection_status"] == "partial"

    with psycopg.connect(autocommit=True) as conn:  # sahip (süper kullanıcı) bile değiştiremez
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE insight.evidence_snapshot SET note = 'x' WHERE sys_id = %s", (snap["id"],))
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM insight.evidence_snapshot WHERE sys_id = %s", (snap["id"],))
        conn.execute("SET ROLE evren_app_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE insight.evidence_snapshot SET note = 'x'")


def test_snapshot_requires_permission_and_validates_input(reader, steward):
    r = reader.post("/api/v1/reports/shadow/snapshots", json={"idempotency_key": _key()}, headers=CSRF)
    assert r.status_code == 403
    with psycopg.connect() as conn:
        conn.execute("SET ROLE evren_auditor_ro")
        row = conn.execute("SELECT decision FROM audit.security_event WHERE principal = 'dev:salt-okuyucu' "
                           "AND action = 'create:shadow_report' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ("deny",)
    assert steward.post("/api/v1/reports/shadow/snapshots", json={"idempotency_key": _key(), "x": 1},
                        headers=CSRF).status_code == 422
    assert steward.get("/api/v1/reports/shadow/snapshots/NOT-AN-ID").status_code == 400
    assert steward.get(f"/api/v1/reports/shadow/snapshots/{'0' * 32}").status_code == 404


def test_auditor_may_capture_evidence():
    with make_client(("evren_reader", "evren_auditor"), "denetci") as c:
        assert c.get("/api/v1/reports/shadow").json()["can_snapshot"] is True
