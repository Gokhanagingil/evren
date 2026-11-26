"""Elle düzenleme (PATCH /api/v1/ci/{id}): alan sözlükten çözülür, Manual kaynak kaydı olarak uzlaştırılır."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client
from .scenarios import apply_pending_edits, make_ci


def _key() -> str:
    return f"t-{uuid.uuid4()}"


@pytest.fixture(scope="module")
def editor():
    with make_client(("evren_reader", "evren_cmdb_editor"), "duzenleyici") as c:
        yield c


def _group(client) -> str:
    items = client.get("/api/v1/ref/sys_user_group?limit=5").json()["items"]
    assert items, "demo referans verisi yüklü olmalı (tools/load_demo_pg.py)"
    return items[0]["sys_id"]


def test_detail_lists_editable_fields_by_permission(editor, reader):
    ci, _ = make_ci()
    doc = editor.get(f"/api/v1/ci/{ci}").json()
    assert doc["can_edit"] is True
    fields = {f["attribute"]: f for f in doc["editable"]}
    assert fields["support_group"]["governed"] is True and fields["support_group"]["reference"] == "sys_user_group"
    assert fields["used_for"]["choices"] and fields["used_for"]["governed"] is True
    assert fields["os"]["governed"] is False and fields["os"]["authority"][0] == "Discovery"
    assert "last_discovered" not in fields and "discovery_source" not in fields
    # U4.3: maliyet merkezi yönetişim alanıdır; sahiplik onayı yalnızca onay akışıyla değişir
    assert fields["cost_center"]["governed"] is True and fields["cost_center"]["reference"] == "cmn_cost_center"
    assert not {"attested", "attested_by", "attested_date"} & set(fields)
    stage = fields["life_cycle_stage_status"]["choices"]
    assert {"value": "In Use", "label": "Kullanımda"} in stage
    ro = reader.get(f"/api/v1/ci/{ci}").json()
    assert ro["can_edit"] is False and ro["editable"] == []


def test_edit_end_to_end_with_role_priority_and_idempotency(editor):
    ci, _ = make_ci()
    group = _group(editor)
    body = {"attributes": {"used_for": "Production", "support_group": group, "os": "Elle"}, "note": "Envanter",
            "idempotency_key": _key()}
    assert editor.patch(f"/api/v1/ci/{ci}", json=body).status_code == 403  # CSRF başlığı yok
    r = editor.patch(f"/api/v1/ci/{ci}", json=body, headers=CSRF)
    assert r.status_code == 202, r.text
    assert r.json()["attributes"] == ["os", "support_group", "used_for"] and r.json()["replayed"] is False
    again = editor.patch(f"/api/v1/ci/{ci}", json=body, headers=CSRF)
    assert again.status_code == 202 and again.json()["replayed"] is True
    other = editor.patch(f"/api/v1/ci/{ci}", json={**body, "note": "başka"}, headers=CSRF)
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_cakismasi"
    assert editor.get(f"/api/v1/ci/{ci}").json()["edits"][0]["status"] == "pending"

    assert apply_pending_edits() == ["2 alan uygulandı; 1 alan daha öncelikli kaynakta; elle değer yedek olarak saklandı"]
    doc = editor.get(f"/api/v1/ci/{ci}").json()
    attrs = {a["attribute"]: a for a in doc["attributes"]}
    assert attrs["used_for"]["source"] == "Manual" and attrs["support_group"]["source"] == "Manual"
    assert attrs["os"]["value"] == "RHEL 9.4" and attrs["os"]["source"] == "BMC"
    edit = doc["edits"][0]
    assert edit["status"] == "applied" and edit["result"]["os"] == {"status": "overridden", "source": "BMC"}
    fields = {f["attribute"]: f for f in doc["editable"]}
    assert fields["os"]["manual_value"] == "Elle"  # yedek değer görünür
    hist = editor.get(f"/api/v1/ci/{ci}/history").json()["items"]
    assert any(h["attribute"] == "used_for" and h["reason"] == "manual" and h["actor"] == "dev:duzenleyici"
               for h in hist)


@pytest.mark.parametrize(("attributes", "fragment"), [
    ({"uydurma_alan": "x"}, "Sözlükte"),
    ({"last_discovered": "2026-11-25T00:00:00+00:00"}, "Elle yazılamaz"),
    ({"used_for": "Uydurma"}, "Seçim listesinde"),
    ({"support_group": "f" * 32}, "bulunamadı"),
    ({"support_group": "../etc"}, "32 haneli"),
    ({"short_description": "x" * 1001}, "En fazla 1000"),
    ({"short_description": 5}, "Metin bekleniyor"),
    ({"name; DROP TABLE x": "y"}, "Sözlükte"),
])
def test_edit_values_are_validated_against_dictionary(editor, attributes, fragment):
    ci, _ = make_ci()
    r = editor.patch(f"/api/v1/ci/{ci}", json={"attributes": attributes, "idempotency_key": _key()}, headers=CSRF)
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "gecersiz_alan" and fragment in err["details"][0]["hata"]
    assert "DROP" not in r.text  # geçersiz alan adı yanıta yansıtılmaz


def test_field_without_manual_authority_is_rejected(editor):
    vms = editor.get("/api/v1/ci?class=cmdb_ci_vmware_instance&limit=1").json()["items"]
    assert vms, "demo verisinde VMware sanal makinesi olmalı"
    r = editor.patch(f"/api/v1/ci/{vms[0]['sys_id']}", json={"attributes": {"cpus": 4}, "idempotency_key": _key()},
                     headers=CSRF)
    assert r.status_code == 422 and "Elle yazılamaz" in r.json()["error"]["details"][0]["hata"]


def test_edit_requires_write_permission_and_deny_is_audited(reader, client):
    ci, _ = make_ci()
    body = {"attributes": {"used_for": "Test"}, "idempotency_key": _key()}
    for c in (reader, client):  # okuyucu ve eşleştirme analisti CI yazamaz
        r = c.patch(f"/api/v1/ci/{ci}", json=body, headers=CSRF)
        assert r.status_code == 403 and r.json()["error"]["code"] == "yetki_yok"
    with psycopg.connect() as conn:
        conn.execute("SET ROLE evren_auditor_ro")
        row = conn.execute("SELECT decision FROM audit.security_event WHERE principal = 'dev:salt-okuyucu' "
                           "AND action = 'write:cmdb_ci_linux_server' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ("deny",)


def test_edit_body_is_strict(editor):
    ci, _ = make_ci()
    for body in ({"attributes": {}, "idempotency_key": _key()},
                 {"attributes": {"used_for": "Test"}, "idempotency_key": _key(), "fazla": 1},
                 {"attributes": {f"a{i}": 1 for i in range(51)}, "idempotency_key": _key()}):
        r = editor.patch(f"/api/v1/ci/{ci}", json=body, headers=CSRF)
        assert r.status_code == 422 and r.json()["error"]["code"] == "gecersiz_istek"


def test_ref_search_is_whitelisted_and_escapes_like(editor):
    assert editor.get("/api/v1/ref/sys_user_group").status_code == 200
    assert editor.get("/api/v1/ref/cmdb_ci").status_code == 404
    assert editor.get("/api/v1/ref/sys_user_group?q=%25").json()["items"] == []  # '%' joker değil, düz metin
    assert editor.get(f"/api/v1/ref/sys_user_group?q={'x' * 101}").status_code == 422


def test_manual_link_cannot_be_split_and_does_not_count_as_source(editor, client):
    """Elle giriş kaydı ayrılmaz; CI'da yalnızca elle giriş kalacaksa keşif kaydı da 'tek kaynak' sayılır."""
    ci, _ = make_ci()
    r = editor.patch(f"/api/v1/ci/{ci}", json={"attributes": {"used_for": "Test"}, "idempotency_key": _key()},
                     headers=CSRF)
    assert r.status_code == 202
    apply_pending_edits()
    links = {lk["data_source"]: lk for lk in client.get(f"/api/v1/ci/{ci}").json()["links"]}
    assert links["Manual"]["split"]["allowed"] is False and "Elle giriş" in links["Manual"]["split"]["reason"]
    assert links["BMC"]["split"]["allowed"] is False and links["BMC"]["split"]["reason"] == "CI'ın tek kaynak kaydı ayrılamaz."
    r = client.post(f"/api/v1/ci/{ci}/split", json={"link_id": links["Manual"]["id"], "idempotency_key": _key()},
                    headers=CSRF)
    assert r.status_code == 422 and r.json()["error"]["code"] == "elle_giris_bagi"


def test_summary_reports_pending_requests_and_worker_state(editor):
    """Genel Bakış göstergesi: bekleyen insan istekleri ve uzlaştırıcı nabzı (göç 0009)."""
    from evren_core.metadata import load_metadata
    from evren_reconciler.worker import Worker, WorkerConfig

    from .conftest import REPO

    ci, _ = make_ci()
    before = editor.get("/api/v1/summary").json()["worker"]
    r = editor.patch(f"/api/v1/ci/{ci}", json={"attributes": {"used_for": "Test"}, "idempotency_key": _key()},
                     headers=CSRF)
    assert r.status_code == 202
    pending = editor.get("/api/v1/summary").json()["worker"]
    assert pending["pending"]["edits"] == before["pending"]["edits"] + 1
    assert pending["pending"]["total"] >= 1

    worker = Worker(WorkerConfig(conninfo="", poll_seconds=30, worker_id=f"api-testi-{uuid.uuid4().hex[:6]}"),
                    load_metadata(REPO / "metadata"))
    with worker._open() as conn:
        worker.run_once(conn)  # tek tur: istekleri uygular ve nabız yazar (durdurulmadı → çalışıyor sayılır)
    after = editor.get("/api/v1/summary").json()["worker"]
    assert after["state"] == "ok" and after["pending"]["edits"] == 0 and after["last_seen"]


def test_cost_center_ref_search_and_lifecycle_value_validation(editor):
    items = editor.get("/api/v1/ref/cmn_cost_center", params={"q": "MM-110"}).json()["items"]
    assert items and items[0]["name"].startswith("MM-110")
    ci, _ = make_ci()
    bad = editor.patch(f"/api/v1/ci/{ci}", json={"attributes": {"life_cycle_stage_status": "Kullanımda"},
                                                 "idempotency_key": _key()}, headers=CSRF)
    assert bad.status_code == 422  # değer listede yok (etiket değil, değer gönderilir)
    ok = editor.patch(f"/api/v1/ci/{ci}", json={"attributes": {"life_cycle_stage": "Operational",
                                                               "life_cycle_stage_status": "In Use",
                                                               "cost_center": items[0]["sys_id"]},
                                                "idempotency_key": _key()}, headers=CSRF)
    assert ok.status_code == 202, ok.text
