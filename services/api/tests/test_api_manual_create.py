"""Elle CI oluşturma isteği (U6.1, ADR-0026): izin, doğrulama, CSRF, tekrar güvenliği, istek durumu.

API CI açmaz; isteği yazar. Uzlaştırıcının uygulaması uzlaştırıcı testlerinde (test_manual_create_pg) ve
tarayıcı testinde sınanır; burada sonuç satırı sahip rolüyle yazılarak durum yanıtı denetlenir.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client


@pytest.fixture(scope="module")
def editor():
    with make_client(("evren_reader", "evren_cmdb_editor"), "ci-olusturan") as c:
        yield c


def body(**kw):
    return {"sys_class_name": "cmdb_ci_linux_server", "attributes": {"name": "api-elle-01"},
            "idempotency_key": f"test-{uuid.uuid4().hex}", **kw}


def test_reader_cannot_create_and_does_not_see_the_button(reader):
    assert reader.get("/api/v1/me").json()["can"]["create_ci"] is False
    assert reader.get("/api/v1/meta/creatable-classes").json()["items"] == []
    assert reader.post("/api/v1/ci", json=body(), headers=CSRF).status_code == 403
    assert reader.get("/api/v1/meta/classes/cmdb_ci_linux_server/form").status_code == 403


def test_form_lists_writable_fields_identity_and_excludes_root(editor):
    assert editor.get("/api/v1/me").json()["can"]["create_ci"] is True
    classes = {c["name"] for c in editor.get("/api/v1/meta/creatable-classes").json()["items"]}
    assert "cmdb_ci_linux_server" in classes and "cmdb_ci_business_app" in classes and "cmdb_ci" not in classes
    form = editor.get("/api/v1/meta/classes/cmdb_ci_linux_server/form").json()
    names = {f["attribute"] for f in form["fields"]}
    assert form["required"] == ["name"] and {"name", "serial_number", "support_group", "used_for"} <= names
    assert form["identity"][:2] == ["serial_number", "fqdn"] and "attested" not in names  # onay alanı formda yok
    assert editor.get("/api/v1/meta/classes/cmdb_ci/form").status_code == 422
    assert editor.get("/api/v1/meta/classes/yok_boyle/form").status_code == 404


@pytest.mark.parametrize(("payload", "status", "code"), [
    (body(attributes={"short_description": "adsız"}), 422, "ad_zorunlu"),
    (body(attributes={"name": "x", "uydurma_alan": 1}), 422, "gecersiz_alan"),
    (body(attributes={"name": "x", "discovery_source": "Manual"}), 422, "gecersiz_alan"),  # sistem alanı
    (body(sys_class_name="cmdb_ci"), 422, "kok_sinif"),
    (body(sys_class_name="yok_boyle"), 422, "sinif_yok"),
    (body(force_new=True), 422, None),  # gerekçesiz zorlama
])
def test_validation_rejects_bad_requests(editor, payload, status, code):
    res = editor.post("/api/v1/ci", json=payload, headers=CSRF)
    assert res.status_code == status
    if code:
        assert res.json()["error"]["code"] == code


def test_create_request_is_written_idempotent_and_status_is_readable(editor, reader):
    payload = body(attributes={"name": "api-elle-02", "serial_number": "APIELLE02", "short_description": "Test"},
                   note="Keşif ağının dışında")
    assert editor.post("/api/v1/ci", json=payload).status_code == 403  # CSRF başlığı yok
    res = editor.post("/api/v1/ci", json=payload, headers=CSRF)
    assert res.status_code == 202
    doc = res.json()
    rid = doc["id"]
    assert doc["status"] == "pending" and doc["attributes"] == ["name", "serial_number", "short_description"]
    again = editor.post("/api/v1/ci", json=payload, headers=CSRF)
    assert again.status_code == 202 and again.json()["id"] == rid  # aynı anahtar: aynı istek, ikinci satır yok
    try:
        with psycopg.connect(autocommit=True) as c:
            assert c.execute("SELECT requested_by, attributes->>'name', note FROM ingest.manual_create "
                             "WHERE sys_id = %s", (rid,)).fetchone() == ("dev:ci-olusturan", "api-elle-02",
                                                                        "Keşif ağının dışında")
            assert c.execute("SELECT count(*) FROM audit.security_event WHERE action = 'ci_create' AND resource = %s",
                             (f"manual_create:{rid}",)).fetchone()[0] == 1
            existing = c.execute("SELECT sys_id, name, sys_class_name FROM cmdb.cmdb_ci "
                                 "WHERE sys_class_name LIKE 'cmdb_ci_%%server' ORDER BY sys_id LIMIT 1").fetchone()
        status = reader.get(f"/api/v1/ci-requests/{rid}").json()
        assert (status["status"], status["status_label"], status["name"]) == ("pending", "Uygulanmayı bekliyor",
                                                                              "api-elle-02")
        with psycopg.connect(autocommit=True) as c:  # uzlaştırıcının yazacağı sonucu taklit et
            c.execute("""UPDATE ingest.manual_create SET status = 'needs_decision', applied_at = now(),
                                result = %s, result_note = 'Benzer CI''lar var' WHERE sys_id = %s""",
                      (psycopg.types.json.Jsonb({"outcome": "review", "candidates": [
                          {"sys_id": existing[0], "name": existing[1], "class": existing[2], "probability": 0.7}]}),
                       rid))
        status = reader.get(f"/api/v1/ci-requests/{rid}").json()
        assert status["status"] == "needs_decision" and status["candidates"][0]["sys_id"] == existing[0]
        assert status["candidates"][0]["name"] == existing[1] and status["candidates"][0]["class_label"]
    finally:
        with psycopg.connect(autocommit=True) as c:
            c.execute("DELETE FROM ingest.manual_create WHERE sys_id = %s", (rid,))
    assert reader.get("/api/v1/ci-requests/yok").status_code == 400
    assert reader.get(f"/api/v1/ci-requests/{'0' * 32}").status_code == 404


def test_api_cannot_set_request_outcome():
    with psycopg.connect(autocommit=True) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SET ROLE evren_app_api; UPDATE ingest.manual_create SET status = 'created' WHERE false")
