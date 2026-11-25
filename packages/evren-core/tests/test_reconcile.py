from datetime import timedelta

from evren_core.reconcile import Reconciler, SourceValues


def make(metadata):
    return Reconciler.from_metadata(metadata.hierarchy, metadata.reconciliation)


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
    assert r.authorized("cmdb_ci_ip_switch", "name") == {"NNMi": 100, "Zabbix": 300, "ServiceNow": 400, "Manual": 900}


def test_no_rule_means_latest_observation_and_nulls_never_erase(metadata, now):
    r = make(metadata)
    d = r.reconcile("cmdb_ci_server", [
        SourceValues("Zabbix", {"firmware_version": None, "default_gateway": "10.0.0.1"}, now - timedelta(days=2)),
        SourceValues("Defender", {"default_gateway": "10.0.0.254"}, now),
    ], now)
    assert d["default_gateway"].value == "10.0.0.254"
    assert "firmware_version" not in d
