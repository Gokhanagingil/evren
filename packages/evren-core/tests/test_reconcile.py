import copy
from datetime import timedelta

import pytest

from evren_core.metadata import validate
from evren_core.reconcile import Reconciler, SourceValues
from evren_core.sources import SourceRegistry, SourceRegistryError


def make(metadata, data_sources=None):
    doc = {"source_roles": metadata.source_roles, "data_sources": data_sources or metadata.data_sources}
    return Reconciler.from_metadata(metadata.hierarchy, metadata.reconciliation, SourceRegistry.from_metadata(doc))


def with_new_discovery_product(metadata, *, new_lifecycle, old_lifecycle="active", new_rank=2):
    """BMC'nin yerine 'Lansweeper' adlı başka bir keşif ürünü devreye alınıyor (yalnızca kaynak kaydı değişir)."""
    sources = copy.deepcopy(metadata.data_sources)
    for s in sources:
        if s["name"] == "BMC":
            s["lifecycle"] = old_lifecycle
    sources.append({"name": "Lansweeper", "label_tr": "Örnek yeni keşif ürünü", "role": "Discovery",
                    "role_rank": new_rank, "lifecycle": new_lifecycle, "connector": "lansweeper", "mappings": []})
    return sources


def test_priority_wins_for_hardware_resources(metadata, now):
    r = make(metadata)
    d = r.reconcile("cmdb_ci_linux_server", [
        SourceValues("BMC", {"ram": 16000, "cpu_count": 4}, now - timedelta(hours=2)),
        SourceValues("vCenter", {"ram": 16384, "cpu_count": 4}, now - timedelta(hours=6)),
        SourceValues("Zabbix", {"ram": 15900}, now),
    ], now)
    assert d["ram"].value == 16384 and d["ram"].data_source == "vCenter"
    assert ("BMC", 16000) in d["ram"].overridden


def test_data_refresh_rule_lets_fresher_lower_priority_source_win(metadata, now):
    r = make(metadata)
    d = r.reconcile("cmdb_ci_win_server", [
        SourceValues("vCenter", {"ram": 8192}, now - timedelta(days=10)),
        SourceValues("BMC", {"ram": 16384}, now - timedelta(days=1)),
    ], now)
    assert d["ram"].data_source == "BMC"
    assert "Veri tazeleme" in d["ram"].reason


def test_discovery_tools_cannot_overwrite_governance_attributes(metadata, now):
    r = make(metadata)
    d = r.reconcile("cmdb_ci_server", [
        SourceValues("Zabbix", {"support_group": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, now),
        SourceValues("ServiceNow", {"support_group": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, now - timedelta(days=30)),
    ], now)
    assert d["support_group"].data_source == "ServiceNow"
    assert d["support_group"].unauthorized == ("Zabbix",)


def test_manual_value_wins_over_servicenow_for_governance(metadata, now):
    r = make(metadata)
    d = r.reconcile("cmdb_ci_server", [
        SourceValues("Manual", {"owned_by": "cccccccccccccccccccccccccccccccc"}, now - timedelta(days=90)),
        SourceValues("ServiceNow", {"owned_by": "dddddddddddddddddddddddddddddddd"}, now),
    ], now)
    assert d["owned_by"].data_source == "Manual"


def test_manual_is_fallback_for_discovery_owned_attributes(metadata, now):
    r = make(metadata)
    only_manual = r.reconcile("cmdb_ci_server", [SourceValues("Manual", {"name": "elle-girilen"}, now)], now)
    assert only_manual["name"].value == "elle-girilen"
    both = r.reconcile("cmdb_ci_server", [
        SourceValues("Manual", {"name": "elle-girilen"}, now),
        SourceValues("Zabbix", {"name": "kesfedilen"}, now - timedelta(days=1)),
    ], now)
    assert both["name"].value == "kesfedilen"


def test_child_class_rule_overrides_parent_rule(metadata, now):
    r = make(metadata)
    # cmdb_ci_computer'da name için BMC 100; cmdb_ci_netgear'da NNMi 100 — ağ cihazında NNMi kazanır
    d = r.reconcile("cmdb_ci_ip_switch", [
        SourceValues("Zabbix", {"name": "sw-z"}, now),
        SourceValues("NNMi", {"name": "sw-n"}, now - timedelta(hours=1)),
    ], now)
    assert d["name"].value == "sw-n"
    assert r.authorized("cmdb_ci_ip_switch", "name") == {"Network": 100, "Monitoring": 300, "ITSM": 400, "Manual": 900}
    assert [s for s, *_ in r.authorized_sources("cmdb_ci_ip_switch", "name")] == ["NNMi", "Zabbix", "ServiceNow", "Manual"]


def test_no_rule_means_latest_observation_and_nulls_never_erase(metadata, now):
    r = make(metadata)
    d = r.reconcile("cmdb_ci_server", [
        SourceValues("Zabbix", {"firmware_version": None, "default_gateway": "10.0.0.1"}, now - timedelta(days=2)),
        SourceValues("Defender", {"default_gateway": "10.0.0.254"}, now),
    ], now)
    assert d["default_gateway"].value == "10.0.0.254"
    assert "firmware_version" not in d


# --------------------------------------------------------------------------- ürün bağımsızlığı (ADR-0009)


def test_rules_name_roles_not_products(metadata):
    roles = {r["name"] for r in metadata.source_roles}
    products = {s["name"] for s in metadata.data_sources} - roles
    for rule in metadata.reconciliation["rules"]:
        assert not set(rule["priorities"]) & products, rule["description"]
    for d in metadata.reconciliation["data_refresh"]:
        assert d["role"] in roles


def test_decision_reports_role_and_reason(metadata, now):
    d = make(metadata).reconcile("cmdb_ci_linux_server", [SourceValues("BMC", {"os": "RHEL 9"}, now)], now)
    assert d["os"].role == "Discovery"
    assert "BMC (Discovery)" in d["os"].reason


def test_shadow_product_never_writes_but_is_reported(metadata, now):
    r = make(metadata, with_new_discovery_product(metadata, new_lifecycle="shadow"))
    d = r.reconcile("cmdb_ci_linux_server", [
        SourceValues("BMC", {"os": "Red Hat Enterprise Linux 9.4"}, now - timedelta(days=1)),
        SourceValues("Lansweeper", {"os": "RHEL 9.4", "serial_number": "XYZ"}, now),
    ], now)
    assert d["os"].data_source == "BMC"
    assert d["os"].shadow == (("Lansweeper", "RHEL 9.4"),)
    assert "serial_number" not in d  # gölge kaynak tek başına değer üretemez
    assert not r.registry.may_create_ci("Lansweeper")


def test_product_swap_keeps_rules_and_new_product_takes_over(metadata, now):
    # Devir: yeni ürün active (rank 1), eski ürün retiring (rank 2) — kurallar dosyasına dokunulmadı
    sources = with_new_discovery_product(metadata, new_lifecycle="active", old_lifecycle="retiring", new_rank=1)
    for s in sources:
        if s["name"] == "BMC":
            s["role_rank"] = 2
    r = make(metadata, sources)
    d = r.reconcile("cmdb_ci_linux_server", [
        SourceValues("BMC", {"os": "eski"}, now),
        SourceValues("Lansweeper", {"os": "yeni"}, now - timedelta(hours=3)),
        SourceValues("Zabbix", {"os": "izleme"}, now),
    ], now)
    assert d["os"].value == "yeni" and d["os"].role == "Discovery"
    assert ("BMC", "eski") in d["os"].overridden
    assert r.registry.writes_attributes("BMC") and not r.registry.may_create_ci("BMC")


def test_retired_product_is_ignored(metadata, now):
    sources = with_new_discovery_product(metadata, new_lifecycle="active", old_lifecycle="retired", new_rank=1)
    r = make(metadata, sources)
    d = r.reconcile("cmdb_ci_linux_server", [SourceValues("BMC", {"os": "eski"}, now)], now)
    assert d == {}
    assert not r.registry.counts_for_staleness("BMC")


def test_change_records_never_write_attributes(metadata, now):
    d = make(metadata).reconcile("cmdb_ci_server", [SourceValues("ServiceNowChange", {"comments": "x"}, now)], now)
    assert d == {}


def test_ambiguous_rank_in_same_role_is_rejected(metadata):
    sources = with_new_discovery_product(metadata, new_lifecycle="active", new_rank=1)
    errors = SourceRegistry.from_metadata({"source_roles": metadata.source_roles, "data_sources": sources}).validate()
    assert any("aynı role_rank" in e for e in errors)


def test_unknown_source_or_role_is_explicit(metadata, now):
    with pytest.raises(SourceRegistryError):
        make(metadata).reconcile("cmdb_ci_server", [SourceValues("Bilinmeyen", {"name": "x"}, now)], now)
    md = copy.deepcopy(metadata)
    md.reconciliation["rules"][2]["priorities"] = {"BMC": 100}
    md.__post_init__()
    errors = "\n".join(validate(md).errors)
    assert "kaynak rolü tanımsız BMC (ürün/veri kaynağı adı değil, kaynak rolü yazılır)" in errors
