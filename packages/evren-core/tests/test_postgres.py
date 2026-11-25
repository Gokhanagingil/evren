"""Gerçek PostgreSQL üzerinde şema, tetikleyiciler, yetki sınırları ve sorgu derleyicisi.

Çalıştırmak için: migrasyonlar + tohum uygulanmış boş bir veritabanı ve libpq ortam
değişkenleri (PGHOST, PGPORT, PGUSER, PGDATABASE) ile ``EVREN_TEST_PG=1``.
CI'da ``.github/workflows/ci.yml`` bunu PostgreSQL servis konteyneriyle yapar.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
from decimal import Decimal

import pytest

from evren_core.encoded_query import compile_sql, parse
from evren_core.metadata import metadata_field_resolver

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.environ.get("EVREN_TEST_PG"), reason="EVREN_TEST_PG tanımlı değil"),
]


class SqlError(RuntimeError):
    pass


def psql(sql: str) -> str:
    proc = subprocess.run(
        ["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if proc.returncode != 0:
        raise SqlError(proc.stderr.strip())
    return proc.stdout.strip()


def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, dt.datetime):
        return f"'{value.isoformat()}'::timestamptz"
    if isinstance(value, list):
        return "ARRAY[" + ",".join(literal(v) for v in value) + "]" if value else "'{}'"
    return "'" + str(value).replace("'", "''") + "'"


def render(where: str, params: list) -> str:
    out = where
    for p in params:
        out = out.replace("%s", literal(p), 1)
    return out


@pytest.fixture(scope="module", autouse=True)
def sample_data():
    psql("""
    DELETE FROM cmdb.cmdb_ci WHERE sys_id LIKE 'eeee%';
    INSERT INTO cmdb.cmdb_ci (sys_id, sys_class_name, name, ip_address, operational_status, class_attributes) VALUES
      ('eeee0000000000000000000000000001', 'cmdb_ci_linux_server', 'abc-1', '10.20.30.41', 1,
       '{"ram": 16384, "virtual": true, "os": "Red Hat Enterprise Linux 9", "cpu_count": 8}'),
      ('eeee0000000000000000000000000002', 'cmdb_ci_win_server', 'abc-2', '10.20.30.42', 1,
       '{"ram": 8192, "virtual": false, "os": "Windows Server 2022"}'),
      ('eeee0000000000000000000000000003', 'cmdb_ci_ip_switch', 'ist-dc1-core-sw01', '10.0.0.1', 1,
       '{"can_route": true, "physical_interface_count": 48}'),
      ('eeee0000000000000000000000000004', 'cmdb_ci_linux_server', 'old-batch-01', '10.20.30.99', 6,
       '{"ram": 4096}');
    """)
    yield
    psql("DELETE FROM cmdb.cmdb_ci WHERE sys_id LIKE 'eeee%';")


def test_class_attributes_validated_by_dictionary():
    with pytest.raises(SqlError, match="sözlükte yok"):
        psql("INSERT INTO cmdb.cmdb_ci (sys_class_name, name, class_attributes) VALUES "
             "('cmdb_ci_linux_server', 'x', '{\"no_such_field\": 1}')")
    with pytest.raises(SqlError, match="integer beklenir"):
        psql("INSERT INTO cmdb.cmdb_ci (sys_class_name, name, class_attributes) VALUES "
             "('cmdb_ci_linux_server', 'x', '{\"ram\": \"16GB\"}')")
    with pytest.raises(SqlError, match="sözlükte yok"):  # kardeş sınıfın alanı
        psql("INSERT INTO cmdb.cmdb_ci (sys_class_name, name, class_attributes) VALUES "
             "('cmdb_ci_linux_server', 'x', '{\"can_route\": true}')")


def test_sys_id_format_is_enforced():
    assert len(psql("SELECT meta.new_sys_id()")) == 32
    with pytest.raises(SqlError):
        psql("INSERT INTO cmdb.cmdb_ci (sys_id, sys_class_name) VALUES ('NOT-A-SYS-ID', 'cmdb_ci')")


def test_servicenow_named_views_expose_typed_class_fields():
    # Yalnızca bu modülün test kayıtları (eeee… sys_id'leri): veritabanında demo verisi olsa da sonuç aynı
    mine = "sys_id LIKE 'eeee%'"
    rows = psql(f'SELECT name, ram, "virtual" FROM sn.cmdb_ci_server WHERE {mine} ORDER BY name')
    assert rows.splitlines() == ["abc-1|16384|t", "abc-2|8192|f", "old-batch-01|4096|"]
    assert psql(f"SELECT pg_typeof(ram) FROM sn.cmdb_ci_linux_server WHERE {mine} LIMIT 1") == "bigint"
    assert psql(f"SELECT count(*) FROM sn.cmdb_ci_netgear WHERE {mine}") == "1"


def test_update_bumps_versions():
    psql("UPDATE cmdb.cmdb_ci SET comments = 'test' WHERE sys_id = 'eeee0000000000000000000000000002'")
    assert psql("SELECT sys_mod_count, golden_version FROM cmdb.cmdb_ci "
                "WHERE sys_id = 'eeee0000000000000000000000000002'") == "1|2"


def test_encoded_query_compiles_and_runs_against_real_schema(metadata):
    resolve = metadata_field_resolver(metadata, "cmdb_ci_server")
    q = parse("sys_class_nameINSTANCEOFcmdb_ci_server^operational_status=1^ram>=8192^nameSTARTSWITHabc^ORDERBYname")
    where, params, order = compile_sql(q, resolve, metadata.hierarchy.descendants)
    rows = psql(f"SELECT c.name FROM cmdb.cmdb_ci c WHERE {render(where, params)} ORDER BY {order}")
    assert rows.splitlines() == ["abc-1", "abc-2"]

    q2 = parse("virtual=true^NQoperational_status=6")
    w2, p2, _ = compile_sql(q2, resolve, metadata.hierarchy.descendants)
    rows2 = psql(f"SELECT c.name FROM cmdb.cmdb_ci c WHERE c.sys_id LIKE 'eeee%' AND ({render(w2, p2)}) ORDER BY 1")
    assert rows2.splitlines() == ["abc-1", "old-batch-01"]


def test_audit_trail_is_append_only():
    psql("INSERT INTO audit.ci_change (ci_sys_id, sys_class_name, attribute, new_value, actor, reason) VALUES "
         "('eeee0000000000000000000000000001', 'cmdb_ci_linux_server', 'ram', '16384', 'test', 'reconciliation')")
    for stmt in ("UPDATE audit.ci_change SET actor = 'x'", "DELETE FROM audit.ci_change", "TRUNCATE audit.ci_change"):
        with pytest.raises(SqlError, match="değiştirilemez"):
            psql(stmt)


def test_segregation_of_duties_enforced():
    psql("DELETE FROM iam.principal_role WHERE principal_id = 'test-user-1'")
    psql("INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, justification) "
         "VALUES ('user', 'test-user-1', 'evren_schema_designer', 'admin-1', 'test')")
    with pytest.raises(SqlError, match="Görevler ayrılığı"):
        psql("INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, justification) "
             "VALUES ('user', 'test-user-1', 'evren_schema_approver', 'admin-1', 'test')")
    with pytest.raises(SqlError):  # kendine yetki veremez
        psql("INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, justification) "
             "VALUES ('user', 'test-user-2', 'evren_reader', 'test-user-2', 'test')")
    with pytest.raises(SqlError):  # hazırlayan onaylayamaz
        psql("INSERT INTO meta.schema_change_set (name, created_by, approved_by, state) "
             "VALUES ('x', 'u1', 'u1', 'approved')")
    assert int(psql("SELECT count(*) FROM audit.security_event WHERE resource = 'user:test-user-1'")) >= 1


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        ("evren_app_api", "INSERT INTO cmdb.cmdb_ci (sys_class_name) VALUES ('cmdb_ci')"),
        ("evren_app_api", "UPDATE cmdb.cmdb_ci SET name = 'x'"),
        ("evren_readonly", "SELECT * FROM cmdb.ci_security_posture"),
        ("evren_readonly", "SELECT * FROM audit.ci_change"),
        ("evren_app_reconciler", "DELETE FROM cmdb.cmdb_ci WHERE sys_id = 'eeee0000000000000000000000000004'"),
        ("evren_app_ingest", "SELECT * FROM cmdb.cmdb_ci"),
        ("evren_app_mcp", "INSERT INTO ingest.match_candidate (data_source, source_key, reason) VALUES ('X','y','z')"),
        ("evren_auditor_ro", "UPDATE meta.sys_dictionary SET label_tr = 'x'"),
        ("evren_app_api", "UPDATE ingest.data_source SET lifecycle = 'retired' WHERE name = 'BMC'"),
        ("evren_app_ingest", "INSERT INTO meta.source_role (name, label_tr, kind) VALUES ('X', 'x', 'discovery')"),
        # 0006–0007: API kararı/ayırmayı yalnızca istek olarak yazar; bağı ve durumu uzlaştırıcı değiştirir
        ("evren_app_api", "UPDATE ingest.match_candidate SET applied_at = now()"),
        ("evren_app_api", "UPDATE ingest.split_request SET status = 'applied'"),
        ("evren_app_api", "INSERT INTO ingest.identity_link (data_source, source_key, ci_sys_id, method, decided_by) "
                          "VALUES ('X', 'y', 'eeee0000000000000000000000000004', 'split', 't')"),
        ("evren_app_ingest", "SELECT count(*) FROM ingest.split_request"),
        ("evren_app_mcp", "SELECT count(*) FROM ingest.split_request"),
        # 0012 (U2): geçit kaynak kaydına erişemez, çalışmayı kapatamaz, kimlik bilgisini iptal edemez;
        # kimlik bilgisini yalnızca geçit (ve denetçi, özet hariç) görür
        ("evren_app_ingest", "SELECT count(*) FROM ingest.source_record"),
        ("evren_app_ingest", "INSERT INTO ingest.source_record (data_source, source_key, class_hint, content_hash) "
                             "VALUES ('Zabbix', 'x', 'cmdb_ci', 'x')"),
        ("evren_app_ingest", "UPDATE ingest.source_run SET completed = true, breaker_state = 'ok'"),
        ("evren_app_ingest", "INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, completed) "
                             "VALUES (gen_random_uuid(), 'Zabbix', 'delta', now(), true)"),
        ("evren_app_ingest", "UPDATE ingest.connector_credential SET revoked_at = now(), revoked_by = 'x'"),
        ("evren_app_ingest", "INSERT INTO ingest.connector_credential (name, data_source, token_sha256, token_hint, "
                             "created_by, expires_at) VALUES ('x1', 'Zabbix', repeat('a', 64), 'abcd', 'x', "
                             "now() + interval '1 day')"),
        ("evren_app_reconciler", "SELECT count(*) FROM ingest.connector_credential"),
        ("evren_app_api", "SELECT count(*) FROM ingest.connector_credential"),
        ("evren_auditor_ro", "SELECT token_sha256 FROM ingest.connector_credential"),
    ],
)
def test_least_privilege_boundaries(role, statement):
    with pytest.raises(SqlError, match="permission denied"):
        psql(f"SET ROLE {role}; {statement}")


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        ("evren_readonly", "SELECT count(*) FROM sn.cmdb_ci_server"),
        ("evren_app_reconciler", "UPDATE cmdb.cmdb_ci SET comments = 'r' WHERE sys_id = 'eeee0000000000000000000000000004'"),
        ("evren_app_api", "SELECT count(*) FROM cmdb.ci_security_posture"),
        ("evren_auditor_ro", "SELECT count(*) FROM audit.ci_change"),
        ("evren_app_api", "SELECT count(*) FROM cmdb.cmdb_ci"),          # evren_readonly'den devralınır
        ("evren_app_mcp", "SELECT count(*) FROM sn.cmdb_ci_netgear"),    # evren_readonly'den devralınır
        ("evren_app_api", "INSERT INTO audit.security_event (principal, action, decision) VALUES ('t', 'login', 'allow')"),
        ("evren_readonly", "SELECT count(*) FROM meta.source_role"),
        ("evren_app_ingest", "SELECT role, lifecycle FROM ingest.data_source"),
        ("evren_app_api", "SELECT count(*) FROM ingest.split_request"),
        ("evren_auditor_ro", "SELECT count(*) FROM ingest.split_request"),
        ("evren_app_ingest", "SELECT count(*) FROM ingest.connector_credential"),
        ("evren_app_ingest", "UPDATE ingest.source_run SET accepted_count = accepted_count WHERE false"),
        ("evren_auditor_ro", "SELECT name, token_hint, last_used_at FROM ingest.connector_credential"),
    ],
)
def test_allowed_paths_work(role, statement):
    psql(f"SET ROLE {role}; {statement}")


def test_source_roles_seeded_and_rules_reference_roles_not_products():
    """ADR-0009: kurallar role bağlı; ürün değişimi kural tablolarına dokunmaz."""
    assert psql("SELECT role || ':' || lifecycle FROM ingest.data_source WHERE name = 'BMC'") == "Discovery:active"
    assert psql("SELECT kind FROM ingest.data_source WHERE name = 'Zabbix'") == "monitoring"
    assert psql("SELECT count(*) FROM meta.cmdb_reconciliation_definition") != "0"
    assert psql("SELECT count(*) FROM meta.cmdb_reconciliation_definition r "
                "JOIN ingest.data_source d ON d.name = r.role WHERE d.name <> 'Manual'") == "0"
    with pytest.raises(SqlError, match="foreign key"):
        psql("INSERT INTO meta.cmdb_reconciliation_definition (applies_to, role, priority) VALUES ('cmdb_ci', 'BMC', 1)")
    with pytest.raises(SqlError, match="foreign key"):
        psql("INSERT INTO meta.cmdb_datasource_refresh (applies_to, role, effective_duration) "
             "VALUES ('cmdb_ci', 'vCenter', interval '1 day')")


def test_product_swap_lifecycle_constraints():
    # Aynı rolde aynı sırayla yazan ikinci ürün reddedilir (uzlaştırma sırası belirsiz kalırdı)
    with pytest.raises(SqlError, match="data_source_role_rank_writer"):
        psql("INSERT INTO ingest.data_source (name, label_tr, kind, role, role_rank) "
             "VALUES ('YeniKesif', 'Yeni keşif', 'discovery', 'Discovery', 1)")
    # Gölge modda aynı sıra serbest; rolsüz veya geçersiz yaşam döngüsü reddedilir
    psql("BEGIN; INSERT INTO ingest.data_source (name, label_tr, kind, role, role_rank, lifecycle) "
         "VALUES ('YeniKesif', 'Yeni keşif', 'discovery', 'Discovery', 1, 'shadow'); ROLLBACK;")
    with pytest.raises(SqlError, match=r"not-null|null value"):
        psql("INSERT INTO ingest.data_source (name, label_tr, kind) VALUES ('Rolsuz', 'x', 'discovery')")
    with pytest.raises(SqlError, match="check constraint"):
        psql("INSERT INTO ingest.data_source (name, label_tr, kind, role, role_rank, lifecycle) "
             "VALUES ('Hatali', 'x', 'discovery', 'Discovery', 5, 'paused')")
