"""ServiceNow referans tabloları (F0-08, ADR-0022): değer denetimi, rol yetkisi, kişisel verinin en azlığı."""
from __future__ import annotations

import dataclasses

import pytest

from evren_core.reference import REFERENCE_TABLES, ReferenceDataError, coerce_reference, is_reference

SID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
MGR = "b2c3d4e5f60718293a4b5c6d7e8f90a2"


def test_group_values_are_coerced_and_empty_becomes_null():
    out = coerce_reference("sys_user_group", SID, {"name": "  Linux Ekibi ", "manager": MGR, "active": "false"})
    assert out == {"name": "Linux Ekibi", "manager": MGR, "email": None, "active": False}
    assert coerce_reference("sys_user_group", SID, {"name": "X"})["active"] is True  # etkinlik bilinmiyorsa etkin
    assert coerce_reference("core_company", SID, {"name": "Y", "manufacturer": "true", "vendor": True}) == {
        "name": "Y", "manufacturer": True, "vendor": True}


@pytest.mark.parametrize(("table", "sys_id", "attrs", "message"), [
    ("sys_user_group", "kisa", {"name": "X"}, "sys_id"),
    ("sys_user_group", SID, {"name": "X", "manager": "javascript:alert(1)"}, "manager"),
    ("sys_user_group", SID, {"name": "X", "active": "evet"}, "active"),
    ("sys_user_group", SID, {"name": "X" * 1001}, "1000"),
    ("sys_user_group", SID, {"name": ""}, "name boş"),
    ("sys_user", SID, {"name": "Ad Soyad"}, "user_name boş"),
    ("sys_user", SID, {"user_name": "a", "phone": "555"}, "olmayan sütunlar"),  # kişisel veri en az
    ("sys_user_grmember", SID, {}, "tanımsız"),
    ("cmdb_ci_server", SID, {"name": "x"}, "tanımsız"),
])
def test_invalid_reference_records_are_rejected_without_echoing_values(table, sys_id, attrs, message):
    with pytest.raises(ReferenceDataError, match=message) as exc:
        coerce_reference(table, sys_id, attrs)
    assert "javascript" not in str(exc.value) and "555" not in str(exc.value)


def test_reference_tables_carry_minimal_personal_data():
    assert set(REFERENCE_TABLES["sys_user"].columns) == {"user_name", "name", "email", "department", "active"}
    assert all(is_reference(t) for t in REFERENCE_TABLES) and not is_reference("cmdb_ci_server")
    assert REFERENCE_TABLES["sys_user"].fields[0] == "sys_id" and REFERENCE_TABLES["sys_user"].fields[-1] == "sys_updated_on"


def test_only_reference_role_writes_references(metadata):
    reg = metadata.sources
    assert reg.writes_references("ServiceNow")
    assert not any(reg.writes_references(s) for s in ("BMC", "vCenter", "Zabbix", "Defender", "NNMi", "Manual"))
    shadow = dataclasses.replace(metadata, data_sources=[{**d, "lifecycle": "shadow"} if d["name"] == "ServiceNow" else d
                                                         for d in metadata.data_sources])
    assert not shadow.sources.writes_references("ServiceNow")  # gölgedeki kaynak yazmaz
