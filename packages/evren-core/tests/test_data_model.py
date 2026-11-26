"""U4 CMDB veri modeli (ADR-0019): sınıf kütüphanesi, kimlik aileleri, yaşam döngüsü, izinli ilişki kuralları."""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from evren_core.changeset import Change, apply_changes
from evren_core.connectors.mapping import FieldMapping
from evren_core.metadata import generate_seed_sql, validate, verification_backlog
from evren_core.relations import RelationRules

# Bağlayıcıların ve CSDM'in ihtiyaç duyduğu hedef sınıflar (A1, A2): biri silinirse veri yanlış sınıfa düşer.
REQUIRED_CLASSES = {
    "uç nokta": ["cmdb_ci_pc_hardware", "cmdb_ci_printer"],
    "veri merkezi": ["cmdb_ci_rack"],
    "depolama": ["cmdb_ci_storage_device", "cmdb_ci_disk", "cmdb_ci_storage_volume", "cmdb_ci_storage_server",
                 "cmdb_ci_file_system"],
    "mantıksal ağ": ["cmdb_ci_ip_network", "cmdb_ci_vlan", "cmdb_ci_dns_name"],
    "küme": ["cmdb_ci_cluster", "cmdb_ci_cluster_node", "cmdb_ci_win_cluster"],
    "konteyner": ["cmdb_ci_oslv_container", "cmdb_ci_docker_container", "cmdb_ci_kubernetes_cluster",
                  "cmdb_ci_kubernetes_namespace", "cmdb_ci_kubernetes_node", "cmdb_ci_kubernetes_deployment",
                  "cmdb_ci_kubernetes_pod", "cmdb_ci_kubernetes_service"],
    "bulut": ["cmdb_ci_cloud_service_account", "cmdb_ci_logical_datacenter", "cmdb_ci_azure_datacenter",
              "cmdb_ci_aws_datacenter"],
    "ara katman": ["cmdb_ci_app_server", "cmdb_ci_app_server_tomcat", "cmdb_ci_web_server",
                   "cmdb_ci_apache_web_server", "cmdb_ci_microsoft_iis_web_server"],
    "veritabanı": ["cmdb_ci_db_mysql_instance", "cmdb_ci_db_postgresql_instance", "cmdb_ci_database"],
    "CSDM": ["cmdb_ci_business_capability", "cmdb_ci_business_app", "cmdb_ci_business_process",
             "cmdb_ci_information_object", "cmdb_ci_service_business", "cmdb_ci_service_technical",
             "service_offering", "cmdb_ci_service_auto", "cmdb_ci_query_based_service"],
}


@pytest.mark.parametrize("group", sorted(REQUIRED_CLASSES))
def test_class_library_covers_group(metadata, group):
    missing = [c for c in REQUIRED_CLASSES[group] if c not in metadata.hierarchy]
    assert not missing, f"{group}: eksik sınıflar {missing}"


def test_class_placement_follows_servicenow_tree(metadata):
    h = metadata.hierarchy
    assert h.is_a("cmdb_ci_pc_hardware", "cmdb_ci_computer") and not h.is_a("cmdb_ci_pc_hardware", "cmdb_ci_server")
    assert h.is_a("cmdb_ci_app_server_tomcat", "cmdb_ci_appl")
    assert h.is_a("cmdb_ci_db_postgresql_instance", "cmdb_ci_db_instance")
    assert h.is_a("cmdb_ci_kubernetes_cluster", "cmdb_ci_cluster")
    assert h.is_a("cmdb_ci_query_based_service", "cmdb_ci_service")
    assert h.is_a("cmdb_ci_storage_server", "cmdb_ci_server")


def test_identity_family_defaults_to_top_branch(metadata):
    h = metadata.hierarchy
    # Tanımlı aileler değişmez
    assert h.family("cmdb_ci_linux_server") == "cmdb_ci_hardware"
    assert h.family("cmdb_ci_vmware_instance") == "cmdb_ci_vm_object"
    assert h.family("cmdb_ci_app_server_tomcat") == "cmdb_ci_appl"
    # Aile listesinde olmayan dallar kendi kökleriyle ayrışır (ortak "ailesiz" havuz yok)
    assert h.family("cmdb_ci_kubernetes_pod") == "cmdb_ci_kubernetes_component"
    assert h.family("cmdb_ci_kubernetes_cluster") == "cmdb_ci_cluster"
    assert h.family("cmdb_ci_ip_network") == "cmdb_ci_ip_network"
    assert h.family("cmdb_ci_ip_network") != h.family("cmdb_ci_vlan")
    assert h.family("cmdb_ci") is None


def test_every_class_family_is_defined(metadata):
    h = metadata.hierarchy
    assert all(h.family(c["name"]) for c in metadata.classes if c["name"] != "cmdb_ci")


# --- Yaşam döngüsü (A4) -----------------------------------------------------------------------------------


def test_lifecycle_status_depends_on_stage_and_maps_to_legacy(metadata):
    stages = {v["value"] for v in metadata.choice_list("cmdb_ci", "life_cycle_stage")["values"]}
    statuses = metadata.choice_list("cmdb_ci", "life_cycle_stage_status")
    assert statuses["depends_on"] == "life_cycle_stage"
    assert {"Operational", "End of Life"} <= stages
    by_value = {v["value"]: v for v in statuses["values"]}
    assert all(v["dependent_value"] in stages for v in statuses["values"])
    assert by_value["In Use"]["dependent_value"] == "Operational"
    assert by_value["In Use"]["legacy"] == {"operational_status": "1", "install_status": "1"}
    assert by_value["Retired"]["legacy"]["install_status"] == "7"
    assert by_value["Stolen"]["legacy"]["install_status"] == "8"
    # Her aşamanın en az bir durumu vardır
    assert stages == {v["dependent_value"] for v in statuses["values"]}


def test_lifecycle_fields_are_choice_fields(metadata):
    root = metadata.fields_of("cmdb_ci", inherited=False)
    assert root["life_cycle_stage"]["choice"] and root["life_cycle_stage_status"]["choice"]
    assert not [w for w in validate(metadata).warnings if "life_cycle" in w]


def test_validator_rejects_bad_dependent_and_legacy_values(metadata):
    md = copy.deepcopy(metadata)
    statuses = md.choice_list("cmdb_ci", "life_cycle_stage_status")
    statuses["values"][0]["dependent_value"] = "Hayal"
    statuses["values"][1]["legacy"] = {"install_status": "999"}
    statuses["values"][2]["legacy"] = {"name": "x"}
    used_for = md.choice_list("cmdb_ci", "used_for")
    used_for["values"][0]["dependent_value"] = "Production"
    errors = "\n".join(validate(md).errors)
    assert "dependent_value life_cycle_stage listesinde değil: Hayal" in errors
    assert "install_status için tanımsız değer: 999" in errors
    assert "seçim listesi olmayan alana: name" in errors
    assert "depends_on tanımsız" in errors


def test_seed_writes_dependent_values(metadata):
    sql = generate_seed_sql(metadata)
    line = next(x for x in sql.splitlines() if "'life_cycle_stage_status', 'In Use'" in x)
    assert "'Operational')" in line
    assert "dependent_value = EXCLUDED.dependent_value" in line


# --- İzinli ilişki kuralları (A5) --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rules(metadata) -> RelationRules:
    return metadata.relations


def test_relation_rules_are_inherited(rules):
    v = rules.check("cmdb_ci_app_server_tomcat", "Runs on::Runs", "cmdb_ci_linux_server")
    assert v.allowed and v.rule.parent == "cmdb_ci_appl" and v.rule.child == "cmdb_ci_computer"


def test_reversed_relation_is_detected_with_turkish_hint(rules):
    v = rules.check("cmdb_ci_linux_server", "Runs on::Runs", "cmdb_ci_app_server_tomcat")
    assert v.outcome == "reversed"
    assert "Yön ters" in v.message("cmdb_ci_linux_server", "Runs on::Runs", "cmdb_ci_app_server_tomcat")


def test_meaningless_pair_is_not_allowed(rules):
    assert rules.check("cmdb_ci_appl", "Runs on::Runs", "cmdb_ci_ip_switch").outcome == "not_allowed"
    assert rules.check("cmdb_ci_ip_switch", "Virtualized by::Virtualizes", "cmdb_ci_appl").outcome == "not_allowed"
    assert rules.check("cmdb_ci_bilinmeyen", "Runs on::Runs", "cmdb_ci_server").outcome == "not_allowed"


def test_generic_dependency_is_allowed_between_any_classes(rules):
    assert rules.check("cmdb_ci_service_auto", "Depends on::Used by", "cmdb_ci_kubernetes_pod").allowed
    assert rules.check("cmdb_ci_business_app", "Depends on::Used by", "cmdb_ci_ip_network").allowed


def test_most_specific_rule_is_reported(rules):
    v = rules.check("cmdb_ci_kubernetes_pod", "Contains::Contained by", "cmdb_ci_docker_container")
    assert v.allowed and v.rule.parent == "cmdb_ci_kubernetes_pod"


def test_suggestions_list_both_directions(rules):
    s = {(x["direction"], x["type"], x["other_class"]) for x in rules.suggestions("cmdb_ci_kubernetes_pod")}
    assert ("parent", "Runs on::Runs", "cmdb_ci_kubernetes_node") in s
    assert ("child", "Contains::Contained by", "cmdb_ci_kubernetes_namespace") in s
    assert ("parent", "Depends on::Used by", "cmdb_ci") in s
    assert rules.suggestions("cmdb_ci_bilinmeyen") == []


def test_enforcement_mode_is_validated(metadata):
    with pytest.raises(ValueError, match="Geçersiz zorlama kipi"):
        RelationRules([], metadata.hierarchy, "sıkı")
    md = copy.deepcopy(metadata)
    md.relationship_rules["enforcement"] = "sıkı"
    assert any("geçersiz zorlama kipi" in e for e in validate(md).errors)


def test_validator_rejects_bad_relation_rules(metadata):
    md = copy.deepcopy(metadata)
    md.relationship_rules["rules"] += [
        {"parent": "cmdb_ci_yok", "type": "Runs on::Runs", "child": "cmdb_ci_server"},
        {"parent": "cmdb_ci_appl", "type": "Çalışır::Çalıştırır", "child": "cmdb_ci_server"},
        dict(md.relationship_rules["rules"][1]),
    ]
    errors = "\n".join(validate(md).errors)
    assert "sınıf tanımsız: cmdb_ci_yok" in errors
    assert "ilişki tipi tanımsız" in errors
    assert "tekrarlanan kural" in errors


def test_relation_rules_never_name_products(metadata):
    """Kural 8 ilişki kurallarında da geçerli: ürün adı değil sınıf ve tip."""
    text = json.dumps(metadata.relationship_rules, ensure_ascii=False).lower()
    for product in ("bmc", "zabbix", "nnmi", "defender", "helix"):
        assert product not in text


def test_connector_mapping_relations_are_allowed(repo_root, rules):
    checked = 0
    for path in sorted((repo_root / "connectors").rglob("*mapping.yaml")):
        m = FieldMapping.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
        classes = [m.default_class, *[r.class_name for r in m.class_rules]]
        for rel in m.relations:
            for cls in classes:
                pair = (cls, rel["type"], rel["target_class"]) if rel["direction"] == "parent" \
                    else (rel["target_class"], rel["type"], cls)
                v = rules.check(*pair)
                assert v.allowed, f"{path.name}: {v.message(*pair)}"
                checked += 1
    assert checked >= 2


def test_demo_relations_are_allowed_and_not_duplicated(repo_root, rules):
    demo = json.loads((repo_root / "apps/web/content/demo.json").read_text(encoding="utf-8"))
    cls = {c["sys_id"]: c["class"] for c in demo["cis"]}
    total = 0
    for c in demo["cis"]:
        keys = [(r["type"], r["direction"], r["target"]) for r in c["relations"]]
        assert len(keys) == len(set(keys)), f"{c['name']}: tekrarlanan ilişki"
        targets = [(r["type"], r["target"]) for r in c["relations"]]
        assert len(targets) == len(set(targets)), f"{c['name']}: aynı ilişki hem ebeveyn hem çocuk yönünde"
        for r in c["relations"]:
            pair = (c["class"], r["type"], cls[r["target"]]) if r["direction"] == "parent" \
                else (cls[r["target"]], r["type"], c["class"])
            assert rules.check(*pair).allowed, rules.check(*pair).message(*pair)
            total += 1
    assert total > 0


def test_seed_writes_relation_rules(metadata):
    sql = generate_seed_sql(metadata)
    assert sql.count("INSERT INTO meta.cmdb_rel_rule") == len(metadata.relationship_rules["rules"])
    assert sql.index("INSERT INTO cmdb.cmdb_rel_type") < sql.index("INSERT INTO meta.cmdb_rel_rule")


def test_change_sets_keep_relation_rules(metadata):
    md = apply_changes(metadata, [Change("add_field", "cmdb_ci_rack", "u_room", {
        "type": "string", "label_tr": "Oda", "max_length": 40})])
    assert md.relationship_rules == metadata.relationship_rules
    assert md.relations.check("cmdb_ci_rack", "Contains::Contained by", "cmdb_ci_server").allowed


def test_verification_backlog_covers_every_kind(metadata):
    backlog = verification_backlog(metadata)
    assert "cmdb_ci_kubernetes_pod" in backlog["classes"]
    assert "cmdb_ci.life_cycle_stage_status" in backlog["choices"]
    assert "Provided by::Provides" in backlog["relationship_types"]
    assert any("cmdb_ci_kubernetes_pod" in r for r in backlog["relationship_rules"])
    assert "cmdb_ci_pc_hardware" not in backlog["classes"]


# --- Veri Modeli görünümü (U4.4) ----------------------------------------------------------------------------


def test_model_view_tree_is_complete_ordered_and_deterministic(metadata):
    from evren_core.model_view import model_view

    doc = model_view(metadata)
    names = [c["name"] for c in doc["classes"]]
    assert sorted(names) == sorted(c["name"] for c in metadata.classes) and names[0] == "cmdb_ci"
    pos = {n: i for i, n in enumerate(names)}
    for c in doc["classes"]:
        assert c["depth"] == len(metadata.hierarchy.ancestors(c["name"])) - 1
        if c["parent"]:
            assert pos[c["parent"]] < pos[c["name"]]  # ebeveyn önce (derinlik öncelikli)
        assert c["field_count"] == len(metadata.fields_of(c["name"]))
    assert model_view(metadata) == doc
    assert doc["stats"]["core_columns"] == 39 and doc["stats"]["to_verify"] == sum(len(v) for v in doc["backlog"].values())


def test_model_view_identification_shows_inheritance_and_context(metadata):
    from evren_core.model_view import model_view

    by = {c["name"]: c for c in model_view(metadata)["classes"]}
    linux = by["cmdb_ci_linux_server"]["identification"]
    assert linux["inherited"] and linux["applies_to"] == "cmdb_ci_hardware" and linux["independent"]
    assert [e["priority"] for e in linux["entries"]] == sorted(e["priority"] for e in linux["entries"])
    pod = by["cmdb_ci_kubernetes_pod"]["identification"]
    assert not pod["independent"] and pod["context"] == {
        "kind": "relation", "type": "Contains::Contained by", "parent_class": "cmdb_ci_kubernetes_cluster"}
    assert by["cmdb_ci_file_system"]["identification"]["context"] == {"kind": "reference", "field": "computer"}
    assert by["cmdb_ci"]["identification"] is None


def test_static_model_file_is_up_to_date(repo_root):
    import sys

    sys.path.insert(0, str(repo_root / "tools"))
    import build_model

    assert (repo_root / "apps/web/content/model.json").read_text(encoding="utf-8") == build_model.render()
