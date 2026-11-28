"""ServiceNow uyumlu Table API — okuma (U9.3a, ADR-0044).

Biçim (``result``, metin değerler, UTC zaman, referans bağlantısı, ``X-Total-Count``/``Link``), encoded query
(STARTSWITH, IN, INSTANCEOF, ORDERBY), tablo kalıtımı, alan seçimi, tekil kayıt, ilişki ve referans tabloları;
olumsuz senaryolar: bilinmeyen tablo, ``javascript:``, dot-walk, desteklenmeyen parametre, sınır aşımı, yetkisiz
okuma (403 + denetim olayı), hassasiyet sınırının üstündeki alan (yanıtta ve sorguda yok), reddedilen alt sınıf
(listede, tekil okumada ve ilişkilerde yok), Entra taşıyıcı belirteci. Veriler sentetiktir; ServiceNow istemcisiyle
canlı kabul sayılmaz."""
from __future__ import annotations

import re
import uuid

import psycopg
import pytest

from .conftest import make_client
from .test_api_mcp import access_token, entra_app  # noqa: F401 - fikstür

TIME = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
RUNS_ON = "Runs on::Runs"


def owner():
    return psycopg.connect(autocommit=True)


@pytest.fixture(scope="module")
def world():
    """Kendine ait küçük dünya: iki Linux, bir Windows sunucu, bir uygulama, bir üretici, bir kişi, iki ilişki."""
    tag = f"snt-{uuid.uuid4().hex[:6]}"
    ids = {k: uuid.uuid4().hex for k in ("lin1", "lin2", "win", "app", "company", "user")}
    with owner() as o:
        o.execute("INSERT INTO ref.core_company (sys_id, name, manufacturer) VALUES (%s, %s, true)",
                  (ids["company"], f"{tag} Üretici"))
        o.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, email) VALUES (%s, %s, %s, %s)",
                  (ids["user"], f"{tag}.kisi", "Deneme Kişi", f"{tag}@ornek.test"))
        rows = [("lin1", "cmdb_ci_linux_server", "a"), ("lin2", "cmdb_ci_linux_server", "b"),
                ("win", "cmdb_ci_win_server", "c"), ("app", "cmdb_ci_appl", "d")]
        for key, cls, suffix in rows:
            o.execute("""INSERT INTO cmdb.cmdb_ci (sys_id, sys_class_name, name, operational_status, manufacturer,
                                                   owned_by, ip_address)
                         VALUES (%s, %s, %s, 1, %s, %s, %s)""",
                      (ids[key], cls, f"{tag}-{suffix}", ids["company"], ids["user"], f"10.9.0.{ord(suffix)}"))
        rel = o.execute("SELECT sys_id FROM cmdb.cmdb_rel_type WHERE name = %s", (RUNS_ON,)).fetchone()[0]
        ids["rel_type"] = rel
        ids["rel_lin"] = o.execute("INSERT INTO cmdb.cmdb_rel_ci (parent, child, type) VALUES (%s, %s, %s) "
                                   "RETURNING sys_id", (ids["app"], ids["lin1"], rel)).fetchone()[0]
        ids["rel_win"] = o.execute("INSERT INTO cmdb.cmdb_rel_ci (parent, child, type) VALUES (%s, %s, %s) "
                                   "RETURNING sys_id", (ids["app"], ids["win"], rel)).fetchone()[0]
    ids["tag"] = tag
    return ids


@pytest.fixture(scope="module")
def reader():
    with make_client(("evren_reader",), "sn-okuyucu") as c:
        yield c


def _role(name: str, perms: list[tuple[str, str, str]]) -> str:
    with owner() as o:
        o.execute("INSERT INTO iam.role (name, label_tr) VALUES (%s, %s) ON CONFLICT DO NOTHING", (name, name))
        o.execute("DELETE FROM iam.permission WHERE role = %s", (name,))
        for resource, sensitivity, effect in perms:
            o.execute("INSERT INTO iam.permission (role, resource, operation, max_sensitivity, effect) "
                      "VALUES (%s, %s, 'read', %s, %s)", (name, resource, sensitivity, effect))
    return name


def test_list_uses_servicenow_shape_query_inheritance_and_paging_headers(reader, world):
    tag = world["tag"]
    r = reader.get("/api/now/table/cmdb_ci_server", params={"sysparm_query": f"nameSTARTSWITH{tag}^ORDERBYDESCname"})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    # Üst sınıf tablosu alt sınıfları da döner (Linux + Windows); uygulama sunucu değildir
    assert [x["name"] for x in result] == [f"{tag}-c", f"{tag}-b", f"{tag}-a"]
    assert r.headers["X-Total-Count"] == "3"
    first = result[0]
    assert first["sys_class_name"] == "cmdb_ci_win_server" and first["operational_status"] == "1"
    assert TIME.match(first["sys_updated_on"]) and first["serial_number"] == ""
    assert first["manufacturer"] == {"link": f"http://testserver/api/now/table/core_company/{world['company']}",
                                     "value": world["company"]}
    # Alan seçimi, sayfa ve Link başlığı
    r = reader.get("/api/now/table/cmdb_ci_server", params={
        "sysparm_query": f"nameSTARTSWITH{tag}^ORDERBYname", "sysparm_fields": "name,sys_id,yok_boyle_alan,name",
        "sysparm_limit": "1", "sysparm_offset": "1"})
    assert r.json()["result"] == [{"name": f"{tag}-b", "sys_id": world["lin2"]}]
    links = r.headers["Link"]
    for rel, off in (("first", 0), ("prev", 0), ("next", 2), ("last", 2)):
        assert re.search(rf'sysparm_limit=1&sysparm_offset={off}>;rel="{rel}"', links), links
    # Referans bağlantısız ve IN koşulu
    r = reader.get("/api/now/table/cmdb_ci", params={
        "sysparm_query": f"nameIN{tag}-a,{tag}-d", "sysparm_fields": "name,owned_by",
        "sysparm_exclude_reference_link": "true"})
    assert sorted(x["name"] for x in r.json()["result"]) == [f"{tag}-a", f"{tag}-d"]
    assert all(x["owned_by"] == world["user"] for x in r.json()["result"])


def test_instanceof_single_record_and_relations(reader, world):
    tag = world["tag"]
    r = reader.get("/api/now/table/cmdb_ci", params={
        "sysparm_query": f"sys_class_nameINSTANCEOFcmdb_ci_linux_server^nameSTARTSWITH{tag}"})
    assert {x["sys_id"] for x in r.json()["result"]} == {world["lin1"], world["lin2"]}
    one = reader.get(f"/api/now/table/cmdb_ci_linux_server/{world['lin1']}")
    assert one.status_code == 200 and one.json()["result"]["name"] == f"{tag}-a"
    # Kayıt bu tablonun soyağacında değilse yok sayılır
    assert reader.get(f"/api/now/table/cmdb_ci_server/{world['app']}").status_code == 404
    missing = reader.get("/api/now/table/cmdb_ci/" + "0" * 32)
    assert missing.status_code == 404
    assert missing.json() == {"error": {"message": "No Record found",
                                        "detail": "Kayıt yok veya erişim kuralı okumayı kısıtlıyor."},
                              "status": "failure"}
    # v2 yolu aynı; sonuçsuz sorgu 404 değil, boş dizi (ServiceNow v2)
    v2 = reader.get(f"/api/now/v2/table/cmdb_ci_linux_server/{world['lin1']}")
    assert v2.status_code == 200 and v2.json()["result"]["sys_id"] == world["lin1"]
    empty = reader.get("/api/now/v2/table/cmdb_ci", params={"sysparm_query": f"name={tag}-yok"})
    assert empty.status_code == 200 and empty.json() == {"result": []} and empty.headers["X-Total-Count"] == "0"
    assert reader.get("/api/now/table/cmdb_ci/kisa").status_code == 404
    rels = reader.get("/api/now/table/cmdb_rel_ci", params={"sysparm_query": f"parent={world['app']}"}).json()["result"]
    assert {x["child"]["value"] for x in rels} == {world["lin1"], world["win"]}
    assert rels[0]["type"]["link"].endswith(f"/api/now/table/cmdb_rel_type/{world['rel_type']}")
    rtype = reader.get(f"/api/now/table/cmdb_rel_type/{world['rel_type']}").json()["result"]
    assert rtype["name"] == RUNS_ON and rtype["parent_descriptor"] == "Runs on"
    company = reader.get(f"/api/now/table/core_company/{world['company']}").json()["result"]
    assert company["name"] == f"{tag} Üretici" and company["manufacturer"] == "true"
    # Kişinin e-postası okuyucuya (iç düzey) gitmez ve süzgeçte kullanılamaz
    user = reader.get(f"/api/now/table/sys_user/{world['user']}").json()["result"]
    assert user["user_name"] == f"{tag}.kisi" and "email" not in user
    assert reader.get("/api/now/table/sys_user", params={"sysparm_query": "emailLIKEornek"}).status_code == 400


@pytest.mark.parametrize(("path", "params", "status"), [
    ("/api/now/table/sys_db_object", {}, 400),                                   # CMDB dışı tablo
    ("/api/now/table/cmdb_ci", {"sysparm_query": "name=javascript:gs.getUserID()"}, 400),
    ("/api/now/table/cmdb_ci", {"sysparm_query": "location.name=Ankara"}, 400),  # dot-walk
    ("/api/now/table/cmdb_ci", {"sysparm_fields": "location.name"}, 400),
    ("/api/now/table/cmdb_ci", {"sysparm_query": "yok_alan=1"}, 400),
    ("/api/now/table/cmdb_ci", {"sysparm_query": "sys_class_nameINSTANCEOFyok_sinif"}, 400),
    ("/api/now/table/cmdb_ci", {"sysparm_display_value": "true"}, 400),
    ("/api/now/table/cmdb_ci", {"sysparm_limit": "20000"}, 400),
    ("/api/now/table/cmdb_ci", {"sysparm_limit": "0"}, 400),
    ("/api/now/table/cmdb_ci/" + "0" * 32, {"sysparm_display_value": "all"}, 400),
    ("/api/now/yok/boyle", {}, 404),
])
def test_invalid_requests_fail_in_servicenow_format(reader, path, params, status):
    r = reader.get(path, params=params)
    assert r.status_code == status, r.text
    body = r.json()
    assert body["status"] == "failure" and body["error"]["message"] and "detail" in body["error"]


def test_write_methods_are_not_offered(reader, world):
    r = reader.post("/api/now/table/cmdb_ci", json={"name": "yazilmamali"})
    assert r.status_code == 405 and r.json()["status"] == "failure"
    assert reader.delete(f"/api/now/table/cmdb_ci/{world['lin1']}").status_code == 405


def test_reader_without_permission_is_denied_and_audited():
    with make_client(("evren_iam_admin",), "sn-yetkisiz") as c:
        r = c.get("/api/now/table/cmdb_ci")
        assert r.status_code == 403 and r.json()["error"]["message"] == "User Not Authorized"
        assert c.get("/api/now/table/cmdb_rel_ci").status_code == 403
    with owner() as o:
        row = o.execute("SELECT decision, action, detail->>'api' FROM audit.security_event "
                        "WHERE principal = 'dev:sn-yetkisiz' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ("deny", "read:cmdb_rel_ci", "sn_table")


def test_fields_above_sensitivity_are_absent_and_not_queryable(world):
    role = _role("test_sn_public", [("cmdb_ci", "public", "allow")])
    with make_client((role,), "sn-kamu") as c:
        r = c.get(f"/api/now/table/cmdb_ci/{world['lin1']}")
        assert r.status_code == 200
        assert set(r.json()["result"]) <= {"sys_id", "sys_class_name", "sys_created_on", "sys_updated_on",
                                           "sys_created_by", "sys_updated_by", "sys_mod_count"}
        # Görünmeyen alan süzgeçte de kullanılamaz: değer sezdirilemez
        q = c.get("/api/now/table/cmdb_ci", params={"sysparm_query": f"name={world['tag']}-a"})
        assert q.status_code == 400 and "görme yetkiniz yok" in q.json()["error"]["detail"]
        assert "name" not in c.get("/api/now/table/cmdb_ci", params={"sysparm_fields": "name,sys_id",
                                                                    "sysparm_limit": "1"}).json()["result"][0]


def test_denied_subclass_is_hidden_from_parent_table_and_relations(world):
    role = _role("test_sn_no_linux", [("cmdb_ci", "internal", "allow"), ("cmdb_rel_ci", "internal", "allow"),
                                      ("cmdb_ci_linux_server", "internal", "deny")])
    with make_client((role,), "sn-linuxsuz") as c:
        r = c.get("/api/now/table/cmdb_ci_server", params={"sysparm_query": f"nameSTARTSWITH{world['tag']}"})
        assert [x["sys_id"] for x in r.json()["result"]] == [world["win"]]
        assert r.headers["X-Total-Count"] == "1"
        assert c.get(f"/api/now/table/cmdb_ci/{world['lin1']}").status_code == 404
        assert c.get("/api/now/table/cmdb_ci_linux_server").status_code == 403
        rels = c.get("/api/now/table/cmdb_rel_ci", params={"sysparm_query": f"parent={world['app']}"}).json()["result"]
        assert [x["sys_id"] for x in rels] == [world["rel_win"]]
        assert c.get(f"/api/now/table/cmdb_rel_ci/{world['rel_lin']}").status_code == 404


def test_entra_bearer_token_is_accepted_and_missing_auth_fails_in_servicenow_format(entra_app, world):  # noqa: F811
    r = entra_app.get("/api/now/table/cmdb_ci_server", params={"sysparm_query": f"nameSTARTSWITH{world['tag']}"})
    assert r.status_code == 401 and r.json()["status"] == "failure"
    r = entra_app.get("/api/now/table/cmdb_ci_server", params={"sysparm_query": f"nameSTARTSWITH{world['tag']}"},
                      headers={"authorization": f"Bearer {access_token()}"})
    assert r.status_code == 200 and r.headers["X-Total-Count"] == "3"
    assert r.json()["result"][0]["manufacturer"]["link"].startswith("https://evren.ornek.test/api/now/table/")
    bad = entra_app.get("/api/now/table/cmdb_ci", headers={"authorization": f"Bearer {access_token(aud='api://baska')}"})
    assert bad.status_code == 401 and bad.json()["error"]["message"]
