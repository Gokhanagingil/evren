"""Kullanıcılar ve roller (U8.3, ADR-0034): kişi açma, rol verme ve geri alma, görevler ayrılığı, yetkili rolün
süresi, kendine yetki yasağı, ServiceNow kaynaklı kişinin korunması ve rolün hemen etkili olması. Veriler sentetiktir."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from .conftest import CSRF, make_client

ADMIN = ("evren_reader", "evren_iam_admin")


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


def new_person(c, name: str | None = None, **extra) -> dict:
    tag = uuid.uuid4().hex[:8]
    body = {"user_name": f"kisi.{tag}", "name": name or f"Test Kişi {tag}", "idempotency_key": key(), **extra}
    r = c.post("/api/v1/iam/people", json=body, headers=CSRF)
    assert r.status_code == 201, r.text
    return {**r.json(), "tag": tag}


def grant(c, target: str, role: str, why: str = "TALEP-1234 ekip değişikliği", ptype: str = "user", **extra):
    return c.post("/api/v1/iam/grants", json={"principal_type": ptype, "target": target, "role": role,
                                              "justification": why, "idempotency_key": key(), **extra}, headers=CSRF)


@pytest.fixture(scope="module")
def admin():
    with make_client(ADMIN, "yetki-yoneticisi") as c:
        yield c


def test_person_is_created_and_role_takes_effect_immediately_then_drops_on_revoke(admin):
    p = new_person(admin)
    r = grant(admin, p["sys_id"], "evren_data_steward")
    assert r.status_code == 201 and r.json()["renewed"] is False, r.text
    person = admin.get(f"/api/v1/iam/people/{p['sys_id']}").json()
    assert [(x["role"], x["via"]) for x in person["roles"]] == [("evren_data_steward", "direct")]
    assert person["roles"][0]["justification"] == "TALEP-1234 ekip değişikliği"
    assert person["source"] == "evren" and person["can_sign_in"] is True
    # Geliştirme oturumunda kişi kimliği kullanıcı adıdır: rol hemen etkili (yeniden giriş gerekmez)
    with make_client(("evren_reader",), p["user_name"]) as them:
        me = them.get("/api/v1/me").json()
        assert "evren_data_steward" in {x["name"] for x in me["roles"]} and me["can"]["retire"] is True
        r = admin.post("/api/v1/iam/grants/revoke", json={"principal_type": "user", "target": p["sys_id"],
                                                          "role": "evren_data_steward", "reason": "Görev değişti, TALEP-99"},
                       headers=CSRF)
        assert r.status_code == 200, r.text
        assert them.get("/api/v1/me").json()["can"]["retire"] is False  # bir sonraki istekte düştü
    events = admin.get(f"/api/v1/iam/people/{p['sys_id']}").json()["events"]
    assert [e["action"] for e in events][:2] == ["role_delete", "role_insert"]
    assert events[0]["reason"] == "Görev değişti, TALEP-99" and events[0]["actor"] == "dev:yetki-yoneticisi"
    assert events[1]["justification"] == "TALEP-1234 ekip değişikliği"
    assert any(e["action"] == "user_create" for e in events)


def test_grant_rules_reject_unsafe_assignments(admin):
    p = new_person(admin)
    short = grant(admin, p["sys_id"], "evren_data_steward", why="kısa")
    assert short.status_code == 422 and short.json()["error"]["code"] == "gerekce_gerekli"
    machine = grant(admin, p["sys_id"], "evren_integration")
    assert machine.status_code == 422 and machine.json()["error"]["code"] == "makine_rolu"
    # Yetkili rol süresiz verilmez, sınırı aşan süre reddedilir
    no_exp = grant(admin, p["sys_id"], "evren_schema_approver")
    assert no_exp.status_code == 422 and no_exp.json()["error"]["code"] == "sure_gerekli"
    too_long = grant(admin, p["sys_id"], "evren_schema_approver",
                     expires_at=(datetime.now(UTC) + timedelta(days=200)).isoformat())
    assert too_long.status_code == 422 and too_long.json()["error"]["code"] == "sure_uzun"
    glass = grant(admin, p["sys_id"], "evren_admin", expires_at=(datetime.now(UTC) + timedelta(days=3)).isoformat())
    assert glass.status_code == 422 and "1 gün" in glass.json()["error"]["message"]
    ok = grant(admin, p["sys_id"], "evren_schema_approver", expires_at=(datetime.now(UTC) + timedelta(days=30)).isoformat())
    assert ok.status_code == 201, ok.text
    # Görevler ayrılığı: şema onaylayıcı olan kişiye şema tasarımcısı verilmez
    sod = grant(admin, p["sys_id"], "evren_schema_designer")
    assert sod.status_code == 409 and sod.json()["error"]["code"] == "gorevler_ayriligi"
    assert "hazırlayan onaylayamaz" in sod.json()["error"]["message"].lower()
    # Aynı rol yeniden verilirse yenilenir (süre ve gerekçe güncellenir)
    again = grant(admin, p["sys_id"], "evren_schema_approver", why="Süre uzatma, TALEP-77",
                  expires_at=(datetime.now(UTC) + timedelta(days=60)).isoformat())
    assert again.status_code == 201 and again.json()["renewed"] is True


def test_nobody_grants_or_revokes_their_own_roles():
    with make_client(ADMIN, "kendine-veren") as me:
        with psycopg.connect(autocommit=True) as conn:
            sys_id = conn.execute("""INSERT INTO ref.sys_user (sys_id, user_name, name, source)
                                     VALUES (meta.new_sys_id(), 'kendine-veren', 'Kendine Veren', 'evren')
                                     ON CONFLICT (lower(user_name)) DO UPDATE SET name = EXCLUDED.name
                                     RETURNING sys_id""").fetchone()[0]
        r = grant(me, sys_id, "evren_data_steward")
        assert r.status_code == 403 and r.json()["error"]["code"] == "kendine_rol"
        r = me.post("/api/v1/iam/grants/revoke", json={"principal_type": "user", "target": sys_id,
                                                       "role": "evren_iam_admin", "reason": "Kendimi çıkarıyorum"},
                    headers=CSRF)
        assert r.status_code == 403
        r = me.patch(f"/api/v1/iam/people/{sys_id}", json={"active": False, "reason": "Kendimi pasifleştiriyorum"},
                     headers=CSRF)
        assert r.status_code == 403 and r.json()["error"]["code"] == "kendi_kaydi"


def test_only_iam_admin_writes_and_auditor_reads():
    with make_client(("evren_reader", "evren_data_steward"), "veri-sorumlusu-2") as steward:
        assert steward.get("/api/v1/iam/people").status_code == 403
        r = steward.post("/api/v1/iam/people", json={"user_name": "x.y", "name": "X Y", "idempotency_key": key()},
                         headers=CSRF)
        assert r.status_code == 403
    with make_client(("evren_auditor",), "denetci") as auditor:
        assert auditor.get("/api/v1/iam/people").status_code == 200
        roles = auditor.get("/api/v1/iam/roles").json()
        assert roles["can_admin"] is False
        assert auditor.get("/api/v1/iam/events").status_code == 200
        r = auditor.post("/api/v1/iam/grants", json={"principal_type": "user", "target": "0" * 32, "role": "evren_reader",
                                                     "justification": "Denetçi denemesi uzun", "idempotency_key": key()},
                         headers=CSRF)
        assert r.status_code == 403
        assert auditor.get("/api/v1/me").json()["can"]["iam_read"] is True


def test_role_matrix_shows_inherited_permissions_and_sod(admin):
    body = admin.get("/api/v1/iam/roles").json()
    roles = {r["name"]: r for r in body["roles"]}
    cm = roles["evren_config_manager"]
    assert cm["label_tr"] == "Konfigürasyon yöneticisi" and not cm["elevated"]
    ops = {(p["resource"], p["operation"]) for p in cm["permissions"]}
    assert ("governance", "task_admin") in ops and ("cmdb_ci", "read") in ops  # okuyucudan kalıtılır
    assert {s["role"] for s in cm["sod"]} == {"evren_auditor"}
    assert {"evren_schema_designer"} <= {s["role"] for s in roles["evren_schema_approver"]["sod"]}
    assert roles["evren_iam_admin"]["elevated"] and roles["evren_integration"]["machine"]
    assert body["policy"]["elevated_max_days"] == 90 and body["can_admin"] is True


def test_servicenow_people_are_protected_but_can_be_bound_to_entra(admin):
    tag = uuid.uuid4().hex[:8]
    sn_id = uuid.uuid4().hex
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'servicenow')",
                     (sn_id, f"sn.{tag}", f"SN Kişi {tag}"))
    r = admin.patch(f"/api/v1/iam/people/{sn_id}", json={"name": "Başka Ad"}, headers=CSRF)
    assert r.status_code == 422 and r.json()["error"]["code"] == "kaynak_kisi"
    oid = str(uuid.uuid4())
    r = admin.patch(f"/api/v1/iam/people/{sn_id}", json={"entra_oid": oid}, headers=CSRF)
    assert r.status_code == 422 and r.json()["error"]["code"] == "gerekce_gerekli"
    r = admin.patch(f"/api/v1/iam/people/{sn_id}", json={"entra_oid": oid, "reason": "Entra eşlemesi, TALEP-5"},
                    headers=CSRF)
    assert r.status_code == 200 and r.json()["changed"] == ["entra_oid"], r.text
    # Veritabanı da zorlar: API rolü ServiceNow kaynaklı kişinin adını değiştiremez
    with psycopg.connect(autocommit=True) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SET ROLE evren_app_api")
        conn.execute("UPDATE ref.sys_user SET name = 'x' WHERE sys_id = %s", (sn_id,))
    dup = admin.post("/api/v1/iam/people", json={"user_name": f"SN.{tag}", "name": "Aynı ad",
                                                 "idempotency_key": key()}, headers=CSRF)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "kullanici_adi_var"


def test_deactivating_a_person_revokes_direct_roles_and_list_filters(admin):
    p = new_person(admin)
    exp = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    assert grant(admin, p["sys_id"], "evren_cmdb_editor", expires_at=exp).status_code == 201
    listed = admin.get("/api/v1/iam/people", params={"role": "evren_cmdb_editor", "q": p["tag"]}).json()
    assert [x["sys_id"] for x in listed["items"]] == [p["sys_id"]]
    soon = admin.get("/api/v1/iam/people", params={"expiring": "true", "q": p["tag"]}).json()
    assert [x["sys_id"] for x in soon["items"]] == [p["sys_id"]]
    att = {i["id"]: i for i in admin.get("/api/v1/admin/attention").json()["items"]}
    assert att["grants"]["count"] >= 1 and att["grants"]["href"] == "#/kullanicilar/suresi-yaklasan"
    r = admin.patch(f"/api/v1/iam/people/{p['sys_id']}", json={"active": False}, headers=CSRF)
    assert r.status_code == 422
    r = admin.patch(f"/api/v1/iam/people/{p['sys_id']}", json={"active": False, "reason": "İşten ayrıldı, İK-42"},
                    headers=CSRF)
    assert r.status_code == 200 and r.json()["revoked"] == ["evren_cmdb_editor"], r.text
    assert grant(admin, p["sys_id"], "evren_reader").json()["error"]["code"] == "kisi_pasif"
    gone = admin.get("/api/v1/iam/people", params={"q": p["tag"]}).json()
    assert gone["items"] == []  # varsayılan görünüm etkin kişiler
    assert admin.get("/api/v1/iam/people", params={"q": p["tag"], "status": "inactive"}).json()["total"] == 1


def test_group_roles_need_entra_group_id_and_reach_members(admin):
    tag = uuid.uuid4().hex[:8]
    gid, gsys = str(uuid.uuid4()), uuid.uuid4().hex
    p = new_person(admin)
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("INSERT INTO ref.sys_user_group (sys_id, name) VALUES (%s, %s)", (gsys, f"Test Grup {tag}"))
        conn.execute('INSERT INTO ref.sys_user_grmember ("group", "user") VALUES (%s, %s)', (gsys, p["sys_id"]))
    r = grant(admin, gsys, "evren_match_reviewer", ptype="group")
    assert r.status_code == 409 and r.json()["error"]["code"] == "grup_kimligi_yok"
    r = admin.patch(f"/api/v1/iam/groups/{gsys}", json={"entra_group_id": gid, "reason": "Entra grubu eşlemesi"},
                    headers=CSRF)
    assert r.status_code == 200, r.text
    assert grant(admin, gsys, "evren_match_reviewer", ptype="group").status_code == 201
    person = admin.get(f"/api/v1/iam/people/{p['sys_id']}").json()
    assert [(x["role"], x["via"], x.get("group")) for x in person["roles"]] == [
        ("evren_match_reviewer", "group", f"Test Grup {tag}")]
    # Geliştirme oturumunda grup üyeliği ref tablosundan: üye rolü hemen alır
    with make_client(("evren_reader",), p["user_name"]) as them:
        assert them.get("/api/v1/me").json()["can"]["review_match"] is True
    # Rolü olan grubun kimliği değiştirilemez
    r = admin.patch(f"/api/v1/iam/groups/{gsys}", json={"entra_group_id": None, "reason": "Kaldırma denemesi"},
                    headers=CSRF)
    assert r.status_code == 409 and r.json()["error"]["code"] == "grup_rolleri_var"
    groups = {g["sys_id"]: g for g in admin.get("/api/v1/iam/groups", params={"q": tag}).json()["items"]}
    assert groups[gsys]["roles"][0]["role"] == "evren_match_reviewer" and groups[gsys]["members"] == 1


def test_idempotent_create_and_validation(admin):
    body = {"user_name": f"tekrar.{uuid.uuid4().hex[:6]}", "name": "Tekrar Kişi", "idempotency_key": key()}
    first = admin.post("/api/v1/iam/people", json=body, headers=CSRF)
    second = admin.post("/api/v1/iam/people", json=body, headers=CSRF)
    assert first.status_code == second.status_code == 201 and second.json()["replayed"] is True
    assert first.json()["sys_id"] == second.json()["sys_id"]
    bad = admin.post("/api/v1/iam/people", json={**body, "user_name": "a b<c>", "idempotency_key": key()}, headers=CSRF)
    assert bad.status_code == 422
    mail = admin.post("/api/v1/iam/people", json={**body, "user_name": "baska.kisi.x", "email": "olmaz",
                                                  "idempotency_key": key()}, headers=CSRF)
    assert mail.status_code == 422 and mail.json()["error"]["code"] == "eposta_gecersiz"
    assert admin.get("/api/v1/iam/people/xyz").status_code == 404
    assert admin.post("/api/v1/iam/people", json=body).status_code == 403  # CSRF başlığı yok
