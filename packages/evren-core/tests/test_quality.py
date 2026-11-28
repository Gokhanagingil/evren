"""Kalite ve uyum kuralları (U8.6, ADR-0037): şablonlar sözlükle derlenir, geçersiz kural Türkçe nedenle reddedilir,
alan ve rol adları yalnızca sözlükten, ilişki denetimi izinli ilişki kurallarıyla."""
from __future__ import annotations

from pathlib import Path

import pytest

from evren_core.metadata import load_metadata
from evren_core.quality import CHECK_KINDS, TEMPLATES, compile_rule, describe_details, relation_violations

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def md():
    return load_metadata(REPO / "metadata")


def test_every_template_compiles_and_names_no_product(md):
    assert {t["check_kind"] for t in TEMPLATES} == set(CHECK_KINDS) - {"matches_condition"}
    for t in TEMPLATES:
        plan = compile_rule(md, t["scope_class"], t["scope_query"], t["check_kind"], t["params"])
        assert plan.scope_sql and plan.match_sql and plan.summary
        text = (t["name"] + t["description"] + t["task_instructions"]).lower()
        assert not any(p in text for p in ("bmc", "vcenter", "zabbix", "defender", "nnmi", "servicenow"))


def test_no_relation_expands_classes_and_excludes(md):
    t = next(t for t in TEMPLATES if t["id"] == "iliskisiz-veritabani")
    plan = compile_rule(md, t["scope_class"], "", "no_relation", t["params"])
    related, excluded = plan.match_params
    assert "cmdb_ci_linux_server" in related and "cmdb_ci_business_app" in related
    assert "cmdb_ci_db_mssql_instance" in excluded
    assert "%s" in plan.match_sql and "'" not in plan.match_sql  # değerler parametre
    assert plan.summary == "Uygulama, bilgisayar, iş uygulaması veya servis ile ilişkisi yok"
    assert plan.depends_on_roles == ()


def test_missing_fields_detail_lists_empty_fields(md):
    plan = compile_rule(md, "cmdb_ci", "used_for=Production", "missing_fields", {"fields": ["owned_by", "support_group"]})
    assert plan.detail_sql.startswith("array_remove(") and plan.detail_params == ["owned_by", "support_group"]
    assert plan.summary == "Boş alan: Sahibi, Destek grubu"
    assert describe_details("missing_fields", {"empty": ["owned_by"]}, {"owned_by": "Sahip"}) == "Boş alan: Sahip"


def test_role_rule_depends_on_role_health(md):
    plan = compile_rule(md, "cmdb_ci_server", "", "not_seen_by_role", {"role": "Monitoring"})
    assert plan.depends_on_roles == ("Monitoring",) and plan.match_params == ["Monitoring"]


@pytest.mark.parametrize(("kind", "params", "scope", "needle"), [
    ("bilinmeyen", {}, "cmdb_ci", "Bilinmeyen kural türü"),
    ("missing_fields", {"fields": []}, "cmdb_ci", "En az bir alan"),
    ("missing_fields", {"fields": ["olmayan_alan"]}, "cmdb_ci", "olmayan_alan"),
    ("missing_fields", {"fields": "owned_by"}, "cmdb_ci", "ad listesi"),
    ("missing_fields", {"fields": ["name"], "role": "x"}, "cmdb_ci", "olmayan parametre"),
    ("no_relation", {"related_classes": ["cmdb_ci_yok"]}, "cmdb_ci", "Bilinmeyen sınıf"),
    ("no_relation", {"rel_types": ["Uydurma::Tip"]}, "cmdb_ci", "Bilinmeyen ilişki tipi"),
    ("not_seen_by_role", {"role": "BMC"}, "cmdb_ci_server", "Bilinmeyen kaynak rolü"),
    ("matches_condition", {"condition": ""}, "cmdb_ci", "koşulunu yazın"),
    ("matches_condition", {"condition": "olmayan=1"}, "cmdb_ci", "Bulgu koşulu geçersiz"),
    ("matches_condition", {"condition": "name=x"}, "cmdb_ci_yok", "Bilinmeyen sınıf"),
])
def test_invalid_rules_are_rejected_in_turkish(md, kind, params, scope, needle):
    with pytest.raises(ValueError, match=needle):
        compile_rule(md, scope, "", kind, params)


def test_condition_rule_and_scope_query_are_parameterised(md):
    plan = compile_rule(md, "cmdb_ci_server", "nameSTARTSWITHist-", "matches_condition",
                        {"condition": "install_status=7^operational_status=1"})
    where, params = plan.where
    assert "ist-%" in params and 7 in params and "ist-" not in where


def test_relation_violations_use_allowed_relation_rules(md):
    rows = [("cmdb_ci_appl", "Runs on::Runs", "cmdb_ci_linux_server", "srv-1"),       # izinli
            ("cmdb_ci_linux_server", "Runs on::Runs", "cmdb_ci_appl", "uyg-1"),        # yön ters
            ("cmdb_ci_ip_switch", "Virtualized by::Virtualizes", "cmdb_ci_appl", "x")]  # izinsiz
    bad = relation_violations(md, rows)
    assert [(b["child"], b["verdict"]) for b in bad] == [("uyg-1", "reversed"), ("x", "not_allowed")]
    text = describe_details("relation_not_allowed", {"relations": bad})
    assert "yön ters" in text and "uyg-1" in text


# ------------------------------------------------------------------ kapsam boşluğu (P1.1, ADR-0046)
def test_coverage_gap_plan_has_one_parameter_per_placeholder_and_depends_on_all_roles(md):
    plan = compile_rule(md, "cmdb_ci_server", "used_for=Production", "coverage_gap",
                        {"roles": ["Monitoring", "EndpointSecurity"], "fresh_days": 7,
                         "observed_by": ["Virtualization"], "observed_within_hours": 48})
    where, params = plan.where
    assert where.count("%s") == len(params) and plan.detail_sql.count("%s") == len(plan.detail_params)
    assert plan.depends_on_roles == ("Monitoring", "EndpointSecurity", "Virtualization")
    assert "son 7 günde görülmüyor" in plan.summary and "48 saatte" in plan.summary
    assert "'active', 'retiring'" in plan.match_sql  # gölge ve emekli kaynak kapsam saymaz
    bare = compile_rule(md, "cmdb_ci_server", "", "coverage_gap", {"roles": ["Monitoring"]})
    assert bare.where[0].count("%s") == len(bare.where[1]) and bare.depends_on_roles == ("Monitoring",)


@pytest.mark.parametrize(("params", "problem"), [
    ({}, "En az bir zorunlu rol"),
    ({"roles": ["Uydurma"]}, "Bilinmeyen kaynak rolü"),
    ({"roles": ["Monitoring"], "observed_by": ["Monitoring"]}, "hem zorunlu hem gözlemci"),
    ({"roles": ["Monitoring", "Monitoring"]}, "bir kez"),
    ({"roles": ["Monitoring"], "fresh_days": 0}, "1 ile 90"),
    ({"roles": ["Monitoring"], "fresh_days": True}, "1 ile 90"),
    ({"roles": ["Monitoring"], "observed_within_hours": 1000}, "1 ile 720"),
    ({"roles": ["Monitoring"], "role": "Monitoring"}, "olmayan parametre"),
])
def test_coverage_gap_rejects_bad_parameters(md, params, problem):
    with pytest.raises(ValueError) as e:
        compile_rule(md, "cmdb_ci_server", "", "coverage_gap", params)
    assert problem in str(e.value)


def test_coverage_gap_text_names_roles_and_days_since_last_seen():
    from datetime import UTC, datetime

    now = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
    d = {"missing": ["Monitoring", "EndpointSecurity"],
         "last_seen": {"Monitoring": "2026-11-14T09:00:00+00:00", "EndpointSecurity": None}}
    labels = {"Monitoring": "İzleme", "EndpointSecurity": "Uç nokta güvenliği"}
    assert describe_details("coverage_gap", d, role_labels=labels, now=now) == (
        "İzleme rolünde 14 gündür görülmüyor; Uç nokta güvenliği rolünde hiç görülmedi")
