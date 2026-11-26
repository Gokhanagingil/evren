"""Metadata değişiklik setleri (F1-11, ADR-0015): taslak → onay → yayın, görevler ayrılığı, çakışma, dosya."""
from __future__ import annotations

import hashlib
import shutil
import uuid

import psycopg
import pytest
import yaml

from evren_core.metadata import load_metadata

from .conftest import CSRF, REPO, make_client


@pytest.fixture(scope="module")
def designer():
    with make_client(("evren_reader", "evren_schema_designer"), "tasarimci") as c:
        yield c


@pytest.fixture(scope="module")
def client_without_roles():
    with make_client(("evren_iam_admin",), "rolsuz") as c:  # yalnız kimlik yönetimi: CMDB okuyamaz
        yield c


@pytest.fixture(scope="module")
def approver():
    with make_client(("evren_reader", "evren_schema_approver"), "onayci") as c:
        yield c


def _key() -> str:
    return f"t-{uuid.uuid4()}"


def _field_set(element: str, name: str = "Yedekleme alanı") -> dict:
    return {"name": name, "description": "Sunucuların yedekleme politikası", "idempotency_key": _key(),
            "changes": [{"op": "add_field", "table": "cmdb_ci_server", "element": element,
                         "payload": {"type": "string", "label_tr": "Yedekleme politikası", "max_length": 100}}]}


def test_catalog_lists_classes_sources_and_capabilities(designer, approver):
    cat = designer.get("/api/v1/schema/catalog").json()
    assert cat["can_draft"] is True and cat["can_approve"] is False
    assert approver.get("/api/v1/schema/catalog").json()["can_approve"] is True
    server = next(c for c in cat["classes"] if c["name"] == "cmdb_ci_server")
    root = next(c for c in cat["classes"] if c["name"] == "cmdb_ci")
    assert server["parent"] == "cmdb_ci_computer" and any(f["name"] == "name" for f in root["fields"])
    bmc = next(s for s in cat["sources"] if s["name"] == "BMC")
    assert bmc["lifecycle"] == "active" and [t["value"] for t in bmc["transitions"]] == ["retiring"]


def test_data_model_view_matches_static_file_and_counts_readable_cis(designer, approver):
    """U4.4: API ile demo modunun statik dosyası aynı görünümü verir; CI sayıları yalnız okunabilir sınıflarda."""
    import json

    doc = designer.get("/api/v1/schema/model").json()
    static = json.loads((REPO / "apps/web/content/model.json").read_text(encoding="utf-8"))
    for key in ("stats", "classes", "choices", "relationship_rules", "backlog"):
        assert doc[key] == static[key], key
    assert doc["can_draft"] is True and approver.get("/api/v1/schema/model").json()["can_draft"] is False
    assert doc["ci_counts"].get("cmdb_ci_linux_server", 0) > 0  # demo envanteri yüklü
    assert all(n > 0 for n in doc["ci_counts"].values())


def test_data_model_view_requires_read(client_without_roles):
    r = client_without_roles.get("/api/v1/schema/model")
    assert r.status_code == 403 and r.json()["error"]["code"] == "yetki_yok"


def test_full_flow_draft_review_approve_publish_and_artifact_applies(designer, approver, tmp_path):
    element = f"u_backup_{uuid.uuid4().hex[:6]}"
    body = _field_set(element)
    assert designer.post("/api/v1/schema/change-sets", json=body).status_code == 403  # CSRF yok
    r = designer.post("/api/v1/schema/change-sets", json=body, headers=CSRF)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    again = designer.post("/api/v1/schema/change-sets", json=body, headers=CSRF)
    assert again.json()["replayed"] is True and again.json()["id"] == sid

    assert approver.post(f"/api/v1/schema/change-sets/{sid}/approve", json={}, headers=CSRF).status_code == 409  # taslak
    sub = designer.post(f"/api/v1/schema/change-sets/{sid}/submit", headers=CSRF)
    assert sub.status_code == 200 and sub.json()["state"] == "in_review"
    assert sub.json()["impact"][0]["ci_count"] >= 1 and "DDL yok" in sub.json()["impact"][0]["notes"][0]
    assert designer.post(f"/api/v1/schema/change-sets/{sid}/approve", json={}, headers=CSRF).status_code == 403

    ok = approver.post(f"/api/v1/schema/change-sets/{sid}/approve", json={"note": "Uygun"}, headers=CSRF)
    assert ok.status_code == 200 and ok.json()["state"] == "approved" and ok.json()["approved_by"] == "dev:onayci"
    pub = approver.post(f"/api/v1/schema/change-sets/{sid}/publish", headers=CSRF)
    assert pub.status_code == 200 and pub.json()["state"] == "published", pub.text
    assert approver.post(f"/api/v1/schema/change-sets/{sid}/reject", json={}, headers=CSRF).status_code == 409

    art = designer.get(f"/api/v1/schema/change-sets/{sid}/artifact")
    assert art.status_code == 200 and hashlib.sha256(art.content).hexdigest() == art.headers["x-evren-content-sha256"]
    name = art.headers["content-disposition"].split('filename="')[1].rstrip('"')
    doc = yaml.safe_load(art.content)
    assert doc["prepared_by"] == "dev:tasarimci" and doc["approved_by"] == "dev:onayci"
    assert doc["changes"][0]["element"] == element and art.text.startswith("# Evren değişiklik seti:")

    # Dosya repoya eklenince yükleyici uygular (dağıtım simülasyonu)
    work = tmp_path / "metadata"
    shutil.copytree(REPO / "metadata", work)
    (work / "changes" / name).write_bytes(art.content)
    assert element in load_metadata(work).fields_of("cmdb_ci_linux_server")


def test_conflicting_set_is_blocked_by_published_but_not_deployed_set(designer, approver):
    element = f"u_conflict_{uuid.uuid4().hex[:6]}"
    first = designer.post("/api/v1/schema/change-sets", json=_field_set(element, "İlk"), headers=CSRF).json()["id"]
    designer.post(f"/api/v1/schema/change-sets/{first}/submit", headers=CSRF)
    approver.post(f"/api/v1/schema/change-sets/{first}/approve", json={}, headers=CSRF)
    assert approver.post(f"/api/v1/schema/change-sets/{first}/publish", headers=CSRF).status_code == 200
    r = designer.post("/api/v1/schema/change-sets", json=_field_set(element, "İkinci"), headers=CSRF)
    assert r.status_code == 422 and any("zaten kullanılıyor" in d["hata"] for d in r.json()["error"]["details"])


def test_preparer_with_both_roles_cannot_approve_and_db_enforces(designer):
    with make_client(("evren_reader", "evren_schema_designer", "evren_schema_approver"), "iki-rollu") as both:
        sid = both.post("/api/v1/schema/change-sets", json=_field_set(f"u_sod_{uuid.uuid4().hex[:6]}"),
                        headers=CSRF).json()["id"]
        both.post(f"/api/v1/schema/change-sets/{sid}/submit", headers=CSRF)
        r = both.post(f"/api/v1/schema/change-sets/{sid}/approve", json={}, headers=CSRF)
        assert r.status_code == 403 and r.json()["error"]["code"] == "hazirlayan_onaylayamaz"
        assert both.get(f"/api/v1/schema/change-sets/{sid}").json()["can_approve"] is False
    with psycopg.connect(autocommit=True) as conn:  # API'yi atlayan yazım da reddedilir
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE meta.schema_change_set SET state = 'approved', approved_by = created_by, "
                         "approved_on = now() WHERE sys_id = %s", (sid,))
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE meta.schema_change_set SET state = 'published' WHERE sys_id = %s", (sid,))  # in_review → published
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM meta.schema_change_set WHERE sys_id = %s", (sid,))
        with pytest.raises(psycopg.Error):  # işlem yalnız taslağa eklenir
            conn.execute("INSERT INTO meta.schema_change (change_set, sequence, operation, target_table) "
                         "VALUES (%s, 99, 'add_field', 'cmdb_ci')", (sid,))
        conn.execute("SET ROLE evren_app_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE meta.schema_change_set SET created_by = 'x'")


@pytest.mark.parametrize(("change", "fragment"), [
    ({"op": "set_source_lifecycle", "table": "ingest.data_source", "element": "BMC", "payload": {"lifecycle": "retired"}},
     "geçişine izin yok"),
    ({"op": "add_field", "table": "cmdb_ci_server", "element": "backup", "payload": {"type": "string", "label_tr": "x"}},
     "u_ ile başlamalı"),
    ({"op": "uydurma", "table": "x", "payload": {}}, "tanımsız işlem"),
])
def test_invalid_sets_are_rejected_with_reasons(designer, change, fragment):
    r = designer.post("/api/v1/schema/change-sets", json={"name": "Hatalı set", "changes": [change],
                                                          "idempotency_key": _key()}, headers=CSRF)
    assert r.status_code == 422 and any(fragment in d["hata"] for d in r.json()["error"]["details"]), r.text


def test_product_swap_set_and_permissions(designer, reader):
    body = {"name": "Keşif-B gölge mod", "idempotency_key": _key(), "changes": [
        {"op": "add_data_source", "table": "ingest.data_source", "element": f"KesifB{uuid.uuid4().hex[:4]}",
         "payload": {"role": "Discovery", "label_tr": "Keşif-B", "connector": "kesif_b"}}]}
    r = designer.post("/api/v1/schema/change-sets", json=body, headers=CSRF)
    assert r.status_code == 201
    sub = designer.post(f"/api/v1/schema/change-sets/{r.json()['id']}/submit", headers=CSRF).json()
    assert "Gölge kaynak değer yazmaz" in sub["impact"][0]["notes"][0]
    assert reader.post("/api/v1/schema/change-sets", json=_field_set("u_x"), headers=CSRF).status_code == 403
    assert any(i["id"] == r.json()["id"] for i in reader.get("/api/v1/schema/change-sets").json()["items"])
    assert designer.get("/api/v1/schema/change-sets/NOT-AN-ID").status_code == 400
