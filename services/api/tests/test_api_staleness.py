"""Eskime işletimi (U7.1, F1-06, ADR-0028): öneri listesi ve özeti, Veri sorumlusunun kararı, yetki ve yeniden denetim.
Tam emekliye ayırma akışı uzlaştırıcı tatbikatında (test_staleness_pg). Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client
from .scenarios import apply_pending_retirements, make_stale_case


@pytest.fixture(scope="module")
def steward():
    with make_client(("evren_reader", "evren_data_steward"), "veri-sorumlusu") as c:
        yield c


def body(action, note="Donanım iade edildi"):
    return {"action": action, "note": note, "idempotency_key": f"test-{uuid.uuid4().hex}"}


def test_summary_lists_rules_and_observer_source_health(steward, reader):
    doc = reader.get("/api/v1/staleness/summary").json()
    assert {r["applies_to"] for r in doc["rules"]} == {"cmdb_ci_hardware", "cmdb_ci_vm_instance"}
    names = {s["name"] for s in doc["sources"]}
    assert {"BMC", "vCenter", "Zabbix"} <= names and "ServiceNow" not in names and "Manual" not in names
    assert not doc["can_decide"] and steward.get("/api/v1/staleness/summary").json()["can_decide"]
    assert steward.get("/api/v1/me").json()["can"]["retire"] is True


def test_candidate_list_detail_and_reconciler_recheck(steward, reader):
    ci, name, case = make_stale_case()
    rows = reader.get("/api/v1/staleness/cases", params={"view": "candidate", "q": name}).json()["rows"]
    assert [(r["id"], r["stage"], r["days_since"] >= 49, r["can_decide"]) for r in rows] == [(case, "candidate", True,
                                                                                               False)]
    assert rows[0]["observations"][0]["label"]
    detail = steward.get(f"/api/v1/ci/{ci}").json()["staleness"]
    assert detail["case"]["id"] == case and detail["can_retire"] and detail["rule"]["applies_to"] == "cmdb_ci_hardware"
    assert reader.post(f"/api/v1/staleness/{case}/decision", json=body("retire"), headers=CSRF).status_code == 403
    assert steward.post(f"/api/v1/staleness/{case}/decision", json=body("retire", "  "),
                        headers=CSRF).status_code == 422
    payload = body("retire")
    res = steward.post(f"/api/v1/staleness/{case}/decision", json=payload, headers=CSRF)
    assert res.status_code == 202
    assert steward.post(f"/api/v1/staleness/{case}/decision", json=payload, headers=CSRF).json()["id"] == res.json()["id"]
    again = steward.post(f"/api/v1/staleness/{case}/decision", json=body("dismiss"), headers=CSRF)
    assert again.status_code == 409 and again.json()["error"]["code"] == "bekleyen_istek"
    # CI'ın keşif kaydı görülüyor: uzlaştırıcı uygulama anında reddeder, CI emekliye ayrılmaz
    assert apply_pending_retirements() == ["retire_rejected"]
    st = steward.get(f"/api/v1/retirement-requests/{res.json()['id']}").json()
    assert st["status"] == "rejected" and "yeniden görüldü" in st["result_note"]
    with psycopg.connect(autocommit=True) as c:
        assert c.execute("SELECT life_cycle_stage FROM cmdb.cmdb_ci WHERE sys_id = %s", (ci,)).fetchone() == (None,)
        assert c.execute("SELECT count(*) FROM audit.security_event WHERE action = 'ci_retire_decision' "
                         "AND resource = %s", (f"staleness_case:{case}",)).fetchone()[0] == 1


def test_stale_case_cannot_be_retired_but_can_be_dismissed(steward):
    _, name, case = make_stale_case("stale", days=20)
    res = steward.post(f"/api/v1/staleness/{case}/decision", json=body("retire"), headers=CSRF)
    assert res.status_code == 409 and res.json()["error"]["code"] == "aday_degil"
    assert steward.post(f"/api/v1/staleness/{case}/decision", json=body("dismiss", "Yedek sunucu"),
                        headers=CSRF).status_code == 202
    assert apply_pending_retirements() == ["retire_dismissed"]
    rows = steward.get("/api/v1/staleness/cases", params={"view": "dismissed", "q": name}).json()["rows"]
    assert [(r["id"], r["status"], r["decision_note"], r["decided_by"]) for r in rows] == [
        (case, "dismissed", "Yedek sunucu", "dev:veri-sorumlusu")]
    closed = steward.post(f"/api/v1/staleness/{case}/decision", json=body("dismiss"), headers=CSRF)
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "oneri_kapali"


def test_validation_and_unknown(steward):
    assert steward.post("/api/v1/staleness/kisa/decision", json=body("retire"), headers=CSRF).status_code == 400
    assert steward.post(f"/api/v1/staleness/{'0' * 32}/decision", json=body("retire"), headers=CSRF).status_code == 404
    assert steward.post(f"/api/v1/staleness/{'0' * 32}/decision", json=body("sil"), headers=CSRF).status_code == 422
    assert steward.post(f"/api/v1/staleness/{'0' * 32}/decision", json=body("retire")).status_code == 403  # CSRF
    assert steward.get("/api/v1/staleness/cases", params={"view": "yok"}).status_code == 422
