"""ServiceNow replikası (U9.1, ADR-0042): kapsam doğrulaması, IRE yükü, yanıt yorumu, istemci ve simülatör."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from evren_core.connectors.http import HttpClient, HttpError
from evren_core.replica import (
    CiSnapshot,
    IreClient,
    IreSimulator,
    ItemResult,
    ReplicaField,
    build_item,
    judge,
    parse_response,
    payload_hash,
    sn_value,
    validate_sn_sync,
)

FIELDS = [ReplicaField("name", "string"), ReplicaField("serial_number", "string"),
          ReplicaField("install_status", "integer"), ReplicaField("install_date", "glide_date_time"),
          ReplicaField("warranty_expiration", "glide_date"), ReplicaField("attested", "boolean"),
          ReplicaField("support_group", "reference", "sys_user_group"), ReplicaField("owned_by", "reference", "sys_user"),
          ReplicaField("discovery_source", "string"), ReplicaField("duplicate_of", "reference", "cmdb_ci")]
WHEN = datetime(2026, 11, 28, 9, 30, tzinfo=UTC)
SYS = "a" * 32


def snap(**values):
    return CiSnapshot(SYS, "cmdb_ci_linux_server", {"name": "web-01", **values}, WHEN)


def client(sim: IreSimulator, source: str = "Evren") -> IreClient:
    return IreClient(HttpClient("https://ornek.service-now.test", transport=sim, retries=0,
                                auth_header=HttpClient.basic("evren", "parola")), source)


# ------------------------------------------------------------------------------------------- kapsam
def test_scope_document_is_validated():
    ok = {"source_name": "Evren", "classes": [{"name": "cmdb_ci_server", "enabled": True}], "exclude_fields": ["name"]}
    assert validate_sn_sync(ok, classes={"cmdb_ci_server"}, fields={"name"}) == []
    bad = {"source_name": "1x", "classes": [{"name": "yok"}, {"name": "cmdb_ci_server"}, {"name": "cmdb_ci_server"}],
           "exclude_fields": ["olmayan"]}
    errors = validate_sn_sync(bad, classes={"cmdb_ci_server"}, fields={"name"})
    assert len(errors) == 4
    assert any("source_name" in e for e in errors) and any("iki kez" in e for e in errors)


def test_repository_scope_is_valid():
    from evren_core.metadata import load_metadata, validate

    md = load_metadata(Path(__file__).resolve().parents[3] / "metadata")
    assert md.sn_sync["source_name"] == "Evren"
    assert {c["name"] for c in md.sn_sync["classes"]} >= {"cmdb_ci_server", "cmdb_ci_netgear"}
    assert validate(md).ok


# ------------------------------------------------------------------------------------------- yük
def test_values_use_servicenow_formats():
    assert sn_value(datetime(2026, 11, 28, 12, 0, tzinfo=UTC), "glide_date_time") == "2026-11-28 12:00:00"
    assert sn_value(date(2027, 1, 31), "glide_date") == "2027-01-31"
    assert sn_value(True, "boolean") == "true" and sn_value("0", "boolean") == "false"
    assert sn_value(Decimal("12.50"), "decimal") == "12.5"
    assert sn_value(7, "integer") == "7"
    assert sn_value(None, "string") is None and sn_value("", "string") is None


def test_item_carries_source_identity_and_scoped_values_only():
    b = build_item(snap(serial_number="SN-1", install_status=1, install_date=WHEN, attested=True,
                        warranty_expiration=date(2027, 1, 1), discovery_source="BMC", duplicate_of="b" * 32,
                        comments="kapsam dışı"), FIELDS, source_name="Evren")
    item = b.item
    assert item["className"] == "cmdb_ci_linux_server" and item["internal_id"] == SYS
    assert item["sys_object_source_info"] == {"source_name": "Evren", "source_native_key": SYS,
                                              "source_recency_timestamp": "2026-11-28 09:30:00"}
    assert item["values"] == {"sys_class_name": "cmdb_ci_linux_server", "name": "web-01", "serial_number": "SN-1",
                              "install_status": "1", "install_date": "2026-11-28 09:30:00",
                              "warranty_expiration": "2027-01-01", "attested": "true"}
    # IRE'nin kendi tuttuğu kaynak alanları ve Evren'in iç birleştirme izi gönderilmez; boş alan ServiceNow'da silinmez
    assert "discovery_source" not in item["values"] and "duplicate_of" not in item["values"]


def test_references_without_servicenow_counterpart_are_skipped():
    group, person = "c" * 32, "d" * 32

    def ref_value(table, sys_id):
        return sys_id if table == "sys_user_group" else None     # kişi Evren'de doğmuş: ServiceNow'da yok

    b = build_item(snap(support_group=group, owned_by=person), FIELDS, source_name="Evren", ref_value=ref_value)
    assert b.item["values"]["support_group"] == group
    assert "owned_by" not in b.item["values"] and b.skipped == ("owned_by",)
    # Geçersiz sys_id de gönderilmez
    assert build_item(snap(support_group="x"), FIELDS, source_name="Evren").skipped == ("support_group",)


def test_ci_reference_is_translated_to_the_replica_sys_id():
    fields = [ReplicaField("host", "reference", "cmdb_ci_esx_server")]
    b = build_item(snap(host="e" * 32), fields, source_name="Evren",
                   ref_value=lambda t, v: "f" * 32 if t.startswith("cmdb_ci") else None)
    assert b.item["values"]["host"] == "f" * 32


def test_hash_ignores_recency_and_changes_with_values():
    a = build_item(snap(), FIELDS, source_name="Evren")
    later = CiSnapshot(SYS, "cmdb_ci_linux_server", {"name": "web-01"}, datetime(2027, 1, 1, tzinfo=UTC))
    assert build_item(later, FIELDS, source_name="Evren").hash == a.hash
    assert build_item(snap(name="web-02"), FIELDS, source_name="Evren").hash != a.hash
    assert payload_hash("x", {"a": "1", "b": "2"}) == payload_hash("x", {"b": "2", "a": "1"})


# ------------------------------------------------------------------------------------------- yanıt
DOC_EXAMPLE = {  # ServiceNow API başvurusundaki örnek yanıt (kısaltılmış)
    "result": {
        "items": [{"className": "cmdb_ci_linux_server", "operation": "UPDATE", "sysId": "539747cac0a801640163e60735fbbf6e",
                   "identifierEntrySysId": "556eb250c3400200d8d4bea192d3ae92",
                   "identificationAttempts": [{"identifierName": "Hardware Rule", "attemptResult": "MATCHED",
                                               "attributes": ["name"], "searchOnTable": "cmdb_ci_hardware"}],
                   "markers": [], "inputIndices": [0], "mergedPayloadIds": [], "errorCount": 0},
                  {"className": "cmdb_ci_ip_switch", "operation": "UPDATE", "sysId": "304481257f701200bee45f19befa915b",
                   "inputIndices": [1], "errorCount": 0}],
        "additionalCommittedItems": [],
        "relations": [{"className": "cmdb_rel_ci", "operation": "INSERT", "markers": [], "inputIndices": [0],
                       "mergedPayloadIds": [], "errorCount": 0}],
        "additionalCommittedRelations": [],
    }
}


def test_documented_response_is_parsed_by_input_index():
    items, rels = parse_response(DOC_EXAMPLE)
    assert items[0].operation == "UPDATE" and items[0].sys_id == "539747cac0a801640163e60735fbbf6e"
    assert items[1].class_name == "cmdb_ci_ip_switch"
    assert rels[0].operation == "INSERT" and not rels[0].errors


def test_unknown_sys_id_and_errors_are_reported():
    items, _ = parse_response({"result": {"items": [{"className": "cmdb_ci_linux_server", "operation": "NO_CHANGE",
                                                      "sysId": "Unknown", "inputIndices": [0], "errors": [
                                                          {"error": "MISSING_MATCHING_ATTRIBUTES", "message": "eksik"}]}]}})
    v = judge(items[0], None)
    assert v.state == "error" and "Kimlik kuralının istediği alanlar" in v.message and items[0].sys_id is None


@pytest.mark.parametrize(("result", "expected", "taken", "committed", "state"), [
    (ItemResult(0, "c", "UPDATE", SYS), SYS, False, True, "ok"),            # ServiceNow kökenli CI, aynı kayıt
    (ItemResult(0, "c", "UPDATE", "b" * 32), SYS, False, True, "conflict"),  # başka kayıt bulundu
    (ItemResult(0, "c", "INSERT", "b" * 32), None, False, True, "ok"),       # Evren'de doğdu: yeni kayıt
    (ItemResult(0, "c", "UPDATE", "b" * 32), None, False, True, "conflict"),  # Evren'de doğdu ama var olanla eşleşti
    (ItemResult(0, "c", "UPDATE", SYS), SYS, True, True, "conflict"),        # bulunan kayıt başka Evren CI'ına eşli
    (ItemResult(0, "c", "INSERT", None), None, False, False, "ok"),          # denemede yeni kaydın sys_id'si yok
    (ItemResult(0, "c", "INSERT", None), SYS, False, False, "conflict"),     # beklenen kayıt ServiceNow'da yok
    (None, SYS, False, True, "error"),
])
def test_verdict_compares_identification_with_expectation(result, expected, taken, committed, state):
    assert judge(result, expected, taken_by_other=taken, committed=committed).state == state


# ------------------------------------------------------------------------------------------- simülatör
def item(sys_id, name, serial=None, cls="cmdb_ci_linux_server"):
    return build_item(CiSnapshot(sys_id, cls, {"name": name, "serial_number": serial}, WHEN), FIELDS,
                      source_name="Evren").item


def test_query_does_not_write_and_commit_creates_source_identity():
    sim = IreSimulator()
    sim.seed(SYS, "cmdb_ci_linux_server", name="web-01")
    c = client(sim)
    res, _ = c.send([item(SYS, "web-01"), item("b" * 32, "yeni-01")], commit=False)
    assert res[0].operation == "NO_CHANGE" and res[0].sys_id == SYS     # ServiceNow'da aynı değerler
    assert res[1].operation == "INSERT" and "b" * 32 not in sim.cis and len(sim.cis) == 1 and sim.commits == 0
    res, _ = c.send([item(SYS, "web-01"), item("b" * 32, "yeni-01")], commit=True)
    new = res[1].sys_id
    assert new in sim.cis and sim.commits == 1
    # Ad değişse de sonraki itmede kaynak anahtarıyla aynı kayıt bulunur
    res, _ = c.send([item("b" * 32, "yeni-adi")], commit=True)
    assert res[0].sys_id == new and res[0].operation == "UPDATE"
    assert sim.calls == [("query", 2), ("commit", 2), ("commit", 1)]


def test_relations_are_inserted_once():
    sim = IreSimulator()
    c = client(sim)
    items = [item("1" * 32, "app-01", cls="cmdb_ci_appl"), item("2" * 32, "srv-01")]
    rel = [{"parent": 0, "child": 1, "type": "Runs on::Runs"}]
    _, rels = c.send(items, rel, commit=True)
    assert rels[0].operation == "INSERT"
    _, rels = c.send(items, rel, commit=True)
    assert rels[0].operation == "NO_CHANGE" and len(sim.relations) == 1


def test_duplicates_and_missing_identity_are_errors():
    sim = IreSimulator()
    sim.seed("1" * 32, "cmdb_ci_linux_server", name="a", serial_number="S1")
    sim.seed("2" * 32, "cmdb_ci_linux_server", name="b", serial_number="S1")
    res, _ = client(sim).send([item("3" * 32, "c", "S1"),
                               {"className": "cmdb_ci_linux_server", "values": {"sys_class_name": "x"}}], commit=False)
    assert res[0].errors[0][0] == "MULTIPLE_DUPLICATE_RECORDS"
    assert res[1].errors[0][0] == "MISSING_MATCHING_ATTRIBUTES"


def test_simulator_requires_auth_and_known_data_source():
    sim = IreSimulator()
    no_auth = IreClient(HttpClient("https://ornek.service-now.test", transport=sim, retries=0), "Evren")
    with pytest.raises(HttpError) as err:
        no_auth.send([item(SYS, "web-01")], commit=False)
    assert err.value.status == 401
    with pytest.raises(HttpError):
        client(sim, "Baska").send([item(SYS, "web-01")], commit=False)
    with pytest.raises(ValueError):
        IreClient(HttpClient("https://ornek.service-now.test", transport=sim), "<script>")


def test_request_body_is_json_with_items_and_relations():
    seen = {}

    class Spy(IreSimulator):
        def send(self, method, url, headers, body, timeout):
            seen.update(url=url, body=json.loads(body))
            return super().send(method, url, headers, body, timeout)

    client(Spy()).send([item(SYS, "web-01"), item("b" * 32, "db-01")], [{"parent": 0, "child": 1, "type": "Depends on::Used by"}],
                       commit=True)
    assert seen["url"].endswith("/api/now/identifyreconcile?sysparm_data_source=Evren")
    assert set(seen["body"]) == {"items", "relations"} and seen["body"]["relations"][0]["type"] == "Depends on::Used by"
