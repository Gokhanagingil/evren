"""Kimlik çözümleme senaryoları — 11. isterin kabul testleri."""
from evren_core.identity import Outcome, SourceRecord, extract_keys, jaro_winkler, score


def rec(source, key, cls="cmdb_ci_server", lookup=None, external=None, **attrs):
    return SourceRecord(source, key, cls, attrs, lookup or {}, external or {})


def test_same_host_named_differently_in_three_sources_becomes_one_ci(engine):
    """abc_1 / abc-1 / abc_1@local → tek CI (kullanıcının örneği)."""
    zabbix = engine.process(rec("Zabbix", "10501", name="abc-1", ip_address="10.0.0.5"))
    bmc = engine.process(rec("BMC", "h-77", "cmdb_ci_win_server", name="abc_1", serial_number="CZ1234ABCD",
                             os="Microsoft Windows Server 2019"))
    defender = engine.process(rec("Defender", "m-1", "cmdb_ci_win_server", name="abc_1@local",
                                  ip_address="10.0.0.5", os="Windows Server 2019",
                                  external={"aad_device_id": "aaaa"}))
    assert zabbix.outcome is Outcome.NEW
    assert bmc.outcome is Outcome.RULE_MATCH and bmc.ci_sys_id == zabbix.ci_sys_id
    assert defender.outcome is Outcome.RULE_MATCH and defender.ci_sys_id == zabbix.ci_sys_id
    profile = engine.resolver.index.profile(zabbix.ci_sys_id)
    assert profile.class_name == "cmdb_ci_win_server"  # genel sınıf daha özele yükseltildi


def test_serial_number_has_priority_over_name(engine):
    a = engine.process(rec("BMC", "h-1", name="db-prod-01", serial_number="SGH123XYZ"))
    b = engine.process(rec("NNMi", "n-1", name="tamamen-farkli-ad", serial_number="sgh123xyz"))
    assert b.outcome is Outcome.RULE_MATCH and b.ci_sys_id == a.ci_sys_id
    assert "serial_number" in b.rule  # seri numarası girdisi (öncelik 100), ad değil


def test_name_match_with_conflicting_serial_goes_to_review(engine):
    engine.process(rec("BMC", "h-1", name="app-01", serial_number="AAA111"))
    r = engine.process(rec("Zabbix", "z-1", name="app-01", serial_number="BBB222"))
    assert r.outcome is Outcome.REVIEW
    assert any(f.code == "serial_conflict" for f in r.features)


def test_numbered_hostnames_are_not_merged(engine):
    engine.process(rec("BMC", "h-1", name="srv-db-01"))
    r = engine.process(rec("Zabbix", "z-2", name="srv-db-02"))
    assert r.outcome is Outcome.NEW


def test_same_short_name_in_different_domains_needs_review(engine):
    engine.process(rec("BMC", "h-1", name="web01.prod.sirket.local"))
    r = engine.process(rec("Defender", "d-1", name="web01.test.sirket.local"))
    assert r.outcome is Outcome.REVIEW
    assert any(f.code == "fqdn_conflict" for f in r.features)


def test_sibling_class_conflict_goes_to_review(engine):
    engine.process(rec("BMC", "h-1", "cmdb_ci_linux_server", name="app-02", os="Red Hat Enterprise Linux 9"))
    r = engine.process(rec("Defender", "d-2", "cmdb_ci_win_server", name="app-02", os="Windows Server 2022"))
    assert r.outcome is Outcome.REVIEW
    codes = {f.code for f in r.features}
    assert {"os_conflict", "class_conflict"} <= codes


def test_vmware_serial_links_guest_os_to_vcenter_guest_record(engine):
    vc = engine.process(rec("vCenter", "vm-uuid-1", name="ignored-vm-label", bios_uuid="421A2B3C-4D5E-6F70-8192-A3B4C5D6E7F8"))
    bmc = engine.process(rec("BMC", "h-9", name="guest-host",
                             serial_number="VMware-42 1a 2b 3c 4d 5e 6f 70-81 92 a3 b4 c5 d6 e7 f8"))
    assert bmc.outcome is Outcome.RULE_MATCH and bmc.ci_sys_id == vc.ci_sys_id
    assert "150" in bmc.rule  # BIOS UUID girdisi


def test_byte_swapped_uuid_still_matches(engine):
    a = engine.process(rec("vCenter", "vm-2", name="x1", bios_uuid="421a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"))
    b = engine.process(rec("BMC", "h-2", name="x1-guest", bios_uuid="3c2b1a42-5e4d-706f-8192-a3b4c5d6e7f8"))
    assert b.ci_sys_id == a.ci_sys_id


def test_hsrp_shared_mac_does_not_merge_two_routers(engine):
    a = engine.process(rec("NNMi", "r1", "cmdb_ci_ip_router", name="core-rtr-a", mac_address="00:00:0c:07:ac:01",
                           serial_number="FOC1111A"))
    b = engine.process(rec("NNMi", "r2", "cmdb_ci_ip_router", name="core-rtr-b", mac_address="00:00:0c:07:ac:01",
                           serial_number="FOC2222B"))
    assert a.ci_sys_id != b.ci_sys_id


def test_sticky_link_and_identity_change_detection(engine):
    first = engine.process(rec("vCenter", "vm-7", name="batch-01", bios_uuid="11111111-2222-3333-4444-555555555555"))
    again = engine.process(rec("vCenter", "vm-7", name="batch-01", bios_uuid="11111111-2222-3333-4444-555555555555"))
    assert again.outcome is Outcome.LINKED and again.ci_sys_id == first.ci_sys_id
    rebuilt = engine.process(rec("vCenter", "vm-7", name="batch-01", bios_uuid="99999999-2222-3333-4444-555555555555"))
    assert rebuilt.outcome is Outcome.REVIEW
    assert any(f.code == "uuid_conflict" for f in rebuilt.features)


def test_compact_name_only_goes_to_review_not_auto_merge(engine):
    engine.process(rec("BMC", "h-1", name="abc1", ip_address="10.9.9.9"))
    r = engine.process(rec("Zabbix", "z-9", name="abc-1", ip_address="10.9.9.9"))
    assert r.outcome is Outcome.REVIEW
    assert r.probability is not None and 0.35 <= r.probability < 0.97


def test_records_without_identity_are_parked(engine):
    r = engine.process(rec("Zabbix", "z-x", name="localhost", ip_address="10.1.1.1"))
    assert r.outcome is Outcome.INSUFFICIENT


def test_dependent_classes_are_not_matched_globally(engine):
    r = engine.process(rec("BMC", "app-1", "cmdb_ci_appl", name="nginx"))
    assert r.outcome is Outcome.INSUFFICIENT
    assert "Bağımlı" in r.reasons[0]


def test_explanations_are_in_turkish_and_scored():
    a = extract_keys(rec("A", "1", serial_number="SGH123XYZ", name="db01"))
    b = extract_keys(rec("B", "2", serial_number="SGH123XYZ", name="db01"))
    result = score(a, b)
    assert result.probability > 0.999
    assert any("Seri numarası eşleşti" in f.detail for f in result.features)


def test_jaro_winkler_reference_values():
    assert round(jaro_winkler("martha", "marhta"), 3) == 0.961
    assert jaro_winkler("abc", "abc") == 1.0
    assert jaro_winkler("", "abc") == 0.0
