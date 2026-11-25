import pytest

from evren_core.encoded_query import FieldRef, QueryError, compile_sql, parse

FIELDS = {
    "name": FieldRef("c.name", "string"),
    "operational_status": FieldRef("c.operational_status", "integer"),
    "sys_class_name": FieldRef("c.sys_class_name", "sys_class_name"),
    "ram": FieldRef("((c.class_attributes->>'ram')::bigint)", "integer"),
    "virtual": FieldRef("((c.class_attributes->>'virtual')::boolean)", "boolean"),
    "last_discovered": FieldRef("c.last_discovered", "glide_date_time"),
    "support_group": FieldRef("c.support_group", "reference"),
}


def resolve(field):
    try:
        return FIELDS[field]
    except KeyError:
        raise QueryError(f"Bilinmeyen alan: {field}") from None


def test_or_binds_to_previous_condition():
    q = parse("operational_status=1^nameSTARTSWITHabc^ORnameSTARTSWITHxyz^ORDERBYname")
    assert len(q.groups) == 1
    assert [len(t) for t in q.groups[0]] == [1, 2]
    where, params, order = compile_sql(q, resolve)
    assert where == "c.operational_status = %s AND (c.name::text ILIKE %s OR c.name::text ILIKE %s)"
    assert params == [1, "abc%", "xyz%"]
    assert order == "c.name ASC"


def test_new_query_groups_are_ored():
    q = parse("operational_status=1^NQoperational_status=2^ORDERBYDESClast_discovered")
    where, params, order = compile_sql(q, resolve)
    assert where == "(c.operational_status = %s) OR (c.operational_status = %s)"
    assert params == [1, 2]
    assert order == "c.last_discovered DESC"


def test_instanceof_expands_class_hierarchy(hierarchy):
    q = parse("sys_class_nameINSTANCEOFcmdb_ci_server")
    where, params, _ = compile_sql(q, resolve, hierarchy.descendants)
    assert where == "c.sys_class_name = ANY(%s)"
    assert "cmdb_ci_linux_server" in params[0] and "cmdb_ci_esx_server" in params[0]
    assert "cmdb_ci_ip_switch" not in params[0]


def test_typed_values_and_operators():
    q = parse("ram>=16384^virtual=true^last_discoveredBETWEEN2026-11-01@2026-11-25^support_groupISEMPTY")
    where, params, _ = compile_sql(q, resolve)
    assert "((c.class_attributes->>'ram')::bigint) >= %s" in where
    assert params[0] == 16384 and params[1] is True
    assert params[2].year == 2026 and params[3].day == 25
    assert "(c.support_group IS NULL OR c.support_group::text = '')" in where


def test_in_and_not_in_and_not_like():
    where, params, _ = compile_sql(parse("operational_statusIN1,2,5^nameNOT LIKEtest"), resolve)
    assert where == "c.operational_status = ANY(%s) AND (c.name IS NULL OR c.name::text NOT ILIKE %s)"
    assert params == [[1, 2, 5], "%test%"]


def test_like_wildcards_are_escaped():
    _, params, _ = compile_sql(parse("nameLIKE50%_off"), resolve)
    assert params == ["%50\\%\\_off%"]


def test_caret_escape_in_values():
    q = parse("name=a^^b")
    assert q.conditions[0].value == "a^b"


@pytest.mark.parametrize(
    "bad",
    [
        "name=javascript:gs.getUserID()",
        "unknown_field=1",
        "operational_status=abc",
        "Name=abc",
        "nameISEMPTYx",
        "^ORname=a",
        "ram BETWEEN 1",
    ],
)
def test_invalid_queries_are_rejected(bad):
    with pytest.raises(QueryError):
        compile_sql(parse(bad), resolve)


def test_condition_limit():
    with pytest.raises(QueryError):
        parse("^".join(["name=a"] * 201))


def test_values_are_never_interpolated():
    where, params, _ = compile_sql(parse("name=x'; DROP TABLE cmdb.cmdb_ci;--"), resolve)
    assert "DROP" not in where and params == ["x'; drop table cmdb.cmdb_ci;--"]
