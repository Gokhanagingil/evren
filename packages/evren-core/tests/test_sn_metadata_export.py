"""ServiceNow sözlük dışa aktarımının dönüştürme ve fark mantığı (ağ erişimi olmadan)."""
import sys


def load_tool(repo_root):
    sys.path.insert(0, str(repo_root / "tools"))
    import sn_metadata_export

    return sn_metadata_export


RAW = {
    "objects": [
        {"name": "cmdb_ci", "label": "Configuration Item", "super_class.name": ""},
        {"name": "cmdb_ci_hardware", "label": "Hardware", "super_class.name": "cmdb_ci"},
        {"name": "cmdb_ci_computer", "label": "Computer", "super_class.name": "cmdb_ci_hardware"},
        {"name": "cmdb_ci_server", "label": "Server", "super_class.name": "cmdb_ci_computer"},
        {"name": "cmdb_ci_esx_server", "label": "ESX Server", "super_class.name": "cmdb_ci_hyper_visor"},
    ],
    "dictionary": [
        {"name": "cmdb_ci", "element": "name", "column_label": "Name", "internal_type": "string", "max_length": "255"},
        {"name": "cmdb_ci", "element": "sys_id", "column_label": "Sys ID", "internal_type": "GUID"},
        {"name": "cmdb_ci", "element": "support_group", "column_label": "Support group", "internal_type": "reference",
         "reference": "sys_user_group"},
        {"name": "cmdb_ci_computer", "element": "ram", "column_label": "RAM (MB)", "internal_type": "integer"},
        {"name": "cmdb_ci_computer", "element": "os", "column_label": "Operating System", "internal_type": "string",
         "choice": "1"},
        {"name": "cmdb_ci_computer", "element": "cpu_speed", "column_label": "CPU speed", "internal_type": "decimal"},
        {"name": "cmdb_ci_computer", "element": "notes", "internal_type": "user_input"},
    ],
    "choices": [],
    "rel_types": [{"name": "Runs on::Runs"}],
}


def test_conversion_maps_types_and_skips_system_fields(repo_root):
    tool = load_tool(repo_root)
    converted, notes = tool.to_evren(RAW)
    classes = {c["name"]: c for c in converted["classes"]}
    names = [f["name"] for f in classes["cmdb_ci"]["fields"]]
    assert "sys_id" not in names and "support_group" in names
    ram = next(f for f in classes["cmdb_ci_computer"]["fields"] if f["name"] == "ram")
    assert ram["type"] == "integer"
    os_field = next(f for f in classes["cmdb_ci_computer"]["fields"] if f["name"] == "os")
    assert os_field.get("choice") is True
    assert any("user_input" in n for n in notes)


def test_diff_reports_parent_type_and_missing_items(repo_root, metadata):
    tool = load_tool(repo_root)
    converted, _ = tool.to_evren(RAW)
    report = "\n".join(tool.diff(metadata.classes, metadata.relationship_types, converted))
    assert "ÜST SINIF FARKI: cmdb_ci_esx_server" in report
    assert "TİP FARKI: cmdb_ci_computer.cpu_speed: Evren=integer ServiceNow=decimal" in report
    assert "EKSİK SINIF: cmdb_ci_linux_server" in report
    assert "EKSİK İLİŞKİ TİPİ: Depends on::Used by" in report
