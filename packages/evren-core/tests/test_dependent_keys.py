"""Bağımlı CI kimliği (F1-07, ADR-0029): yerel anahtar, kural ayrıştırma ve eşleme dosyasındaki ana kayıt."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from evren_core.connectors.mapping import FieldMapping, MappingError
from evren_core.identity import RuleSet, dependent_keys

NOW = datetime(2026, 11, 26, tzinfo=UTC)


@pytest.fixture(scope="module")
def rules(metadata):
    return RuleSet.from_metadata(metadata.identification, metadata.hierarchy)


def test_rules_keep_their_dependency(rules):
    nic = rules.for_class("cmdb_ci_network_adapter")
    assert not nic.independent and nic.parent_reference == "cmdb_ci" and nic.parent_relation is None
    appl = rules.for_class("cmdb_ci_db_mssql_instance")  # uygulamadan kalıtılır
    assert appl.parent_relation == ("Runs on::Runs", "cmdb_ci_hardware") and appl.parent_reference is None
    assert rules.for_class("cmdb_ci_linux_server").independent


def test_keys_are_normalised_and_skip_missing_attributes(rules):
    nic = rules.for_class("cmdb_ci_network_adapter")
    assert dependent_keys(nic, {"mac_address": "00-50-56-AB-CD-EF"}) == [("mac_address", "00:50:56:ab:cd:ef")]
    assert dependent_keys(nic, {"mac_address": "02:00:00:00:00:01"}) == [("mac_address", "02:00:00:00:00:01")]
    assert dependent_keys(nic, {"name": "eth0"}) == []
    appl = rules.for_class("cmdb_ci_appl")
    assert dependent_keys(appl, {"name": " Nginx "}) == [("name", "nginx")]
    assert dependent_keys(appl, {"name": "İçerik Yönetimi"}) == [("name", "icerik yonetimi")]


def test_mapping_parent_is_carried_and_required():
    m = FieldMapping.from_dict({"data_source": "NNMi", "source_key": "_key", "class": "cmdb_ci_network_adapter",
                                "parent": {"source_key": {"from": "_node"}},
                                "attributes": {"mac_address": {"from": "mac"}}})
    rec = m.apply({"_key": "n1/if/1", "_node": "n1", "mac": "AA:BB:CC:00:00:01"}, run_id=uuid.uuid4(), observed_at=NOW)
    assert rec.parent is not None and (rec.parent.source_key, rec.parent.data_source) == ("n1", None)
    with pytest.raises(MappingError, match="ana kayıt"):
        m.apply({"_key": "n1/if/2", "mac": "AA:BB:CC:00:00:02"}, run_id=uuid.uuid4(), observed_at=NOW)
