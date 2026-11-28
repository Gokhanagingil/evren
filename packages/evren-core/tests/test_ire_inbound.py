"""IRE uyumlu alım dönüşümü (U9.3b, ADR-0045): IRE yükü → alım kayıtları; olumsuz senaryolar ve yanıt biçimi."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evren_core.ire_inbound import IreInputError, parse_payload, response
from evren_core.metadata import load_metadata

MD = load_metadata(Path(__file__).resolve().parents[3] / "metadata")
NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
RUN = uuid.UUID("11111111-2222-4333-8444-555555555555")


def item(key="vm-1", cls="cmdb_ci_linux_server", **values):
    return {"className": cls, "values": {"name": "app-01", "serial_number": "SN-1", **values},
            "sys_object_source_info": {"source_name": "SCCM", "source_native_key": key,
                                       "source_recency_timestamp": "2026-11-28 11:30:00"}}


def parse(doc, **kw):
    return parse_payload(doc, md=MD, data_source="SCCM", run_id=RUN, now=NOW, max_items=kw.get("max_items", 50))


def test_items_lookup_and_relations_become_ingest_records():
    app = {"className": "cmdb_ci_appl", "values": {"name": "Ödeme"},
           "sys_object_source_info": {"source_native_key": "app-9"}}
    srv = item()
    srv["lookup"] = [{"className": "cmdb_serial_number", "values": {"serial_number": "SN-2", "valid": "true"}},
                     {"className": "cmdb_ci_network_adapter", "values": {"mac_address": "00:50:56:aa:bb:cc",
                                                                         "ip_address": "10.9.0.5"}},
                     {"className": "cmdb_ci_storage_device", "values": {"name": "disk"}}]
    p = parse({"items": [srv, app], "relations": [{"parent": 1, "child": 0, "type": "Runs on::Runs"}]})
    s, a = p.records
    assert (s.source_key, s.class_hint, s.data_source, s.run_id) == ("vm-1", "cmdb_ci_linux_server", "SCCM", RUN)
    assert s.attributes == {"name": "app-01", "serial_number": "SN-1"}
    assert s.observed_at == datetime(2026, 11, 28, 11, 30, tzinfo=UTC)
    assert s.lookup.serial_numbers == ["SN-2"] and s.lookup.mac_addresses == ["00:50:56:aa:bb:cc"]
    assert s.lookup.ip_addresses == ["10.9.0.5"]
    assert any("cmdb_ci_storage_device" in w for w in p.items[0].warnings)
    assert a.observed_at == NOW  # zaman yoksa şimdi
    rel = a.relations[0]
    assert (rel.type, rel.direction, rel.target_source_key, rel.target_class_hint) == (
        "Runs on::Runs", "parent", "vm-1", "cmdb_ci_linux_server")
    # Uygulama bağımlı sınıf (ADR-0029): ana CI, kuraldaki ilişkiyle eşleşen sunucudur
    assert (a.parent.source_key, a.parent.data_source) == ("vm-1", "SCCM") and s.parent is None
    doc = response(p, operation="QUEUED", run_id=RUN, status_path="/ingest/v1/runs/x")["result"]
    assert [i["operation"] for i in doc["items"]] == ["QUEUED", "QUEUED"]
    assert doc["items"][0]["sysId"] == "Unknown" and doc["items"][1]["inputIndices"] == [1]
    assert doc["relations"][0] == {"className": "Runs on::Runs", "operation": "QUEUED", "parent": 1, "child": 0,
                                   "inputIndices": [0], "errors": []}
    assert doc["evren"]["accepted"] == 2 and doc["evren"]["run_id"] == str(RUN)


def test_unknown_and_system_fields_are_dropped_with_warnings():
    p = parse({"items": [item(sys_id="a" * 32, u_bilinmeyen="x", manufacturer="Dell")]})
    attrs = p.records[0].attributes
    assert "sys_id" not in attrs and "u_bilinmeyen" not in attrs
    assert attrs["manufacturer"] == "Dell"  # geçer, ama uyarılır: referans sys_id bekler
    w = " | ".join(p.items[0].warnings)
    assert "sys_id: sistem alanı" in w and "u_bilinmeyen" in w and "manufacturer: referans alanı" in w


@pytest.mark.parametrize(("doc", "where"), [
    ([], "gövde"),
    ({"items": []}, "items"),
    ({"items": [item()], "fazla": 1}, "fazla"),
    ({"items": [item(cls="sys_user")]}, "className"),
    ({"items": [item(cls="yok_sinif")]}, "className"),
    ({"items": [{"className": "cmdb_ci_linux_server", "values": {"name": "x"}}]}, "source_native_key"),
    ({"items": [item(key="  ")]}, "source_native_key"),
    ({"items": [item(), item()]}, "aynı"),
    ({"items": [item(name={"display_value": "x"})]}, "tek değer"),
    ({"items": [{**item(), "sys_object_source_info": {"source_name": "Zabbix", "source_native_key": "k"}}]},
     "source_name"),
    ({"items": [{**item(), "sys_object_source_info": {"source_native_key": "k",
                                                      "source_recency_timestamp": "2099-01-01 00:00:00"}}]}, "gelecekte"),
    ({"items": [{**item(), "sys_object_source_info": {"source_native_key": "k",
                                                      "source_recency_timestamp": "dün"}}]}, "biçiminde"),
    ({"items": [item("a"), item("b")], "relations": [{"parent": 0, "child": 0, "type": "Runs on::Runs"}]}, "farklı"),
    ({"items": [item("a"), item("b")], "relations": [{"parent": 0, "child": 5, "type": "Runs on::Runs"}]}, "farklı"),
    ({"items": [item("a"), item("b")], "relations": [{"parent": 0, "child": 1, "type": "Uydurma::Tip"}]}, "ilişki tipi"),
])
def test_invalid_payload_is_rejected_whole_without_echoing_values(doc, where):
    with pytest.raises(IreInputError) as e:
        parse(doc)
    assert where in e.value.detail
    assert "display_value" not in e.value.detail  # girdi değeri yansıtılmaz


def test_item_limit_and_unsupported_parts():
    with pytest.raises(IreInputError) as e:
        parse({"items": [item(f"k{i}") for i in range(3)]}, max_items=2)
    assert "en çok 2" in e.value.detail
    p = parse({"items": [{**item(), "related": [{"className": "cmdb_ci_file_system"}]}],
               "referenceItems": [{"className": "core_company"}]})
    assert any("related" in w for w in p.items[0].warnings) and p.warnings == ["referenceItems bu sürümde işlenmez"]
    q = response(p, operation="VALIDATED", run_id=None, status_path=None)["result"]
    assert q["items"][0]["operation"] == "VALIDATED" and q["evren"]["accepted"] == 0


def test_dependent_item_without_its_parent_relation_is_accepted_with_a_warning():
    app = {"className": "cmdb_ci_appl", "values": {"name": "Ödeme"}, "sys_object_source_info": {"source_native_key": "a"}}
    p = parse({"items": [app]})
    assert p.records[0].parent is None
    assert any("bağımlı sınıf" in w and "Runs on::Runs" in w for w in p.items[0].warnings)
