import json
import re

from evren_core.metadata import CORE_COLUMNS, generate_seed_sql, validate


def test_seed_metadata_is_valid(metadata):
    report = validate(metadata)
    assert report.ok, report.errors


def test_ddl_core_columns_match_metadata(repo_root):
    ddl = (repo_root / "db/migrations/0001_core.sql").read_text(encoding="utf-8")
    body = ddl.split("CREATE TABLE cmdb.cmdb_ci (", 1)[1].split("class_attributes", 1)[0]
    columns = [m.group(1) for m in re.finditer(r"^\s{2}([a-z_]+)\s+\S", body, re.M)]
    assert tuple(c for c in columns if c not in ("sys_id", "sys_class_name")) == CORE_COLUMNS


def test_every_class_has_turkish_label_and_view(metadata):
    sql = generate_seed_sql(metadata)
    for c in metadata.classes:
        assert c["label_tr"]
        assert f'CREATE VIEW sn."{c["name"]}"' in sql


def test_validator_catches_common_mistakes(metadata):
    import copy

    md = copy.deepcopy(metadata)
    md.classes.append({"name": "cmdb_ci_bad", "label": "Bad", "label_tr": "Hatalı", "extends": "cmdb_ci_server",
                       "fields": [{"name": "ram", "type": "integer", "label_tr": "RAM"},
                                  {"name": "foo", "type": "money", "label_tr": "x"},
                                  {"name": "bar", "type": "string", "label_tr": "y", "storage": "column"}]})
    md.reconciliation = copy.deepcopy(md.reconciliation)
    md.reconciliation["rules"].append({"applies_to": "cmdb_ci_server", "attributes": "*", "priorities": {"Nope": 1}})
    md.__post_init__()
    errors = "\n".join(validate(md).errors)
    assert "üst sınıftaki alanı yeniden tanımlıyor" in errors
    assert "geçersiz tip money" in errors
    assert "fiziksel kolon yalnızca cmdb_ci" in errors
    assert "gölgeler" in errors
    assert "veri kaynağı tanımsız Nope" in errors


def test_json_schemas_are_in_sync_with_models(repo_root):
    import sys

    sys.path.insert(0, str(repo_root / "tools"))
    from export_schemas import SCHEMAS, render

    for name, model in SCHEMAS.items():
        committed = (repo_root / "schemas" / name).read_text(encoding="utf-8")
        assert committed == render(model), f"schemas/{name} güncel değil: python tools/export_schemas.py"
        json.loads(committed)


def test_envelope_example_validates_against_schema(repo_root):
    import jsonschema

    schema = json.loads((repo_root / "schemas/ingest-envelope.schema.json").read_text(encoding="utf-8"))
    example = {
        "specversion": "1.0",
        "id": "zbx-10501-2026-11-25T09:00:00Z",
        "source": "urn:evren:connector:zabbix:dc1",
        "type": "evren.ingest.record.v1",
        "time": "2026-11-25T09:00:00Z",
        "subject": "10501",
        "data": {
            "data_source": "Zabbix",
            "run_id": "00000000-0000-4000-8000-000000000001",
            "source_key": "10501",
            "class_hint": "cmdb_ci_win_server",
            "attributes": {"name": "ABC_1", "ip_address": "10.20.30.41"},
            "lookup": {"ip_addresses": ["10.20.30.41"]},
            "observed_at": "2026-11-25T09:00:00Z",
        },
    }
    jsonschema.validate(example, schema)
    bad = json.loads(json.dumps(example))
    bad["data"]["unexpected"] = True
    try:
        jsonschema.validate(bad, schema)
    except jsonschema.ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Bilinmeyen alan reddedilmeli (extra=forbid)")
