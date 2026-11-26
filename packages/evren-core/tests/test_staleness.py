"""Eskime işletimi (F1-06, ADR-0028): kural seçimi, karar sırası, gözlemci roller ve doğrulama. Veriler sentetiktir."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from evren_core.staleness import Observation, StalenessError, StalenessPolicy, validate_staleness

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
D = timedelta(days=1)


def obs(ds="BMC", days=0, status="missing", ok=True):
    return Observation(ds, NOW - days * D, status, ok)


@pytest.fixture(scope="module")
def policy(metadata):
    return metadata.staleness_policy


def test_rules_inherit_nearest_ancestor(policy, hierarchy):
    assert policy.rule_for("cmdb_ci_linux_server", hierarchy).applies_to == "cmdb_ci_hardware"
    assert policy.rule_for("cmdb_ci_vmware_instance", hierarchy).applies_to == "cmdb_ci_vm_instance"
    assert policy.rule_for("cmdb_ci_business_app", hierarchy) is None
    assert policy.rule_for("yok", hierarchy) is None


@pytest.mark.parametrize(("observations", "outcome"), [
    ([], "not_observed"),
    ([obs(days=50), obs("Zabbix", days=1, status="active")], "seen"),          # bir gözlemci görüyor
    ([obs(days=50, ok=False)], "suppressed"),                                   # kaynak sağlıksız: karar yok
    ([obs(days=50), obs("Zabbix", days=60, ok=False)], "suppressed"),
    ([obs(days=5)], "absent"),
    ([obs(days=20)], "stale"),
    ([obs(days=50), obs("Zabbix", days=20)], "stale"),                           # en yeni son görülme sayılır
    ([obs(days=45)], "candidate"),
])
def test_verdicts(policy, hierarchy, observations, outcome):
    v = policy.evaluate("cmdb_ci_linux_server", observations, hierarchy, NOW)
    assert v.outcome == outcome
    if observations:
        assert v.last_seen == max(o.last_seen for o in observations)
    assert v.stage == (outcome if outcome in ("stale", "candidate") else None)


def test_class_without_rule_is_not_applicable(policy, hierarchy):
    assert policy.evaluate("cmdb_ci_business_app", [obs(days=400)], hierarchy, NOW).outcome == "not_applicable"


def test_only_observer_roles_count(metadata):
    reg = metadata.sources
    assert reg.counts_for_staleness("BMC") and reg.counts_for_staleness("Zabbix") and reg.counts_for_staleness("NNMi")
    assert not reg.counts_for_staleness("ServiceNow")       # ITSM CI'ı canlı tutmaz
    assert not reg.counts_for_staleness("Manual") and not reg.counts_for_staleness("Evren")
    assert not reg.counts_for_staleness("ServiceNowChange")


@pytest.mark.parametrize(("rule", "needle"), [
    ({"applies_to": "yok", "stale_after_days": 1, "absent_candidate_after_days": 2}, "tanımsız"),
    ({"applies_to": "cmdb_ci_hardware", "stale_after_days": 10, "absent_candidate_after_days": 5}, "olmalı"),
    ({"applies_to": "cmdb_ci_hardware", "stale_after_days": 0, "absent_candidate_after_days": 5}, "pozitif"),
    ({"applies_to": "cmdb_ci_hardware", "stale_after_days": 1, "absent_candidate_after_days": 5,
      "require_all_sources_absent": False}, "desteklenmez"),
])
def test_validation(hierarchy, rule, needle):
    assert any(needle in e for e in validate_staleness([rule], hierarchy))


def test_policy_rejects_unsupported_rules():
    with pytest.raises(StalenessError):
        StalenessPolicy.from_rules([{"applies_to": "x", "stale_after_days": 3, "absent_candidate_after_days": 2}])
    with pytest.raises(StalenessError):
        StalenessPolicy.from_rules([{"applies_to": "x", "stale_after_days": 1, "absent_candidate_after_days": 2,
                                     "require_all_sources_absent": False}])
