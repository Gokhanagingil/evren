"""Yönetişim görevleri API'si (U8.4, ADR-0035): görevi CI'ın sorumlusu görür ve karar verir, başkası görmez;
atanmamış görev ve dikkat listeleri; yalnızca Konfigürasyon yöneticisi açar ve iptal eder; kanal ayarı doğrulanır;
varsayılan kanal Evren olunca görev ServiceNow'a gitmez. Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client
from .scenarios import make_owned_ci

ADMIN = ("evren_reader", "evren_config_manager")


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


def open_task(c, **body):
    r = c.post("/api/v1/tasks", json={"title": "Uygulama verilerini teyit edin",
                                      "description": "Sahip, destek grubu ve ortam doğru mu?",
                                      "idempotency_key": key(), **body}, headers=CSRF)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def admin():
    with make_client(ADMIN, "konf-yonetici") as c:
        yield c


def test_owner_sees_and_decides_task_others_cannot(admin):
    owner = f"gorev-sahip-{uuid.uuid4().hex[:6]}"
    ci, name, user = make_owned_ci(owner)
    t = open_task(admin, ci_sys_id=ci)
    assert t["basis"] == "owned_by" and t["channel"] == "servicenow" and t["number"].startswith("EGT")
    detail = admin.get(f"/api/v1/tasks/{t['sys_id']}").json()
    assert detail["assignee"]["user"] == user and detail["ci"]["name"] == name
    assert detail["delivery_status"] == "pending" and detail["can_cancel"] is True
    assert [e["action"] for e in detail["events"]] == ["created"]
    with make_client(("evren_reader",), owner) as me:
        mine = me.get("/api/v1/tasks").json()
        assert t["sys_id"] in {i["sys_id"] for i in mine["items"]} and mine["counts"]["mine"] >= 1
        assert "all" not in mine["counts"] and mine["can_admin"] is False
        att = {i["id"]: i for i in me.get("/api/v1/admin/attention").json()["items"]}
        assert att["tasks"]["href"] == "#/gorevler" and att["tasks"]["count"] >= 1
        assert me.get(f"/api/v1/tasks/{t['sys_id']}").json()["can_complete"] is True
        short = me.post(f"/api/v1/tasks/{t['sys_id']}/complete", json={"outcome": "corrected", "note": "kısa"},
                        headers=CSRF)
        assert short.status_code == 422 and short.json()["error"]["code"] == "aciklama_gerekli"
        ok = me.post(f"/api/v1/tasks/{t['sys_id']}/complete",
                     json={"outcome": "corrected", "note": "Destek grubu güncellendi"}, headers=CSRF)
        assert ok.status_code == 200 and "Düzeltildi" in ok.json()["message"], ok.text
        again = me.post(f"/api/v1/tasks/{t['sys_id']}/complete", json={"outcome": "confirmed"}, headers=CSRF)
        assert again.status_code == 409
        assert me.get("/api/v1/tasks", params={"view": "all"}).status_code == 403
    with make_client(("evren_reader",), "baska-biri") as other:
        assert other.get(f"/api/v1/tasks/{t['sys_id']}").status_code == 404  # varlığı sızmaz
        assert other.post(f"/api/v1/tasks/{t['sys_id']}/complete", json={"outcome": "confirmed"},
                          headers=CSRF).status_code == 404
    done = admin.get(f"/api/v1/tasks/{t['sys_id']}").json()
    assert done["state"] == "done" and done["outcome"] == "corrected" and done["closed_by"] == f"dev:{owner}"
    assert next(e["action"] for e in done["events"]) == "completed"


def test_unassigned_and_group_tasks(admin):
    ci, _, _ = make_owned_ci(None)
    t = open_task(admin, ci_sys_id=ci)
    assert t["basis"] == "unassigned" and "atanmamış" in t["message"]
    un = admin.get("/api/v1/tasks", params={"view": "unassigned"}).json()
    assert t["sys_id"] in {i["sys_id"] for i in un["items"]}
    att = {i["id"]: i for i in admin.get("/api/v1/admin/attention").json()["items"]}
    assert att["task_unassigned"]["href"] == "#/gorevler/atanmamis"
    # Gruba atanan görevi grubun üyesi kapatır
    tag = uuid.uuid4().hex[:6]
    gsys, usys = uuid.uuid4().hex, uuid.uuid4().hex
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("INSERT INTO ref.sys_user_group (sys_id, name, source) VALUES (%s, %s, 'servicenow')",
                     (gsys, f"Gorev Grubu {tag}"))
        conn.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'servicenow')",
                     (usys, f"uye-{tag}", f"Üye {tag}"))
        conn.execute('INSERT INTO ref.sys_user_grmember ("group", "user") VALUES (%s, %s)', (gsys, usys))
    g = open_task(admin, assignee={"mode": "group", "sys_id": gsys}, title="Grup görevi")
    assert g["basis"] == "assigned"
    with make_client(("evren_reader",), f"uye-{tag}") as member:
        assert g["sys_id"] in {i["sys_id"] for i in member.get("/api/v1/tasks").json()["items"]}
        r = member.post(f"/api/v1/tasks/{g['sys_id']}/complete", json={"outcome": "confirmed"}, headers=CSRF)
        assert r.status_code == 200, r.text
    bad = admin.post("/api/v1/tasks", json={"title": "Sorumlusuz", "idempotency_key": key()}, headers=CSRF)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "ci_gerekli"


def test_only_config_manager_opens_and_cancels_auditor_reads(admin):
    with make_client(("evren_reader", "evren_data_steward"), "veri-sorumlusu-g") as steward:
        r = steward.post("/api/v1/tasks", json={"title": "Deneme görevi", "assignee": {"mode": "user", "sys_id": "0" * 32},
                                                "idempotency_key": key()}, headers=CSRF)
        assert r.status_code == 403
        assert steward.get("/api/v1/task-channels").status_code == 403
    ci, _, _ = make_owned_ci(f"iptal-{uuid.uuid4().hex[:6]}")
    t = open_task(admin, ci_sys_id=ci)
    with make_client(("evren_auditor",), "denetci-g") as auditor:
        assert t["sys_id"] in {i["sys_id"] for i in auditor.get("/api/v1/tasks", params={"view": "all"}).json()["items"]}
        assert auditor.post(f"/api/v1/tasks/{t['sys_id']}/cancel", json={"reason": "Denetçi iptal denemesi"},
                            headers=CSRF).status_code == 403
    assert admin.post(f"/api/v1/tasks/{t['sys_id']}/cancel", json={"reason": "kısa"}, headers=CSRF).status_code == 422
    r = admin.post(f"/api/v1/tasks/{t['sys_id']}/cancel", json={"reason": "Yanlışlıkla açıldı, TALEP-3"}, headers=CSRF)
    assert r.status_code == 200 and admin.get(f"/api/v1/tasks/{t['sys_id']}").json()["outcome"] == "cancelled"


def test_channel_settings_are_validated_and_default_channel_routes_new_tasks(admin):
    ch = {c["name"]: c for c in admin.get("/api/v1/task-channels").json()["channels"]}
    assert ch["servicenow"]["is_default"] and ch["servicenow"]["settings"]["table"] == "sc_task"
    bad = admin.patch("/api/v1/task-channels/servicenow", json={"settings": {"table": "sys_user"},
                                                               "reason": "Tablo değişikliği denemesi"}, headers=CSRF)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "ayar_gecersiz"
    assert admin.patch("/api/v1/task-channels/servicenow", json={"is_default": False, "reason": "Varsayılanı kaldır"},
                       headers=CSRF).json()["error"]["code"] == "varsayilan_gerekli"
    try:
        r = admin.patch("/api/v1/task-channels/evren", json={"is_default": True, "reason": "ServiceNow hesabı gelene dek"},
                        headers=CSRF)
        assert r.status_code == 200, r.text
        ci, _, _ = make_owned_ci(f"evren-kanal-{uuid.uuid4().hex[:6]}")
        t = open_task(admin, ci_sys_id=ci)
        d = admin.get(f"/api/v1/tasks/{t['sys_id']}").json()
        assert t["channel"] == "evren" and d["delivery_status"] == "not_needed"
    finally:
        r = admin.patch("/api/v1/task-channels/servicenow", json={"is_default": True, "reason": "Varsayılana dönüş"},
                        headers=CSRF)
        assert r.status_code == 200, r.text
    chans = {c["name"]: c for c in admin.get("/api/v1/task-channels").json()["channels"]}
    assert chans["servicenow"]["is_default"] and not chans["evren"]["is_default"]


def test_task_creation_is_idempotent_and_validated(admin):
    ci, _, _ = make_owned_ci(f"tekrar-{uuid.uuid4().hex[:6]}")
    body = {"title": "Tekrar görevi", "ci_sys_id": ci, "idempotency_key": key()}
    a = admin.post("/api/v1/tasks", json=body, headers=CSRF)
    b = admin.post("/api/v1/tasks", json=body, headers=CSRF)
    assert a.status_code == b.status_code == 201 and b.json()["replayed"] is True
    assert a.json()["sys_id"] == b.json()["sys_id"]
    assert admin.post("/api/v1/tasks", json={**body, "idempotency_key": key(), "ci_sys_id": "f" * 32},
                      headers=CSRF).json()["error"]["code"] == "ci_yok"
    assert admin.post("/api/v1/tasks", json={**body, "idempotency_key": key(), "due_days": 0},
                      headers=CSRF).status_code == 422
    assert admin.get("/api/v1/tasks/xyz").status_code == 404
