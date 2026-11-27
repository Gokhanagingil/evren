"""Zamanlanmış yönetişim kampanyaları API'si (U8.5, ADR-0036): önizleme, oluşturma, durum geçişleri, düzenleme
sınırı, "şimdi çalıştır" isteği, yetki ayrımı (Konfigürasyon yöneticisi yazar, Denetçi okur, okuyucu görmez) ve
tekrar oynatma. Görevleri görev çalışanı açar; bu testler yalnızca API sözleşmesini sınar. Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client
from .scenarios import make_owned_ci

ADMIN = ("evren_reader", "evren_config_manager")


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


def body(tag: str, **extra):
    return {"name": f"Kampanya {tag}", "kind": "attestation", "title": "Uygulama verilerini teyit edin",
            "instructions": "Sahip, destek grubu ve ortam doğru mu?", "scope_class": "cmdb_ci_linux_server",
            "scope_query": f"nameSTARTSWITH{tag}", "frequency": "quarterly", "due_days": 20,
            "idempotency_key": key(), **extra}


@pytest.fixture(scope="module")
def admin():
    with make_client(ADMIN, "konf-yonetici-k") as c:
        yield c


def owned(prefix: str, owner: str | None) -> str:
    """``make_owned_ci`` CI'ı ``onay-…`` adıyla açar; kapsam koşulu için adı ``prefix`` ile başlatırız."""
    ci, _name, _user = make_owned_ci(owner)
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("UPDATE cmdb.cmdb_ci SET name = %s WHERE sys_id = %s", (f"{prefix}-{ci[:6]}", ci))
    return ci


def test_preview_counts_scope_by_assignee_basis(admin):
    tag = f"kp{uuid.uuid4().hex[:6]}"
    owner = f"kmp-sahip-{uuid.uuid4().hex[:6]}"
    owned(tag, owner)
    owned(tag, owner)
    owned(tag, None)
    r = admin.post("/api/v1/campaigns/preview", json={"scope_class": "cmdb_ci_linux_server",
                                                      "scope_query": f"nameSTARTSWITH{tag}"}, headers=CSRF)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["in_scope"] == 3 and p["by_basis"]["owned_by"] == 2 and p["by_basis"]["unassigned"] == 1
    assert p["people"] == 1 and len(p["sample"]) == 3
    assert {s["basis"] for s in p["sample"]} == {"owned_by", "unassigned"}
    # Üst sınıf alt sınıfları kapsar
    up = admin.post("/api/v1/campaigns/preview", json={"scope_class": "cmdb_ci_server",
                                                       "scope_query": f"nameSTARTSWITH{tag}"}, headers=CSRF).json()
    assert up["in_scope"] == 3
    bad = admin.post("/api/v1/campaigns/preview", json={"scope_class": "cmdb_ci_linux_server",
                                                        "scope_query": "olmayan_alan=1"}, headers=CSRF)
    assert bad.status_code == 200 and bad.json()["valid"] is False and "olmayan_alan" in bad.json()["problem"]
    nocls = admin.post("/api/v1/campaigns/preview", json={"scope_class": "cmdb_ci_yok"}, headers=CSRF)
    assert nocls.status_code == 200 and nocls.json()["valid"] is False
    assert p["valid"] is True
    assert admin.post("/api/v1/campaigns/preview", json={"scope_class": "cmdb_ci_linux_server"}).status_code == 403


def test_create_draft_then_activate_pause_retire_with_reason(admin):
    tag = f"kc{uuid.uuid4().hex[:6]}"
    b = body(tag)
    r = admin.post("/api/v1/campaigns", json=b, headers=CSRF)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["state"] == "draft" and c["next_run_at"] is None and c["replayed"] is False
    again = admin.post("/api/v1/campaigns", json=b, headers=CSRF)
    assert again.status_code == 201 and again.json()["sys_id"] == c["sys_id"] and again.json()["replayed"] is True
    dup = admin.post("/api/v1/campaigns", json={**b, "idempotency_key": key()}, headers=CSRF)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ad_var"
    cid = c["sys_id"]
    d = admin.get(f"/api/v1/campaigns/{cid}").json()
    assert d["frequency"] == "quarterly" and d["frequency_tr"] and d["scope_class_tr"] == "Linux sunucu"
    assert d["transitions"] == ["active", "retired"] and d["runs"] == [] and d["can_admin"] is True
    # Taslak düzenlenir
    e = admin.patch(f"/api/v1/campaigns/{cid}", json={"due_days": 15, "frequency": "monthly"}, headers=CSRF)
    assert e.status_code == 200 and e.json()["changed"] == ["due_days", "schedule"], e.text
    # Durum değişikliği gerekçe ister
    short = admin.patch(f"/api/v1/campaigns/{cid}", json={"state": "active", "reason": "kısa"}, headers=CSRF)
    assert short.status_code == 422 and short.json()["error"]["code"] == "gerekce_gerekli"
    act = admin.patch(f"/api/v1/campaigns/{cid}", json={"state": "active", "reason": "Çeyrek dönemi teyidi başlıyor"},
                      headers=CSRF)
    assert act.status_code == 200 and act.json()["state"] == "active"
    d = admin.get(f"/api/v1/campaigns/{cid}").json()
    assert d["state"] == "active" and d["next_run_at"] and d["due_days"] == 15 and d["frequency"] == "monthly"
    # Etkin kampanya düzenlenmez; önce duraklatılır
    locked = admin.patch(f"/api/v1/campaigns/{cid}", json={"title": "Yeni başlık"}, headers=CSRF)
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "duzenlenemez"
    pause = admin.patch(f"/api/v1/campaigns/{cid}", json={"state": "paused", "reason": "Dönem sonu dondurma süresi"},
                        headers=CSRF)
    assert pause.status_code == 200 and admin.get(f"/api/v1/campaigns/{cid}").json()["next_run_at"] is None
    assert admin.patch(f"/api/v1/campaigns/{cid}", json={"title": "Yeni başlık"}, headers=CSRF).status_code == 200
    ret = admin.patch(f"/api/v1/campaigns/{cid}", json={"state": "retired", "reason": "Kampanya artık gerekmiyor"},
                      headers=CSRF)
    assert ret.status_code == 200
    back = admin.patch(f"/api/v1/campaigns/{cid}", json={"state": "active", "reason": "Yeniden başlatma denemesi"},
                       headers=CSRF)
    assert back.status_code == 409 and back.json()["error"]["code"] == "gecis_yok"
    listing = admin.get("/api/v1/campaigns").json()
    assert {i["sys_id"]: i for i in listing["items"]}[cid]["state_tr"] == "Emekli"
    tree = {c["name"]: c for c in listing["classes"]}
    assert tree["cmdb_ci"]["depth"] == 0 and tree["cmdb_ci_linux_server"]["depth"] > tree["cmdb_ci_server"]["depth"]
    assert {"monthly", "quarterly", "semiannual", "annual"} == {f["id"] for f in listing["frequencies"]}
    with psycopg.connect(autocommit=True) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM audit.security_event WHERE resource = %s ORDER BY id", (f"campaign:{cid}",))]
    assert actions[0] == "campaign_create" and actions.count("campaign_update") == 5


def test_create_active_and_validation(admin):
    tag = f"ka{uuid.uuid4().hex[:6]}"
    r = admin.post("/api/v1/campaigns", json=body(tag, kind="access_review", activate=True), headers=CSRF)
    assert r.status_code == 201, r.text
    assert r.json()["state"] == "active" and r.json()["next_run_at"] and "Şimdi çalıştır" in r.json()["message"]
    custom = admin.post("/api/v1/campaigns", json=body(f"{tag}x", frequency=None, schedule="30 8 * * 1"), headers=CSRF)
    assert custom.status_code == 201
    assert admin.get(f"/api/v1/campaigns/{custom.json()['sys_id']}").json()["frequency_tr"] == "Özel: 30 8 * * 1"
    cases = [({"frequency": None, "schedule": "her gün"}, "kampanya_gecersiz"),
             ({"frequency": None}, "siklik_gerekli"),
             ({"scope_query": "olmayan_alan=1"}, "kampanya_gecersiz"),
             ({"channel": "olmayan-kanal"}, "kanal_yok")]
    for extra, code in cases:
        bad = admin.post("/api/v1/campaigns", json=body(f"{tag}{code}", **extra), headers=CSRF)
        assert bad.status_code == 422 and bad.json()["error"]["code"] == code, (extra, bad.text)
    assert admin.post("/api/v1/campaigns", json=body(f"{tag}k", kind="baska"), headers=CSRF).status_code == 422


def test_run_now_requests_once_and_only_when_active(admin):
    tag = f"kr{uuid.uuid4().hex[:6]}"
    draft = admin.post("/api/v1/campaigns", json=body(tag), headers=CSRF).json()["sys_id"]
    no = admin.post(f"/api/v1/campaigns/{draft}/run", json={"reason": "Hemen başlatmak istiyorum"}, headers=CSRF)
    assert no.status_code == 409 and no.json()["error"]["code"] == "etkin_degil"
    cid = admin.post("/api/v1/campaigns", json=body(f"{tag}a", activate=True), headers=CSRF).json()["sys_id"]
    short = admin.post(f"/api/v1/campaigns/{cid}/run", json={"reason": "şimdi"}, headers=CSRF)
    assert short.status_code == 422
    ok = admin.post(f"/api/v1/campaigns/{cid}/run", json={"reason": "Denetim öncesi erken başlatma"}, headers=CSRF)
    assert ok.status_code == 202 and ok.json()["run"], ok.text
    twice = admin.post(f"/api/v1/campaigns/{cid}/run", json={"reason": "Denetim öncesi erken başlatma"}, headers=CSRF)
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "calisiyor"
    runs = admin.get(f"/api/v1/campaigns/{cid}").json()["runs"]
    assert [(r["trigger"], r["status"], r["requested_by"]) for r in runs] == [
        ("manual", "requested", "dev:konf-yonetici-k")]
    # Görev listesi kampanyaya göre süzülür (görevi görev çalışanı açar; burada kurulum olarak yazıyoruz)
    ci = owned(tag, None)
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO gov.task (kind, title, ci_sys_id, assignee_basis, due_at, source, source_ref, dedup_key,
                                              channel, delivery_status, created_by)
                        VALUES ('attestation', 'Teyit — kurulum', %s, 'unassigned', now() + interval '20 days', 'campaign',
                                %s, %s, 'evren', 'not_needed', 'evren:kampanya')""", (ci, cid, f"campaign:{cid}:{ci}"))
    only = admin.get("/api/v1/tasks", params={"view": "all", "campaign": cid}).json()
    assert [t["ci"]["sys_id"] for t in only["items"]] == [ci]
    assert admin.get(f"/api/v1/campaigns/{cid}").json()["open"] == 1
    assert admin.get("/api/v1/tasks", params={"view": "all", "campaign": "x"}).status_code == 422
    assert admin.post(f"/api/v1/campaigns/{uuid.uuid4().hex}/run", json={"reason": "Olmayan kampanya denemesi"},
                      headers=CSRF).status_code == 404


def test_auditor_reads_reader_and_owner_cannot(admin):
    tag = f"kd{uuid.uuid4().hex[:6]}"
    cid = admin.post("/api/v1/campaigns", json=body(tag), headers=CSRF).json()["sys_id"]
    with make_client(("evren_auditor",), "denetci-k") as auditor:
        listing = auditor.get("/api/v1/campaigns").json()
        assert cid in {i["sys_id"] for i in listing["items"]} and listing["can_admin"] is False
        assert auditor.get(f"/api/v1/campaigns/{cid}").json()["can_admin"] is False
        assert auditor.post("/api/v1/campaigns", json=body(f"{tag}d"), headers=CSRF).status_code == 403
        assert auditor.patch(f"/api/v1/campaigns/{cid}", json={"state": "active", "reason": "Denetçi denemesi yapıyor"},
                             headers=CSRF).status_code == 403
        assert auditor.post(f"/api/v1/campaigns/{cid}/run", json={"reason": "Denetçi denemesi yapıyor"},
                            headers=CSRF).status_code == 403
    with make_client(("evren_reader",), "okuyucu-k") as reader:
        assert reader.get("/api/v1/campaigns").status_code == 403
        assert reader.get(f"/api/v1/campaigns/{cid}").status_code == 403
        assert reader.post("/api/v1/campaigns/preview", json={"scope_class": "cmdb_ci_linux_server"},
                           headers=CSRF).status_code == 403
    assert admin.get("/api/v1/campaigns/xyz").status_code == 404
    assert admin.post("/api/v1/campaigns", json=body(f"{tag}c"), headers={}).status_code == 403  # CSRF
