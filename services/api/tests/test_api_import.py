"""CSV ile toplu içe aktarma (U6.3, ADR-0026 §6): önizleme yazmaz; onay dosyayı yeniden doğrular, geçerli satırlar toplu
iş altında oluşturma isteği olur; uzlaştırıcı satırları sırayla kimlik çözümlemesinden geçirir. Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client
from .scenarios import apply_pending_creates


@pytest.fixture(scope="module")
def editor():
    with make_client(("evren_reader", "evren_cmdb_editor"), "csv-aktaran") as c:
        yield c


def csv_text(tag: str) -> str:
    # Türkçe başlıklar, noktalı virgül (Türkçe Excel), BOM; referans ve seçim değeri adla; dosya içi mükerrer seri
    return ("﻿Ad;Seri numarası;Destek grubu;Kullanım amacı;Bilinmeyen sütun\r\n"
            f"csv-srv-{tag}-1;CSV{tag}A;Sunucu Operasyon;Üretim;x\r\n"
            f"csv-srv-{tag}-2;CSV{tag}B;;Test;\r\n"
            f"=HYPERLINK(\"http://ornek\");CSV{tag}C;;;\r\n"
            f";CSV{tag}D;;;\r\n"
            f"csv-srv-{tag}-5;CSV{tag}E;Böyle Grup Yok;;\r\n"
            f"csv-srv-{tag}-6;CSV{tag}A;;;\r\n")


def body(text, **kw):
    return {"sys_class_name": "cmdb_ci_linux_server", "csv": text, "file_name": "sunucular.csv", **kw}


def test_preview_maps_turkish_headers_and_validates_rows_without_writing(editor):
    tag = uuid.uuid4().hex[:6].upper()
    with psycopg.connect(autocommit=True) as c:
        before = c.execute("SELECT count(*) FROM ingest.manual_create").fetchone()[0]
    res = editor.post("/api/v1/imports/preview", json=body(csv_text(tag)), headers=CSRF)
    assert res.status_code == 200
    doc = res.json()
    assert doc["delimiter"] == "noktalı virgül" and doc["total"] == 6
    assert [(c["header"], c["field"], c["status"]) for c in doc["columns"]] == [
        ("Ad", "name", "ok"), ("Seri numarası", "serial_number", "ok"), ("Destek grubu", "support_group", "ok"),
        ("Kullanım amacı", "used_for", "ok"), ("Bilinmeyen sütun", None, "unknown")]
    errors = {r["row_no"]: r["errors"] for r in doc["rows"]}
    assert errors[1] == [] and errors[2] == [] and errors[6] == []  # dosya içi mükerrer uzlaştırıcıda yakalanır
    assert "formül olabilecek değer" in errors[3][0]
    assert errors[4] == ["Ad boş"]
    assert "bulunamadı" in errors[5][0]
    assert (doc["valid_count"], doc["invalid_count"]) == (3, 3)
    with psycopg.connect(autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM ingest.manual_create").fetchone()[0] == before  # önizleme yazmaz


def test_commit_creates_batch_rows_in_order_and_reconciler_reports_each(editor, reader):
    tag = uuid.uuid4().hex[:6].upper()
    payload = body(csv_text(tag), idempotency_key=f"test-{uuid.uuid4().hex}", note="İlk envanter aktarımı")
    assert editor.post("/api/v1/imports", json={**payload, "idempotency_key": None}, headers=CSRF).status_code == 422
    res = editor.post("/api/v1/imports", json=payload, headers=CSRF)
    assert res.status_code == 202
    batch = res.json()["id"]
    assert (res.json()["row_count"], res.json()["skipped_count"]) == (3, 3)
    assert editor.post("/api/v1/imports", json=payload, headers=CSRF).json()["id"] == batch  # tekrar güvenli
    st = reader.get(f"/api/v1/imports/{batch}").json()
    assert st["counts"]["pending"] == 3 and not st["done"] and [r["row_no"] for r in st["rows"]] == [1, 2, 6]
    with psycopg.connect(autocommit=True) as c:
        (sg,) = c.execute("SELECT attributes->>'support_group' FROM ingest.manual_create "
                          "WHERE batch_id = %s AND row_no = 1", (batch,)).fetchone()
        assert sg == c.execute("SELECT sys_id FROM ref.sys_user_group WHERE name = 'Sunucu Operasyon'").fetchone()[0]
        assert c.execute("SELECT attributes->>'used_for' FROM ingest.manual_create WHERE batch_id = %s AND row_no = 1",
                         (batch,)).fetchone()[0] == "Production"  # seçim etiketi değerine çevrildi
    apply_pending_creates()
    st = reader.get(f"/api/v1/imports/{batch}").json()
    assert st["done"] and st["counts"]["created"] == 2 and st["counts"]["duplicate"] == 1
    dup = reader.get(f"/api/v1/imports/{batch}", params={"status": "duplicate"}).json()["rows"]
    assert [(r["row_no"], r["result_ci_name"]) for r in dup] == [(6, f"csv-srv-{tag}-1")]  # ilk satır açıldı
    with psycopg.connect(autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM audit.security_event WHERE action = 'ci_import' AND resource = %s",
                         (f"import_batch:{batch}",)).fetchone()[0] == 1


@pytest.mark.parametrize(("text", "code"), [
    ("Ad\n", "satir_yok"),
    ("Seri numarası\nX1\n", "ad_sutunu_yok"),
    ("   ", "bos_dosya"),
    ("Ad\n" + "\n".join(f"r{i}" for i in range(5001)) + "\n", "cok_satir"),
    ('Ad,Seri\n"kapanmayan,1\n', "csv_bozuk"),
])
def test_format_errors(editor, text, code):
    res = editor.post("/api/v1/imports/preview", json=body(text), headers=CSRF)
    assert res.status_code == 422 and res.json()["error"]["code"] == code


def test_permissions_class_column_and_body_limit(editor, reader):
    assert reader.post("/api/v1/imports/preview", json=body("Ad\nx\n"), headers=CSRF).status_code == 403
    doc = editor.post("/api/v1/imports/preview", json=body(
        "name,sys_class_name\nvirgul-1,cmdb_ci_win_server\nvirgul-2,cmdb_ci\nvirgul-3,yok_sinif\n"), headers=CSRF).json()
    assert doc["delimiter"] == "virgül" and doc["columns"][1]["status"] == "class"
    assert [(r["class"], r["valid"]) for r in doc["rows"]] == [("cmdb_ci_win_server", True), ("cmdb_ci", False),
                                                                ("yok_sinif", False)]
    big = editor.post("/api/v1/imports/preview", content=b"x" * (6 * 1024 * 1024),
                      headers={**CSRF, "content-type": "application/json"})
    assert big.status_code == 413 and big.json()["error"]["code"] == "istek_cok_buyuk"


def test_same_name_without_keys_needs_decision_with_candidate_link(editor, reader):
    # Kimlik anahtarı olmadan aynı ailede aynı ad: ikinci satır açılmaz, karar ister ve ilk satırın CI'ını aday gösterir
    tag = uuid.uuid4().hex[:6]
    text = f"Ad\ncsv-vm-{tag}\nCSV-VM-{tag}\n"
    res = editor.post("/api/v1/imports", json={"sys_class_name": "cmdb_ci_vm_instance", "csv": text,
                                               "idempotency_key": f"test-{uuid.uuid4().hex}"}, headers=CSRF)
    assert res.status_code == 202
    batch = res.json()["id"]
    apply_pending_creates()
    st = reader.get(f"/api/v1/imports/{batch}").json()
    assert (st["counts"]["created"], st["counts"]["needs_decision"]) == (1, 1)
    first, second = st["rows"]
    assert first["status"] == "created" and first["result_ci_name"] == f"csv-vm-{tag}"
    assert second["status"] == "needs_decision" and "'Yeni CI…' penceresinden" in second["result_note"]
    assert [c["sys_id"] for c in second["candidates"]] == [first["result_ci"]]
