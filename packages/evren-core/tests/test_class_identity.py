"""U4.2 (ADR-0019): yeni sınıfların kimlik kuralları ve sınıfa özgü kimlik anahtarları (attr:<alan>)."""
from __future__ import annotations

import copy

import pytest

from evren_core.identity import Outcome, SourceRecord, extract_keys, score
from evren_core.identity.rules import ATTRIBUTE_KEY_TYPES, CLASS_KEY_ATTRIBUTES, CONTEXT_KEY_ATTRIBUTES
from evren_core.metadata import validate


def rec(source, key, cls, **attrs):
    return SourceRecord(source, key, cls, attrs)


# --- Anahtar çıkarımı ------------------------------------------------------------------------------------


def test_class_keys_are_extracted_and_normalized():
    keys = extract_keys(rec("Bulut", "a1", "cmdb_ci_cloud_service_account", name="Üretim Aboneliği",
                            account_id="  4F2A-99B1 "))
    assert keys.class_keys == {"account_id=4f2a-99b1"}
    assert keys.by_type()["attr:account_id"] == {"4f2a-99b1"}
    assert keys.can_create_ci


@pytest.mark.parametrize(("raw", "expected"), [
    ("10.1.2.5/24", "10.1.2.0/24"),
    ("10.1.2.0/24", "10.1.2.0/24"),
    ("2001:db8::1/64", "2001:db8::/64"),
])
def test_subnet_is_normalized_to_cidr(raw, expected):
    assert extract_keys(rec("Ağ", "n1", "cmdb_ci_ip_network", subnet=raw)).class_keys == {f"subnet={expected}"}


@pytest.mark.parametrize("raw", ["", "   ", "10.1.2.300/24", "alt-ağ", "x" * 300])
def test_invalid_class_key_values_are_ignored(raw):
    attr = "subnet" if raw.strip() and len(raw) < 300 else "account_id"
    assert extract_keys(rec("Ağ", "n1", "cmdb_ci_ip_network", **{attr: raw})).class_keys == frozenset()


def test_context_attributes_never_enter_the_global_index():
    """'default' ad alanı yüzlerce kümede vardır: küresel aday anahtarı olamaz."""
    keys = extract_keys(rec("K8s", "p1", "cmdb_ci_kubernetes_pod", name="web-7d9", namespace="default",
                            mount_point="/var"))
    assert not any(k.startswith(("namespace=", "mount_point=")) for k in keys.class_keys)
    assert not any(t.startswith("attr:namespace") for t in keys.by_type())


def test_class_and_context_attributes_are_disjoint_and_supported():
    assert not set(CLASS_KEY_ATTRIBUTES) & set(CONTEXT_KEY_ATTRIBUTES)
    for a in (*CLASS_KEY_ATTRIBUTES, *CONTEXT_KEY_ATTRIBUTES):
        assert ATTRIBUTE_KEY_TYPES[a] == f"attr:{a}"


# --- Skor ve çelişki -------------------------------------------------------------------------------------


def test_class_key_match_is_strong_and_conflict_is_explained():
    a = extract_keys(rec("A", "1", "cmdb_ci_cluster", name="prod-kume", cluster_id="C-1"))
    b = extract_keys(rec("B", "2", "cmdb_ci_cluster", name="prod-kume", cluster_id="c-1"))
    c = extract_keys(rec("C", "3", "cmdb_ci_cluster", name="prod-kume", cluster_id="C-2"))
    assert score(a, b).probability > 0.999
    conflicting = score(a, c)
    assert conflicting.probability < 0.5
    assert any(f.code == "class_key_conflict" and "cluster_id" in f.detail for f in conflicting.features)


# --- Çözümleyici senaryoları -----------------------------------------------------------------------------


def test_cloud_account_is_matched_by_account_id_across_sources(engine):
    first = engine.process(rec("Bulut-A", "sub-1", "cmdb_ci_cloud_service_account", name="Üretim",
                               account_id="4f2a-99b1"))
    second = engine.process(rec("ServiceNow", "sn-1", "cmdb_ci_cloud_service_account", name="Azure Prod",
                                account_id="4F2A-99B1"))
    assert first.outcome is Outcome.NEW
    assert second.outcome is Outcome.RULE_MATCH and second.ci_sys_id == first.ci_sys_id
    assert "account_id" in second.rule


def test_same_name_different_cluster_id_is_not_merged(engine):
    engine.process(rec("K8s-A", "k1", "cmdb_ci_kubernetes_cluster", name="prod", cluster_id="uid-aaa"))
    r = engine.process(rec("K8s-B", "k2", "cmdb_ci_kubernetes_cluster", name="prod", cluster_id="uid-bbb"))
    assert r.outcome in (Outcome.NEW, Outcome.REVIEW)
    assert r.outcome is not Outcome.SCORE_MATCH and r.outcome is not Outcome.RULE_MATCH


def test_ip_network_with_same_name_but_different_subnet_is_a_new_ci(engine):
    a = engine.process(rec("Ağ", "n1", "cmdb_ci_ip_network", name="sunucu-agi", subnet="10.1.2.0/24"))
    b = engine.process(rec("Ağ", "n2", "cmdb_ci_ip_network", name="sunucu-agi", subnet="10.9.9.0/24"))
    same = engine.process(rec("IPAM", "i1", "cmdb_ci_ip_network", name="DC1 sunucu", subnet="10.1.2.7/24"))
    assert b.ci_sys_id != a.ci_sys_id
    assert same.outcome is Outcome.RULE_MATCH and same.ci_sys_id == a.ci_sys_id


def test_new_branches_do_not_share_a_candidate_pool(engine):
    """'prod' adlı ad alanı ile 'prod' adlı IP ağı birbirinin adayı değildir (ayrı aileler)."""
    net = engine.process(rec("Ağ", "n1", "cmdb_ci_ip_network", name="prod", subnet="10.0.0.0/16"))
    container = engine.process(rec("K8s", "c1", "cmdb_ci_docker_container", name="prod", correlation_id="abc123"))
    assert container.outcome is Outcome.NEW and container.ci_sys_id != net.ci_sys_id


@pytest.mark.parametrize(("cls", "attrs"), [
    ("cmdb_ci_kubernetes_namespace", {"name": "shop", "namespace": "shop"}),
    ("cmdb_ci_kubernetes_pod", {"name": "web-7d9", "namespace": "shop"}),
    ("cmdb_ci_file_system", {"name": "/var", "mount_point": "/var"}),
    ("cmdb_ci_cluster_node", {"name": "node-1"}),
    ("cmdb_ci_azure_datacenter", {"name": "westeurope", "region": "westeurope"}),
    ("cmdb_ci_database", {"name": "satis"}),
])
def test_dependent_new_classes_are_never_matched_globally(engine, cls, attrs):
    engine.process(rec("İlk", "x-1", cls, **attrs))
    r = engine.process(rec("İkinci", "x-2", cls, **attrs))
    assert r.outcome is Outcome.INSUFFICIENT and "Bağımlı" in r.reasons[0]


def test_business_application_is_matched_by_name_within_its_family(engine):
    a = engine.process(rec("ServiceNow", "ba-1", "cmdb_ci_business_app", name="Müşteri Portalı"))
    b = engine.process(rec("Manual", "m-1", "cmdb_ci_business_app", name="müşteri portalı"))
    svc = engine.process(rec("Manual", "m-2", "cmdb_ci_service_auto", name="Müşteri Portalı"))
    assert b.outcome is Outcome.RULE_MATCH and b.ci_sys_id == a.ci_sys_id
    assert svc.ci_sys_id != a.ci_sys_id  # uygulama servisi ayrı aile: aynı ad birleşmez


# --- Doğrulayıcı -----------------------------------------------------------------------------------------


def test_validator_rejects_bad_identification_rules(metadata):
    md = copy.deepcopy(metadata)
    md.identification += [
        {"applies_to": "cmdb_ci_server", "independent": True, "entries": [{"priority": 100,
                                                                             "attributes": ["account_id"]}]},
        {"applies_to": "cmdb_ci_kubernetes_cluster", "independent": False,
         "entries": [{"priority": 100, "attributes": ["name"]}]},
        {"applies_to": "cmdb_ci_disk", "independent": False, "parent_reference": "size_bytes",
         "entries": [{"priority": 100, "attributes": ["name"]}]},
        {"applies_to": "cmdb_ci_vlan", "independent": False,
         "parent_relation": {"type": "Contains::Contained by", "parent_class": "cmdb_ci_yok"},
         "entries": [{"priority": 100, "attributes": ["vlan_id"]}]},
    ]
    errors = "\n".join(validate(md).errors)
    assert "sınıf anahtarı 'account_id' bu sınıfta veya atalarında tanımlı değil" in errors
    assert "bağımlı kural üst bağlam ister" in errors
    assert "parent_reference 'size_bytes' bu sınıfta referans alanı değil" in errors
    assert "üst sınıf tanımsız cmdb_ci_yok" in errors
