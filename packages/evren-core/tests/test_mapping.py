"""Bağlayıcı eşleme dosyaları: gerçekçi (sentetik) kaynak yükleri → alım kaydı → tek CI."""
import json
import uuid
from pathlib import Path

import pytest
import yaml

from evren_core.connectors import FieldMapping, MappingError, resolve_path
from evren_core.identity import Outcome
from evren_core.identity.adapters import source_record_from_ingest

FIXTURES = Path(__file__).parent / "fixtures"
RUN = uuid.UUID("00000000-0000-4000-8000-000000000001")


def mapping(repo_root, rel):
    return FieldMapping.from_dict(yaml.safe_load((repo_root / "connectors" / rel).read_text(encoding="utf-8")))


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def all_mapping_files(repo_root):
    return sorted((repo_root / "connectors").rglob("*mapping.yaml"))


def test_every_mapping_file_parses_and_targets_known_classes_and_fields(repo_root, metadata):
    files = all_mapping_files(repo_root)
    assert len(files) >= 6
    known_sources = {s["name"] for s in metadata.data_sources}
    rel_types = {r["name"] for r in metadata.relationship_types}
    for f in files:
        m = FieldMapping.from_dict(yaml.safe_load(f.read_text(encoding="utf-8")))
        assert m.data_source in known_sources, f
        classes = [m.default_class] + [r.class_name for r in m.class_rules]
        for cls in classes:
            assert cls in metadata.hierarchy, f"{f}: {cls}"
        allowed = set(metadata.fields_of(m.default_class)) | {"bios_uuid", "vcenter_uuid", "correlation_id"}
        for cls in classes:
            allowed |= set(metadata.fields_of(cls))
        unknown = set(m.attributes) - allowed
        assert not unknown, f"{f}: sözlükte olmayan alanlar {unknown}"
        for rel in m.relations:
            assert rel["type"] in rel_types, f"{f}: {rel['type']}"


def test_short_hostname_transform():
    from evren_core.connectors.mapping import TRANSFORMS

    f = TRANSFORMS["short_hostname"]
    assert f("abc-1.corp.local") == "abc-1"
    assert f("10.20.30.41") == "10.20.30.41"
    assert f("ABC_1") == "ABC_1"


def test_resolve_path_supports_indices_and_wildcards():
    data = {"a": [{"b": 1}, {"b": 2}, {"c": 3}], "x": {"y": "z"}}
    assert resolve_path(data, "a[1].b") == 2
    assert resolve_path(data, "a[*].b") == [1, 2]
    assert resolve_path(data, "x.y") == "z"
    assert resolve_path(data, "x.missing") is None
    assert resolve_path(data, "a[9].b") is None


def test_zabbix_mapping(repo_root, now):
    rec = mapping(repo_root, "zabbix/mapping.yaml").apply(fixture("zabbix_host.json"), run_id=RUN, observed_at=now)
    assert rec.class_hint == "cmdb_ci_win_server"
    assert rec.attributes["name"] == "ABC_1"
    assert rec.attributes["serial_number"] == "CZ1234ABCD"
    assert "asset_tag" not in rec.attributes  # boş değer taşınmaz
    assert rec.lookup.ip_addresses == ["10.20.30.41", "10.20.31.41"]
    assert rec.lookup.mac_addresses == ["00-50-56-AB-CD-EF", "02-42-AC-11-00-02"]
    assert rec.external_ids == {"zabbix_hostid": "10501"}


def test_vcenter_produces_vm_instance_and_guest_server(repo_root, now):
    raw = fixture("vcenter_vm.json")
    vm = mapping(repo_root, "vcenter/vm_instance.mapping.yaml").apply(raw, run_id=RUN, observed_at=now)
    guest = mapping(repo_root, "vcenter/guest_server.mapping.yaml").apply(raw, run_id=RUN, observed_at=now)
    assert vm.class_hint == "cmdb_ci_vmware_instance"
    assert vm.attributes["state"] == "on" and vm.attributes["memory"] == 16384 and vm.attributes["template"] is False
    assert vm.relations[0].type == "Registered on::Has registered"
    assert guest.class_hint == "cmdb_ci_win_server"
    assert guest.attributes["virtual"] is True
    assert guest.attributes["name"] == "abc_1" and guest.attributes["fqdn"] == "abc_1.corp.sirket.local"
    assert guest.relations[0].type == "Virtualized by::Virtualizes"
    assert guest.relations[0].target_source_key == raw["config"]["instanceUuid"]


def test_vcenter_vm_and_guest_have_distinct_source_keys(repo_root, now):
    """(data_source, source_key) benzersizdir: aynı VM'den üretilen iki kayıt birbirini ezmemeli."""
    raw = fixture("vcenter_vm.json")
    vm = mapping(repo_root, "vcenter/vm_instance.mapping.yaml").apply(raw, run_id=RUN, observed_at=now)
    guest = mapping(repo_root, "vcenter/guest_server.mapping.yaml").apply(raw, run_id=RUN, observed_at=now)
    assert vm.source_key != guest.source_key
    assert guest.source_key == "guest:" + raw["config"]["instanceUuid"]
    assert guest.relations[0].target_source_key == vm.source_key


def test_servicenow_import_keeps_sys_id(repo_root, engine, now):
    raw = {"sys_id": "0123456789abcdef0123456789abcdef", "sys_class_name": "cmdb_ci_linux_server",
           "name": "ist-erp-01", "serial_number": "CZJ1234567", "used_for": "Production",
           "support_group": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "operational_status": "1"}
    rec = mapping(repo_root, "servicenow_cmdb/server.mapping.yaml").apply(raw, run_id=RUN, observed_at=now)
    assert rec.class_hint == "cmdb_ci_linux_server" and rec.attributes["operational_status"] == 1
    res = engine.import_existing(source_record_from_ingest(rec), raw["sys_id"])
    assert res.ci_sys_id == raw["sys_id"] and res.rule == "import"
    with pytest.raises(ValueError):
        engine.import_existing(source_record_from_ingest(rec), raw["sys_id"])


def test_vcenter_guest_skipped_without_tools(repo_root, now):
    raw = fixture("vcenter_vm.json")
    raw["guest"] = {}
    assert mapping(repo_root, "vcenter/guest_server.mapping.yaml").apply(raw, run_id=RUN, observed_at=now) is None


def test_defender_scope_excludes_workstations(repo_root, now):
    m = mapping(repo_root, "defender/mapping.yaml")
    assert m.apply(fixture("defender_workstation.json"), run_id=RUN, observed_at=now) is None
    server = m.apply(fixture("defender_machine.json"), run_id=RUN, observed_at=now)
    assert server.class_hint == "cmdb_ci_win_server"
    assert server.external_ids["aad_device_id"].startswith("80fe8ff8")


def test_nnmi_l3_switch_classified_as_switch(repo_root, now):
    rec = mapping(repo_root, "nnmi/mapping.yaml").apply(fixture("nnmi_node.json"), run_id=RUN, observed_at=now)
    assert rec.class_hint == "cmdb_ci_ip_switch"
    assert rec.attributes["name"] == "IST-DC1-CORE-SW01"


def test_bmc_discovery_host_mapping(repo_root, now):
    m = mapping(repo_root, "bmc_discovery/host.mapping.yaml")
    rec = m.apply(fixture("bmc_discovery_host.json"), run_id=RUN, observed_at=now)
    assert rec.data_source == "BMC"
    assert rec.class_hint == "cmdb_ci_win_server"
    assert rec.attributes["name"] == "ABC_1"
    assert rec.attributes["fqdn"] == "abc_1.corp.sirket.local"
    assert rec.attributes["ram"] == 16384 and rec.attributes["cpu_speed"] == 2500
    assert rec.attributes["virtual"] is True
    assert rec.lookup.mac_addresses == ["00:50:56:AB:CD:EF"]
    assert rec.external_ids == {"bmc_discovery_key": "a1b2c3d4e5f60718293a4b5c6d7e8f90/Host"}
    # Kapsam: masaüstü düğümleri sunucu CMDB'sine girmez
    assert m.apply(fixture("bmc_discovery_workstation.json"), run_id=RUN, observed_at=now) is None


def test_every_data_source_mapping_file_exists_and_matches(repo_root, metadata):
    """Kaynak kaydı (data_sources.yaml) ile eşleme dosyaları tutarlı: ürün değişiminde unutulan dosya kalmaz."""
    referenced = set()
    for src in metadata.data_sources:
        for rel in src.get("mappings") or []:
            path = repo_root / rel
            assert path.is_file(), f"{src['name']}: {rel} yok"
            m = FieldMapping.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert m.data_source == src["name"], f"{rel}: data_source {m.data_source} ≠ {src['name']}"
            assert rel.startswith(f"connectors/{src['connector']}/"), f"{rel}: bağlayıcı klasörü {src['connector']} değil"
            referenced.add(path.resolve())
    orphans = {p.resolve() for p in all_mapping_files(repo_root)} - referenced
    assert not orphans, f"Hiçbir veri kaynağına bağlı olmayan eşleme dosyaları: {sorted(map(str, orphans))}"


def test_end_to_end_four_sources_one_ci(repo_root, engine, now):
    """vCenter misafir sunucu + BMC Discovery + Zabbix + Defender aynı makineyi farklı adlarla bildirir → tek CI."""
    raw_vm = fixture("vcenter_vm.json")
    records = [
        mapping(repo_root, "vcenter/guest_server.mapping.yaml").apply(raw_vm, run_id=RUN, observed_at=now),
        mapping(repo_root, "bmc_discovery/host.mapping.yaml").apply(fixture("bmc_discovery_host.json"), run_id=RUN,
                                                                     observed_at=now),
        mapping(repo_root, "zabbix/mapping.yaml").apply(fixture("zabbix_host.json"), run_id=RUN, observed_at=now),
        mapping(repo_root, "defender/mapping.yaml").apply(fixture("defender_machine.json"), run_id=RUN, observed_at=now),
    ]
    results = [engine.process(source_record_from_ingest(r)) for r in records]
    assert results[0].outcome is Outcome.NEW
    assert all(r.ci_sys_id == results[0].ci_sys_id for r in results), [r.summary_tr for r in results]
    assert len(engine.resolver.index) == 1


def test_mapping_errors_are_explicit():
    with pytest.raises(MappingError):
        FieldMapping.from_dict({"data_source": "X", "class": "cmdb_ci"})
    with pytest.raises(MappingError):
        FieldMapping.from_dict({"data_source": "X", "source_key": "id", "class": "cmdb_ci", "atributes": {}})
    with pytest.raises(MappingError):
        FieldMapping.from_dict({"data_source": "X", "source_key": "id", "class": "cmdb_ci",
                                "attributes": {"name": {"from": "n", "transform": "rot13"}}})
    with pytest.raises(MappingError):
        FieldMapping.from_dict({"data_source": "X", "source_key": {"from": "id", "prefixx": "a:"}, "class": "cmdb_ci"})


def test_nnmi_model_oid_is_not_an_identity(repo_root, engine, now):
    """Aynı modeldeki iki switch: sysObjectID ortak, cihazlar farklı → asla birleştirilmez."""
    m = mapping(repo_root, "nnmi/mapping.yaml")
    a = fixture("nnmi_node.json")
    b = {**a, "uuid": "11111111-2222-4333-8444-555555555555", "name": "ist-dc1-core-sw02",
         "systemName": "IST-DC1-CORE-SW02", "longName": "ist-dc1-core-sw02.net.sirket.local",
         "managementAddress": "10.0.0.2", "serialNumber": "FDO99999ABC"}
    ra = engine.process(source_record_from_ingest(m.apply(a, run_id=RUN, observed_at=now)))
    rb = engine.process(source_record_from_ingest(m.apply(b, run_id=RUN, observed_at=now)))
    assert rb.outcome is Outcome.NEW and rb.ci_sys_id != ra.ci_sys_id
    assert not any(f.code == "external_id_match" for f in rb.features)


def test_zabbix_turkish_network_group_is_network_gear(repo_root, now):
    raw = {"hostid": "20001", "host": "IST-DC1-CORE-SW01", "name": "Çekirdek switch", "interfaces": [],
           "inventory": {}, "hostgroups": [{"groupid": "9", "name": "Ağ cihazları/Çekirdek"}]}
    rec = mapping(repo_root, "zabbix/mapping.yaml").apply(raw, run_id=RUN, observed_at=now)
    assert rec.class_hint == "cmdb_ci_netgear"


def test_ip_named_host_without_other_identity_does_not_create_ci(repo_root, engine, now):
    """Zabbix'te host adı olarak IP girilmiş, MAC/seri yok → yeni CI açılmaz, kayıt park edilir."""
    raw = {"hostid": "20002", "host": "10.20.30.99", "name": "10.20.30.99", "interfaces": [
        {"interfaceid": "1", "type": "1", "ip": "10.20.30.99", "main": "1"}], "inventory": {}, "hostgroups": []}
    rec = mapping(repo_root, "zabbix/mapping.yaml").apply(raw, run_id=RUN, observed_at=now)
    res = engine.process(source_record_from_ingest(rec))
    assert res.outcome is Outcome.INSUFFICIENT and res.ci_sys_id is None
    assert "yeni CI için" in res.summary_tr
    assert len(engine.resolver.index) == 0
