"""Sahiplik onayı (U6.4, F1-17, ADR-0027): yalnızca CI'ın sorumlusu ve CI sahibi rolüyle; istek uzlaştırıcıdan geçer;
ret gerekçe ister ve anomali açar; sahipsiz ve geçersiz onaylar raporlanır (CMDB-06). Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client
from .scenarios import apply_pending_attestations, make_owned_ci

OWNER = "sahip-" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def owner():
    with make_client(("evren_reader", "evren_ci_owner"), OWNER) as c:
        yield c


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


def body(items, decision="attested", **kw):
    return {"items": items, "decision": decision, "idempotency_key": key(), **kw}


def test_owner_attests_and_reconciler_applies(owner, reader):
    ci, name, user = make_owned_ci(OWNER)
    me = owner.get("/api/v1/me").json()
    assert me["can"]["attest"] and me["sys_user"]["mapped"]
    att = owner.get(f"/api/v1/ci/{ci}").json()["attestation"]
    assert (att["state"], att["can_attest"], att["policy"]["name"]) == ("not_reviewed", True, "uretim")
    assert att["owners"][0]["sys_id"] == user
    payload = body([ci])
    res = owner.post("/api/v1/attestations", json=payload, headers=CSRF)
    assert res.status_code == 202 and len(res.json()["accepted"]) == 1
    assert owner.post("/api/v1/attestations", json=payload, headers=CSRF).json()["accepted"] == res.json()["accepted"]
    again = owner.post("/api/v1/attestations", json=body([ci]), headers=CSRF)
    assert again.status_code == 409 and "Bekleyen" in again.json()["error"]["details"][0]["reason"]
    rid = res.json()["accepted"][0]["request"]
    assert owner.get("/api/v1/attestation-requests", params={"ids": rid}).json()["done"] is False
    assert apply_pending_attestations() == ["attest_applied"]
    assert owner.get("/api/v1/attestation-requests", params={"ids": rid}).json()["requests"][0]["status"] == "applied"
    att = reader.get(f"/api/v1/ci/{ci}").json()["attestation"]
    assert att["state"] == "valid" and att["attested_by"] and att["valid_until"] and not att["can_attest"]
    rows = owner.get("/api/v1/attestation/items", params={"view": "mine", "q": name}).json()["rows"]
    assert [(r["sys_id"], r["state"], r["mine"]) for r in rows] == [(ci, "valid", True)]
    summary = owner.get("/api/v1/attestation/summary").json()
    assert summary["mine"]["valid"] >= 1 and summary["can_attest"] and summary["in_scope"] >= 1
    with psycopg.connect(autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM audit.security_event WHERE action = 'ci_attest' "
                         "AND detail->'cis' ? %s", (ci,)).fetchone()[0] == 1


def test_only_the_owner_with_the_role_may_attest(owner, reader):
    other = "baska-" + uuid.uuid4().hex[:6]
    ci, _, _ = make_owned_ci(other)
    res = owner.post("/api/v1/attestations", json=body([ci]), headers=CSRF)
    assert res.status_code == 409 and "sahibi veya yöneteni" in res.json()["error"]["details"][0]["reason"]
    assert owner.get(f"/api/v1/ci/{ci}").json()["attestation"]["blocker"].startswith("Yalnızca")
    assert reader.post("/api/v1/attestations", json=body([ci]), headers=CSRF).status_code == 403
    with make_client(("evren_reader", "evren_ci_owner"), "kaydi-olmayan-kisi") as nobody:
        res = nobody.post("/api/v1/attestations", json=body([ci]), headers=CSRF)
        assert res.status_code == 403 and res.json()["error"]["code"] == "kullanici_kaydi_yok"
        assert nobody.get("/api/v1/attestation/items", params={"view": "mine"}).json()["user_mapped"] is False
    # bilinmeyen CI ile varlık sızdırılmaz; toplu istekte uygun olanlar alınır
    mine, _, _ = make_owned_ci(OWNER)
    res = owner.post("/api/v1/attestations", json=body([mine, ci, "f" * 32]), headers=CSRF)
    assert res.status_code == 202 and [a["ci"] for a in res.json()["accepted"]] == [mine]
    assert [s["reason"] for s in res.json()["skipped"]][1] == "CI bulunamadı."
    apply_pending_attestations()


def test_rejection_requires_reason_and_opens_anomaly_and_control(owner):
    ci, name, _ = make_owned_ci(OWNER)
    assert owner.post("/api/v1/attestations", json=body([ci], "rejected", reason="not_exists"),
                      headers=CSRF).status_code == 422
    assert owner.post("/api/v1/attestations", json=body([ci], "attested", reason="not_exists"),
                      headers=CSRF).status_code == 422
    res = owner.post("/api/v1/attestations", json=body([ci], "rejected", reason="not_exists",
                                                       note="Sunucu kapatıldı"), headers=CSRF)
    assert res.status_code == 202
    assert apply_pending_attestations() == ["attest_applied"]
    att = owner.get(f"/api/v1/ci/{ci}").json()["attestation"]
    assert att["state"] == "rejected" and att["open_rejection"]["status"] == "open"
    assert att["history"][0]["reason_label"] == "Bu CI artık yok"
    assert ci in [r["sys_id"] for r in owner.get("/api/v1/attestation/items",
                                                 params={"view": "rejected", "q": name}).json()["rows"]]
    control = next(c for c in owner.get("/api/v1/controls").json() if c["id"] == "CMDB-06")
    assert control["status"] == "fail" and {"sys_id": ci, "name": name, "state": "rejected"} in control["evidence"]
    assert control["provenance"]["collection_status"] == "complete"


def test_orphan_and_out_of_scope(owner):
    orphan, name, _ = make_owned_ci(None, used_for="Test")
    rows = owner.get("/api/v1/attestation/items", params={"view": "orphan", "q": name}).json()["rows"]
    assert [(r["sys_id"], r["state"], r["owned_by"]) for r in rows] == [(orphan, "not_reviewed", None)]
    assert owner.get("/api/v1/attestation/summary").json()["orphan"] >= 1
    retired, _, _ = make_owned_ci(OWNER, life_cycle_stage="End of Life")  # emekli CI kapsam dışı
    att = owner.get(f"/api/v1/ci/{retired}").json()["attestation"]
    assert att["state"] == "out_of_scope" and "kapsamında değil" in att["blocker"]
    res = owner.post("/api/v1/attestations", json=body([retired]), headers=CSRF)
    assert res.status_code == 409


@pytest.mark.parametrize("items", [["a" * 32, "a" * 32], ["XYZ"], ["a" * 32] * 0, [uuid.uuid4().hex for _ in range(201)]])
def test_validation(owner, items):
    assert owner.post("/api/v1/attestations", json=body(items), headers=CSRF).status_code == 422


def test_csrf_and_request_ids(owner):
    assert owner.post("/api/v1/attestations", json=body(["a" * 32])).status_code == 403
    assert owner.get("/api/v1/attestation-requests", params={"ids": "x,y"}).status_code == 400
