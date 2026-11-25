"""Metadata değişiklik setleri (F1-11, ADR-0015): işlemler, kurallar, doğrulama, dosya biçimi."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evren_core.changeset import ChangeError, apply_changes, describe, load_change_files, parse_changes, render_change_file
from evren_core.metadata import load_metadata

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def md():
    return load_metadata(REPO / "metadata")


def _apply(md, *items):
    return apply_changes(md, parse_changes(items))


def test_add_field_is_jsonb_custom_and_inherited(md):
    out = _apply(md, {"op": "add_field", "table": "cmdb_ci_server", "element": "u_backup_policy",
                      "payload": {"type": "string", "label_tr": "Yedekleme politikası", "max_length": 100}})
    f = out.fields_of("cmdb_ci_linux_server")["u_backup_policy"]
    assert f["storage"] == "jsonb" and f["sensitivity"] == "internal" and f["_table"] == "cmdb_ci_server"
    assert "u_backup_policy" not in md.fields_of("cmdb_ci_server")  # girdi değişmez


@pytest.mark.parametrize(("item", "fragment"), [
    ({"op": "add_field", "table": "cmdb_ci_server", "element": "backup", "payload": {"type": "string", "label_tr": "x"}},
     "u_ ile başlamalı"),
    ({"op": "add_field", "table": "cmdb_ci_yok", "element": "u_x", "payload": {"type": "string", "label_tr": "x"}},
     "sınıf tanımsız"),
    ({"op": "add_field", "table": "cmdb_ci_server", "element": "u_x", "payload": {"type": "blob", "label_tr": "x"}},
     "tipi geçersiz"),
    ({"op": "add_field", "table": "cmdb_ci_server", "element": "u_x", "payload": {"type": "reference", "label_tr": "x",
                                                                                  "reference": "cmdb_ci"}},
     "referans tablosu tanımsız"),
    ({"op": "modify_field", "table": "cmdb_ci_linux_server", "element": "name", "payload": {"label_tr": "x"}},
     "kalıtılan alan"),
    ({"op": "modify_field", "table": "cmdb_ci", "element": "name", "payload": {"max_length": 10}}, "yalnızca artırılabilir"),
    ({"op": "modify_field", "table": "cmdb_ci", "element": "name", "payload": {"type": "integer"}}, "yalnızca etiket"),
    ({"op": "deprecate_field", "table": "cmdb_ci", "element": "name", "payload": {}}, "fiziksel kolon"),
    ({"op": "add_choice", "table": "cmdb_ci", "element": "name", "payload": {"value": "x", "label_tr": "x"}},
     "seçim listeli bir alan değil"),
    ({"op": "add_choice", "table": "cmdb_ci", "element": "used_for", "payload": {"value": "Production", "label_tr": "x"}},
     "zaten var"),
    ({"op": "add_data_source", "table": "ingest.data_source", "element": "BMC", "payload": {"role": "Discovery",
                                                                                          "label_tr": "x"}},
     "zaten var"),
    ({"op": "set_source_lifecycle", "table": "ingest.data_source", "element": "BMC", "payload": {"lifecycle": "retired"}},
     "geçişine izin yok"),
    ({"op": "add_class", "table": "u_cmdb_ci_kiosk", "element": None, "payload": {"parent": "cmdb_ci_yok", "label_tr": "x"}},
     "üst sınıf tanımsız"),
])
def test_invalid_changes_are_rejected_with_turkish_reason(md, item, fragment):
    with pytest.raises(ChangeError) as exc:
        _apply(md, item)
    assert any(fragment in e for e in exc.value.errors), exc.value.errors


def test_all_errors_are_reported_together(md):
    with pytest.raises(ChangeError) as exc:
        _apply(md, {"op": "add_field", "table": "cmdb_ci_server", "element": "bad", "payload": {}},
               {"op": "set_source_lifecycle", "table": "ingest.data_source", "element": "Yok", "payload": {}})
    assert len(exc.value.errors) == 2 and exc.value.errors[0].startswith("1. işlem")


def test_product_swap_through_change_sets(md):
    """ADR-0009 akışı: yeni ürün gölge eklenir, sonra etkin + devir, sonra eskisi emekli."""
    a = _apply(md, {"op": "add_data_source", "table": "ingest.data_source", "element": "KesifB",
                    "payload": {"role": "Discovery", "label_tr": "Keşif-B", "connector": "kesif_b"}})
    assert a.sources.source("KesifB").lifecycle == "shadow" and not a.sources.may_create_ci("KesifB")
    # Etkin ürün doğrudan emekli olamaz; aynı rolde aynı sırada iki yazan kaynak olamaz
    with pytest.raises(ChangeError):
        _apply(a, {"op": "set_source_lifecycle", "table": "ingest.data_source", "element": "KesifB",
                   "payload": {"lifecycle": "active", "role_rank": 1}})
    b = _apply(a, {"op": "set_source_lifecycle", "table": "ingest.data_source", "element": "BMC",
                   "payload": {"lifecycle": "retiring", "role_rank": 2}},
               {"op": "set_source_lifecycle", "table": "ingest.data_source", "element": "KesifB",
                "payload": {"lifecycle": "active", "role_rank": 1}})
    assert b.sources.may_create_ci("KesifB") and not b.sources.may_create_ci("BMC")
    c = _apply(b, {"op": "set_source_lifecycle", "table": "ingest.data_source", "element": "BMC",
                   "payload": {"lifecycle": "retired"}})
    assert not c.sources.writes_attributes("BMC")
    assert c.reconciliation == md.reconciliation  # kurallar değişmedi


def test_add_class_then_field_and_choice(md):
    out = _apply(md,
                 {"op": "add_class", "table": "u_cmdb_ci_kiosk", "payload": {"parent": "cmdb_ci_hardware",
                                                                             "label_tr": "Kiosk"}},
                 {"op": "add_field", "table": "u_cmdb_ci_kiosk", "element": "u_screen", "payload": {
                     "type": "string", "label_tr": "Ekran", "choice": True}},
                 {"op": "add_choice", "table": "u_cmdb_ci_kiosk", "element": "u_screen",
                  "payload": {"value": "touch", "label_tr": "Dokunmatik"}})
    assert out.hierarchy.compatible("u_cmdb_ci_kiosk", "cmdb_ci_hardware")
    assert "serial_number" in out.fields_of("u_cmdb_ci_kiosk")


def test_change_file_roundtrip_and_load_order(md, tmp_path):
    base = REPO / "metadata"
    work = tmp_path / "metadata"
    work.mkdir()
    for f in base.glob("*.yaml"):
        (work / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    changes = parse_changes([{"op": "add_field", "table": "cmdb_ci_server", "element": "u_backup_policy",
                              "payload": {"type": "string", "label_tr": "Yedekleme politikası"}}])
    text = render_change_file({"id": "a" * 32, "name": "Yedekleme alanı", "prepared_by": "u1", "approved_by": "u2",
                               "approved_on": "2026-11-25T10:00:00+00:00", "published_on": "2026-11-25T11:00:00+00:00"},
                              changes)
    assert text.startswith("# Evren değişiklik seti: Yedekleme alanı") and describe(changes[0]) in text
    (work / "changes").mkdir()
    (work / "changes" / "202611251100-aaaaaaaa.yaml").write_text(text, encoding="utf-8")
    assert yaml.safe_load(text)["approved_by"] == "u2"
    loaded = load_metadata(work)
    assert "u_backup_policy" in loaded.fields_of("cmdb_ci_server")
    assert "u_backup_policy" not in load_metadata(work, apply_change_files=False).fields_of("cmdb_ci_server")
    # Aynı dosyanın ikinci kez uygulanması açık hata verir (alan zaten var)
    (work / "changes" / "202611251200-bbbbbbbb.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ChangeError, match=r"202611251200-bbbbbbbb\.yaml"):
        load_metadata(work)
    assert [n for n, _ in load_change_files(work / "changes")] == ["202611251100-aaaaaaaa.yaml", "202611251200-bbbbbbbb.yaml"]
