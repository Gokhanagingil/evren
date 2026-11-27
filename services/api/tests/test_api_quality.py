"""Kalite ve uyum kuralları API'si (U8.6, ADR-0037): önizleme (kaç bulgu, kime, neden), kural yaşam döngüsü ve
gerekçe, düzenleme sınırı, şimdi çalıştır; görev çalışanı turu → bulgular, CI ayrıntısında bulgu, görev kararıyla
istisna, Konfigürasyon yöneticisinin istisnası ve geri alması; sağlık panosu; yetki ayrımı. Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client

ADMIN = ("evren_reader", "evren_config_manager")
ORPHAN_DB = {"related_classes": ["cmdb_ci_appl", "cmdb_ci_computer", "cmdb_ci_business_app", "cmdb_ci_service"],
             "exclude_classes": ["cmdb_ci_db_instance"]}


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


def owner_user(name: str) -> str:
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'evren') "
                     "ON CONFLICT DO NOTHING", (uuid.uuid4().hex, name, name.replace("-", " ").title()))
        return conn.execute("SELECT sys_id FROM ref.sys_user WHERE user_name = %s", (name,)).fetchone()[0]


def db_ci(name: str, owned_by: str | None = None) -> str:
    with psycopg.connect(autocommit=True) as conn:
        return conn.execute("INSERT INTO cmdb.cmdb_ci (sys_class_name, name, owned_by) VALUES ('cmdb_ci_db_instance', %s, %s) "
                            "RETURNING sys_id", (name, owned_by)).fetchone()[0]


def body(tag: str, **extra):
    return {"name": f"İlişkisiz veritabanı {tag}", "description": "Kimin kullandığı bilinmiyor.",
            "check_kind": "no_relation", "params": ORPHAN_DB, "scope_class": "cmdb_ci_db_instance",
            "scope_query": f"nameSTARTSWITH{tag}", "dimension": "completeness", "severity": "medium",
            "task_title": "Veritabanının ilişkilerini tamamlayın", "task_instructions": "Kullanan uygulamayı ekleyin.",
            "due_days": 14, "channel": "evren", "exception_days": 30, "idempotency_key": key(), **extra}


def worker_cycle() -> None:
    from evren_core.governance import ServiceNowTaskSimulator
    from evren_governance import GovernanceConfig, GovernanceWorker

    w = GovernanceWorker(GovernanceConfig(conninfo="", public_url="https://evren.ornek.test",
                                          worker_id=f"api-kural-{uuid.uuid4().hex[:6]}", sync_minutes=1),
                         transport=ServiceNowTaskSimulator(), connect=lambda: psycopg.connect())
    with w.open() as conn:
        w.cycle(conn)


@pytest.fixture(scope="module")
def admin():
    with make_client(ADMIN, "konf-yonetici-q") as c:
        yield c


def test_preview_counts_findings_and_rejects_bad_rule(admin):
    tag = f"qp{uuid.uuid4().hex[:6]}"
    sahip = owner_user(f"q-sahip-{tag}")
    db_ci(f"{tag}-a", sahip)
    db_ci(f"{tag}-b")
    used = db_ci(f"{tag}-c", sahip)
    with psycopg.connect(autocommit=True) as conn:
        app = conn.execute("INSERT INTO cmdb.cmdb_ci (sys_class_name, name) VALUES ('cmdb_ci_appl', %s) RETURNING sys_id",
                           (f"{tag}-uyg",)).fetchone()[0]
        conn.execute("""INSERT INTO cmdb.cmdb_rel_ci (parent, child, type)
                        SELECT %s, %s, sys_id FROM cmdb.cmdb_rel_type WHERE name = 'Depends on::Used by'""", (app, used))
    r = admin.post("/api/v1/quality/rules/preview", json={"check_kind": "no_relation", "params": ORPHAN_DB,
                                                          "scope_class": "cmdb_ci_db_instance",
                                                          "scope_query": f"nameSTARTSWITH{tag}"}, headers=CSRF)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["valid"] and p["in_scope"] == 3 and p["matched"] == 2 and p["by_basis"]["owned_by"] == 1
    assert p["by_basis"]["unassigned"] == 1 and p["people"] == 1 and p["inconclusive"] is None
    assert all("ilişkisi yok" in s["why"] for s in p["sample"])
    mf = admin.post("/api/v1/quality/rules/preview", json={"check_kind": "missing_fields", "params": {"fields": ["owned_by"]},
                                                           "scope_class": "cmdb_ci_db_instance",
                                                           "scope_query": f"nameSTARTSWITH{tag}"}, headers=CSRF).json()
    assert mf["matched"] == 1 and mf["sample"][0]["why"] == "Boş alan: Sahibi"
    bad = admin.post("/api/v1/quality/rules/preview", json={"check_kind": "missing_fields", "params": {"fields": ["yok"]},
                                                            "scope_class": "cmdb_ci"}, headers=CSRF).json()
    assert bad["valid"] is False and "yok" in bad["problem"]
    role = admin.post("/api/v1/quality/rules/preview", json={"check_kind": "not_seen_by_role", "params": {"role": "Monitoring"},
                                                             "scope_class": "cmdb_ci_server",
                                                             "scope_query": f"nameSTARTSWITH{tag}"}, headers=CSRF).json()
    assert role["valid"] and role["matched"] == 0
    listing = admin.get("/api/v1/quality/rules").json()
    assert {k["id"] for k in listing["kinds"]} >= {"no_relation", "missing_fields", "relation_not_allowed"}
    assert any(t["id"] == "iliskisiz-veritabani" for t in listing["templates"]) and listing["can_admin"] is True
    assert {r["id"] for r in listing["roles"]} >= {"Monitoring", "Discovery"}


def test_rule_lifecycle_findings_exception_and_health(admin):
    tag = f"ql{uuid.uuid4().hex[:6]}"
    sahip_adi = f"q-sorumlu-{tag}"
    sahip = owner_user(sahip_adi)
    a = db_ci(f"{tag}-a", sahip)
    db_ci(f"{tag}-b")
    r = admin.post("/api/v1/quality/rules", json=body(tag), headers=CSRF)
    assert r.status_code == 201, r.text
    rid = r.json()["sys_id"]
    assert r.json()["state"] == "draft"
    again = admin.post("/api/v1/quality/rules", json={**body(tag), "idempotency_key": key()}, headers=CSRF)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ad_var"
    assert admin.patch(f"/api/v1/quality/rules/{rid}", json={"severity": "high"}, headers=CSRF).status_code == 200
    short = admin.patch(f"/api/v1/quality/rules/{rid}", json={"state": "active", "reason": "kısa"}, headers=CSRF)
    assert short.status_code == 422
    act = admin.patch(f"/api/v1/quality/rules/{rid}", json={"state": "active", "reason": "İlk uyum taraması başlıyor"},
                      headers=CSRF)
    assert act.status_code == 200 and admin.get(f"/api/v1/quality/rules/{rid}").json()["next_run_at"]
    locked = admin.patch(f"/api/v1/quality/rules/{rid}", json={"severity": "low"}, headers=CSRF)
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "duzenlenemez"
    worker_cycle()
    d = admin.get(f"/api/v1/quality/rules/{rid}").json()
    assert d["open"] == 2 and d["runs"][0]["status"] == "done" and d["runs"][0]["tasks_opened"] == 2
    assert d["severity_tr"] == "Yüksek" and d["last_run"]["matched"] == 2
    fs = admin.get("/api/v1/quality/findings", params={"rule": rid}).json()
    assert fs["counts"]["open"] == 2 and len(fs["items"]) == 2
    fa = next(f for f in fs["items"] if f["ci"]["sys_id"] == a)
    assert fa["task"]["state"] == "open" and fa["text"] == "Uygulama, bilgisayar, iş uygulaması veya servis ile ilişkisi yok"
    # CI ayrıntısında bulgu
    ci_doc = admin.get(f"/api/v1/ci/{a}").json()
    assert [f["rule"]["sys_id"] for f in ci_doc["findings"]] == [rid]
    # Sorumlu "geçerli değil" der → süreli istisna
    with make_client(("evren_reader",), sahip_adi) as me:
        t = me.get(f"/api/v1/tasks/{fa['task']['sys_id']}").json()
        assert t["finding"]["sys_id"] == fa["sys_id"] and {o["id"] for o in t["outcomes"]} == {
            "corrected", "not_applicable", "rejected"}
        ok = me.post(f"/api/v1/tasks/{fa['task']['sys_id']}/complete",
                     json={"outcome": "not_applicable", "note": "Arşiv veritabanı, bilinçli bağımsız"}, headers=CSRF)
        assert ok.status_code == 200, ok.text
        assert me.post(f"/api/v1/quality/findings/{fa['sys_id']}/exception", json={"reason": "Kendi istisnam olsun"},
                       headers=CSRF).status_code == 403
    fd = admin.get(f"/api/v1/quality/findings/{fa['sys_id']}").json()
    assert fd["state"] == "excepted" and fd["exception"]["by"] == f"dev:{sahip_adi}" and fd["can_reopen"] is True
    # Yönetici istisnayı kaldırır; diğer bulguya kendisi istisna verir (açık görevi iptal edilir)
    re_ = admin.post(f"/api/v1/quality/findings/{fa['sys_id']}/reopen", json={"reason": "Arşiv kararı geri alındı"},
                     headers=CSRF)
    assert re_.status_code == 200 and admin.get(f"/api/v1/quality/findings/{fa['sys_id']}").json()["state"] == "open"
    fb = next(f for f in fs["items"] if f["ci"]["sys_id"] != a)
    assert admin.post(f"/api/v1/quality/findings/{fb['sys_id']}/exception", json={"reason": "kısa"},
                      headers=CSRF).status_code == 422
    ex = admin.post(f"/api/v1/quality/findings/{fb['sys_id']}/exception",
                    json={"reason": "Göç bitene kadar bilinçli olarak bağımsız", "days": 60}, headers=CSRF)
    assert ex.status_code == 200 and "istisna" in ex.json()["message"]
    fbd = admin.get(f"/api/v1/quality/findings/{fb['sys_id']}").json()
    assert fbd["tasks"][0]["state"] == "done" and fbd["tasks"][0]["outcome"] == "cancelled"
    assert admin.post(f"/api/v1/quality/findings/{fb['sys_id']}/exception",
                      json={"reason": "İkinci istisna denemesi"}, headers=CSRF).status_code == 409
    # Şimdi çalıştır → yeniden açılan bulguya yeni görev; sağlık panosu ölçümü
    run = admin.post(f"/api/v1/quality/rules/{rid}/run", json={"reason": "İstisna sonrası yeniden tarama"}, headers=CSRF)
    assert run.status_code == 202
    assert admin.post(f"/api/v1/quality/rules/{rid}/run", json={"reason": "İstisna sonrası yeniden tarama"},
                      headers=CSRF).status_code == 409
    worker_cycle()
    tasks_a = admin.get(f"/api/v1/quality/findings/{fa['sys_id']}").json()["tasks"]
    assert len(tasks_a) == 2 and tasks_a[0]["state"] == "open"
    h = admin.get("/api/v1/quality/health").json()
    assert h["measured"] and set(h["dimensions"]) == {"completeness", "correctness", "compliance", "coverage"}
    assert any(m["id"] == f"rule:{rid}" for m in h["dimensions"]["completeness"]["metrics"]) and h["trend"]
    assert h["findings"]["open"] >= 1
    with psycopg.connect(autocommit=True) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM audit.security_event WHERE resource IN (%s, %s, %s) ORDER BY id",
            (f"quality_rule:{rid}", f"finding:{fa['sys_id']}", f"finding:{fb['sys_id']}"))]
    assert actions[:1] == ["quality_rule_create"] and {"finding_exception", "finding_reopen", "quality_rule_run"} <= set(actions)


def test_reader_sees_health_and_findings_but_cannot_manage(admin):
    tag = f"qr{uuid.uuid4().hex[:6]}"
    rid = admin.post("/api/v1/quality/rules", json=body(tag), headers=CSRF).json()["sys_id"]
    with make_client(("evren_reader",), "okuyucu-q") as reader:
        assert reader.get("/api/v1/quality/health").status_code == 200
        listing = reader.get("/api/v1/quality/rules").json()
        assert rid in {i["sys_id"] for i in listing["items"]} and listing["can_admin"] is False
        assert reader.get("/api/v1/quality/findings").status_code == 200
        assert reader.post("/api/v1/quality/rules", json=body(f"{tag}x"), headers=CSRF).status_code == 403
        assert reader.post("/api/v1/quality/rules/preview", json={"check_kind": "no_relation",
                                                                  "scope_class": "cmdb_ci"}, headers=CSRF).status_code == 403
        assert reader.patch(f"/api/v1/quality/rules/{rid}", json={"state": "active", "reason": "Okuyucu denemesi yapıyor"},
                            headers=CSRF).status_code == 403
        assert reader.post(f"/api/v1/quality/rules/{rid}/run", json={"reason": "Okuyucu denemesi yapıyor"},
                           headers=CSRF).status_code == 403
    bad = admin.post("/api/v1/quality/rules", json=body(f"{tag}b", params={"fields": ["owned_by"]}), headers=CSRF)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "kural_gecersiz"
    assert admin.post(f"/api/v1/quality/rules/{rid}/run", json={"reason": "Taslak kural çalışmaz"},
                      headers=CSRF).status_code == 409
    assert admin.get("/api/v1/quality/rules/xyz").status_code == 404
    assert admin.get(f"/api/v1/quality/findings/{uuid.uuid4().hex}").status_code == 404
