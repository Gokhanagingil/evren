"""Gerçek PostgreSQL üzerinde şema, tetikleyiciler, yetki sınırları ve sorgu derleyicisi.

Çalıştırmak için: migrasyonlar + tohum uygulanmış boş bir veritabanı ve libpq ortam
değişkenleri (PGHOST, PGPORT, PGUSER, PGDATABASE) ile ``EVREN_TEST_PG=1``.
CI'da ``.github/workflows/ci.yml`` bunu PostgreSQL servis konteyneriyle yapar.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import uuid
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
    # 0034 (ADR-0039): mühür, doğrulama ve inceleme kanıtı da salt eklemedir (tablo sahibi için bile)
    psql("INSERT INTO audit.review (reviewer, period_start, period_end, events, denied, seals_checked, seals_ok, outcome, "
         "note) VALUES ('test', now() - interval '1 hour', now(), 0, 0, 0, true, 'findings', 'salt ekleme denemesi')")
    stmts = ["TRUNCATE audit.seal, audit.seal_check", "TRUNCATE audit.seal_check", "TRUNCATE audit.review"]
    stmts += ["UPDATE audit.review SET outcome = 'no_findings'", "DELETE FROM audit.review"]
    for stmt in stmts:
        with pytest.raises(SqlError, match="değiştirilemez"):
            psql(stmt)

def test_api_writes_only_people_it_created():
    """0030 (ADR-0034): API rolü ServiceNow/Entra kaynaklı kişi açamaz, adını/durumunu değiştiremez; yalnızca Entra
    nesne kimliğini bağlar. Rol olayı gerekçe ve geri alma nedeniyle denetim izine düşer."""
    sid = "c0ffee" + "0" * 26
    psql(f"DELETE FROM ref.sys_user WHERE sys_id = '{sid}'")
    with pytest.raises(SqlError, match="kendi açtığı"):
        psql(f"SET ROLE evren_app_api; INSERT INTO ref.sys_user (sys_id, user_name, source) "
             f"VALUES ('{sid}', 'pg.sn.kisi', 'servicenow')")
    psql(f"INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES ('{sid}', 'pg.sn.kisi', 'SN Kişi', 'servicenow')")
    with pytest.raises(SqlError, match="yalnızca Entra nesne kimliği"):
        psql(f"SET ROLE evren_app_api; UPDATE ref.sys_user SET name = 'x' WHERE sys_id = '{sid}'")
    psql(f"SET ROLE evren_app_api; UPDATE ref.sys_user SET entra_oid = '11111111-2222-4333-8444-555555555555' "
         f"WHERE sys_id = '{sid}'")
    psql(f"DELETE FROM ref.sys_user WHERE sys_id = '{sid}'")
    psql("DELETE FROM iam.principal_role WHERE principal_id = 'pg-denetim'")
    psql("BEGIN; SELECT set_config('evren.actor', 'test:yonetici', true); "
         "INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, justification) "
         "VALUES ('user', 'pg-denetim', 'evren_reader', 'yonetici', 'TALEP-1 okuma'); COMMIT")
    psql("BEGIN; SELECT set_config('evren.actor', 'test:yonetici', true); SELECT set_config('evren.reason', 'Görev bitti', true); "
         "DELETE FROM iam.principal_role WHERE principal_id = 'pg-denetim'; COMMIT")
    rows = psql("SELECT action || '|' || principal || '|' || coalesce(detail->>'justification', '') || '|' || "
                "coalesce(detail->>'reason', '') FROM audit.security_event WHERE resource = 'user:pg-denetim' "
                "ORDER BY id DESC LIMIT 2").splitlines()
    assert rows == ["role_delete|test:yonetici||Görev bitti", "role_insert|test:yonetici|TALEP-1 okuma|"]


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
        # 0013 (U3): dizinleyici yalnızca okur; kaynak bağının anahtarını ve kısıtlı tabloyu göremez
        ("evren_app_indexer", "UPDATE cmdb.cmdb_ci SET name = 'x'"),
        ("evren_app_indexer", "SELECT source_key FROM ingest.identity_link"),
        ("evren_app_indexer", "SELECT count(*) FROM cmdb.ci_security_posture"),
        ("evren_app_indexer", "INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload) "
                              "VALUES ('x', 'x', 'x', '{}')"),
        # 0014 (U4): izinli ilişki kuralları yalnızca metadata tohumuyla yazılır
        ("evren_app_api", "INSERT INTO meta.cmdb_rel_rule (parent_class, rel_type, child_class) "
                          "VALUES ('cmdb_ci', 'Runs on::Runs', 'cmdb_ci')"),
        ("evren_app_reconciler", "DELETE FROM meta.cmdb_rel_rule"),
        ("evren_app_api", "UPDATE meta.sys_choice SET dependent_value = 'x'"),
        # 0018 (U5.3): kimlik görünümü salt okunur; gateway/uzlaştırıcı/arama göremez
        ("evren_app_api", "UPDATE ingest.connector_credential_status SET created_by = 'x'"),
        ("evren_app_ingest", "SELECT count(*) FROM ingest.connector_credential_status"),
        ("evren_app_reconciler", "SELECT count(*) FROM ingest.connector_credential_status"),
        ("evren_app_indexer", "SELECT count(*) FROM ingest.connector_credential_status"),
        # 0019 (U5.3b): API isteği yalnızca elle ve istek sütunlarıyla yazar; durum, köken ve kimlik tablosu yok.
        # Çalıştırıcı CMDB'yi göremez, isteğin içeriğini değiştiremez, kimlik ve kaynak kaydına erişemez.
        ("evren_app_api", "UPDATE ingest.connector_run_request SET status = 'succeeded'"),
        ("evren_app_api", "INSERT INTO ingest.connector_run_request (instance, data_source, origin, scheduled_for, "
                          "requested_by) VALUES ('x', 'Zabbix', 'schedule', now(), 'x')"),
        ("evren_app_api", "INSERT INTO ingest.connector_instance (name, connector, data_source, schedule_tz, "
                          "max_runtime_minutes, runner_id, seen_at) VALUES ('x1', 'zabbix', 'Zabbix', 'UTC', 1, 'r', "
                          "now())"),
        ("evren_app_api", "INSERT INTO ingest.connector_credential (name, data_source, token_sha256, token_hint, "
                          "created_by, expires_at) VALUES ('x1', 'Zabbix', repeat('a', 64), 'abcd', 'x', "
                          "now() + interval '1 day')"),
        ("evren_app_connector", "SELECT count(*) FROM cmdb.cmdb_ci"),
        ("evren_app_connector", "SELECT count(*) FROM ingest.source_record"),
        ("evren_app_connector", "SELECT count(*) FROM ingest.connector_credential"),
        ("evren_app_connector", "SELECT count(*) FROM ingest.connector_credential_status"),
        ("evren_app_connector", "UPDATE ingest.connector_run_request SET requested_by = 'x'"),
        ("evren_app_connector", "UPDATE ingest.connector_run_request SET cancel_requested_at = now()"),
        ("evren_app_connector", "INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload) "
                                "VALUES ('x', 'x', 'x', '{}')"),
        ("evren_app_connector", "SELECT ingest.issue_connector_credential('x1', 'Zabbix', repeat('a', 64), 'abcd', "
                                "1, 1, 'x')"),
        ("evren_app_connector", "SELECT ingest.cancel_connector_run_request(gen_random_uuid(), 'x')"),
        ("evren_app_reconciler", "SELECT ingest.issue_connector_credential('x1', 'Zabbix', repeat('a', 64), 'abcd', "
                                 "1, 1, 'x')"),
        ("evren_app_ingest", "SELECT ingest.revoke_connector_credential('x1', 'x')"),
        ("evren_app_indexer", "SELECT count(*) FROM ingest.connector_run_request"),
        # 0020 (ADR-0022): uzlaştırıcı referansı yalnızca izinli sütunlarla yazar; kaynak, Entra alanları, grup üyeliği,
        # silme ve değişiklik kayıtları yok
        ("evren_app_reconciler", "UPDATE ref.sys_user SET source = 'servicenow' WHERE false"),
        ("evren_app_reconciler", "UPDATE ref.sys_user SET entra_oid = NULL WHERE false"),
        ("evren_app_reconciler", "UPDATE ref.sys_user_group SET entra_group_id = NULL WHERE false"),
        ("evren_app_reconciler", "DELETE FROM ref.sys_user_group WHERE false"),
        ("evren_app_reconciler", "INSERT INTO ref.sys_user_grmember (\"group\", \"user\") VALUES "
                                 "(repeat('a', 32), repeat('b', 32))"),
        # 0021 (ADR-0025): değişiklik kayıtları yalnızca izinli sütunlarla, silme yok; gözlem tablosunu okuyucu ve MCP
        # okumaz (öznitelik geçmişi), API ve denetçi yazamaz, uzlaştırıcı silemez
        ("evren_app_reconciler", "UPDATE ref.sn_change_request SET sys_id = sys_id WHERE false"),
        ("evren_app_reconciler", "DELETE FROM ref.sn_change_request WHERE false"),
        ("evren_app_reconciler", "DELETE FROM ref.sn_task_ci WHERE false"),
        ("evren_app_reconciler", "DELETE FROM insight.config_change WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM insight.config_change"),
        ("evren_app_mcp", "SELECT count(*) FROM insight.config_change"),
        ("evren_app_api", "UPDATE insight.config_change SET verdict = 'authorized' WHERE false"),
        ("evren_app_api", "UPDATE ref.sn_change_request SET state = 'closed' WHERE false"),
        ("evren_auditor_ro", "UPDATE insight.config_change SET verdict = 'authorized' WHERE false"),
        ("evren_app_connector", "SELECT count(*) FROM ref.sn_change_request"),
        # 0022 (ADR-0026): elle oluşturma isteğini API yalnızca istek sütunlarıyla yazar; sonucu uzlaştırıcı yazar
        ("evren_app_api", "UPDATE ingest.manual_create SET status = 'created' WHERE false"),
        ("evren_app_api", "INSERT INTO ingest.manual_create (sys_class_name, attributes, requested_by, status) "
                          "VALUES ('cmdb_ci_server', '{\"name\": \"x\"}', 'x', 'created')"),
        ("evren_app_reconciler", "UPDATE ingest.manual_create SET requested_by = 'x' WHERE false"),
        ("evren_app_reconciler", "DELETE FROM ingest.manual_create WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM ingest.manual_create"),
        # 0023 (ADR-0026 §5): elle ilişki isteği; sonucu uzlaştırıcı yazar, API ilişki tablosuna doğrudan yazamaz
        ("evren_app_api", "UPDATE ingest.manual_relation SET status = 'applied' WHERE false"),
        ("evren_app_api", "INSERT INTO cmdb.cmdb_rel_ci (parent, child, type) SELECT sys_id, sys_id, sys_id "
                          "FROM cmdb.cmdb_ci WHERE false"),
        ("evren_app_reconciler", "UPDATE ingest.manual_relation SET action = 'add' WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM ingest.manual_relation"),
        ("evren_app_connector", "SELECT count(*) FROM ingest.manual_create"),
        # 0024 (ADR-0026 §6): toplu içe aktarma işini API açar ama değiştiremez; uzlaştırıcı yalnızca okur
        ("evren_app_api", "UPDATE ingest.import_batch SET row_count = 1 WHERE false"),
        ("evren_app_api", "DELETE FROM ingest.import_batch WHERE false"),
        ("evren_app_api", "UPDATE ingest.manual_create SET row_no = 1 WHERE false"),
        ("evren_app_reconciler", "INSERT INTO ingest.import_batch (default_class, row_count, requested_by) "
                                 "VALUES ('cmdb_ci_server', 1, 'x')"),
        ("evren_readonly", "SELECT count(*) FROM ingest.import_batch"),
        ("evren_app_connector", "SELECT count(*) FROM ingest.import_batch"),
        # 0025 (ADR-0027): sahiplik onayı isteği; API sonucu ve onay alanlarını yazamaz, uzlaştırıcı isteği değiştiremez
        ("evren_app_api", "UPDATE ingest.attestation_request SET status = 'applied' WHERE false"),
        ("evren_app_api", "INSERT INTO ingest.attestation_request (ci_sys_id, decision, attester_user, requested_by, "
                          "status) SELECT sys_id, 'attested', sys_id, 'x', 'applied' FROM cmdb.cmdb_ci WHERE false"),
        ("evren_app_api", "UPDATE cmdb.cmdb_ci SET attestation_status = 'Attested' WHERE false"),
        ("evren_app_reconciler", "UPDATE ingest.attestation_request SET decision = 'attested' WHERE false"),
        ("evren_app_reconciler", "DELETE FROM ingest.attestation_request WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM ingest.attestation_request"),
        ("evren_app_connector", "SELECT count(*) FROM ingest.attestation_request"),
        ("evren_app_mcp", "SELECT count(*) FROM ingest.attestation_request"),
        # 0026 (ADR-0028): eskime önerisini yalnızca uzlaştırıcı yazar; API karar isteğinin sonucunu yazamaz
        ("evren_app_api", "UPDATE insight.staleness_case SET status = 'retired' WHERE false"),
        ("evren_app_api", "INSERT INTO insight.staleness_case (ci_sys_id, stage, rule_class, last_seen) "
                          "SELECT sys_id, 'candidate', 'x', now() FROM cmdb.cmdb_ci WHERE false"),
        ("evren_app_api", "UPDATE ingest.retirement_request SET status = 'applied' WHERE false"),
        ("evren_app_reconciler", "UPDATE ingest.retirement_request SET action = 'retire' WHERE false"),
        ("evren_app_reconciler", "DELETE FROM insight.staleness_case WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM insight.staleness_case"),
        ("evren_app_mcp", "SELECT count(*) FROM ingest.retirement_request"),
        ("evren_app_connector", "SELECT count(*) FROM insight.staleness_case"),
        # 0027 (ADR-0029): bağımlı kimlik anahtarlarını yalnızca uzlaştırıcı yazar
        ("evren_app_api", "INSERT INTO ingest.dependent_key (parent_ci_sys_id, family, signature, value, ci_sys_id, "
                          "data_source) SELECT sys_id, 'x', 'x', 'x', sys_id, 'NNMi' FROM cmdb.cmdb_ci WHERE false"),
        ("evren_app_api", "UPDATE ingest.dependent_key SET value = 'x' WHERE false"),
        ("evren_app_reconciler", "DELETE FROM ingest.dependent_key WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM ingest.dependent_key"),
        ("evren_app_connector", "SELECT count(*) FROM ingest.dependent_key"),
        # 0029 (ADR-0032): API olay içeriğini (payload) okuyamaz, nabız yazamaz; geçit nabzı dışında yazamaz
        ("evren_app_api", "SELECT payload FROM events.outbox LIMIT 1"),
        ("evren_app_api", "INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind) "
                          "VALUES ('x', now(), now(), 30, 'gateway')"),
        ("evren_app_ingest", "SELECT count(*) FROM events.outbox"),
        ("evren_app_ingest", "DELETE FROM ingest.worker_heartbeat WHERE false"),
        ("evren_app_api", "INSERT INTO meta.schema_migration (version, sha256) VALUES ('x', repeat('0', 64))"),
        # 0028 (ADR-0031): ilişki bildirimlerini yalnızca uzlaştırıcı yazar; bağlayıcı ve salt okur göremez
        ("evren_app_api", "INSERT INTO cmdb.cmdb_rel_ci_source (rel_sys_id, data_source, source_key) "
                          "SELECT sys_id, 'Manual', 'x' FROM cmdb.cmdb_rel_ci WHERE false"),
        ("evren_app_api", "DELETE FROM cmdb.cmdb_rel_ci_source WHERE false"),
        ("evren_app_connector", "SELECT count(*) FROM cmdb.cmdb_rel_ci_source"),
        ("evren_readonly", "SELECT count(*) FROM cmdb.cmdb_rel_ci_source"),
        ("evren_app_api", "SELECT last_error FROM sync.sn_ci_map"),
        ("evren_app_api", "UPDATE sync.sn_ci_map SET sn_sys_id = NULL WHERE false"),
        ("evren_app_api", "SELECT count(*) FROM sync.sn_sync_scope"),
        ("evren_app_reconciler", "INSERT INTO ref.sys_user (sys_id, user_name, source) VALUES (repeat('e', 32), 'x', "
                                 "'entra')"),
        ("evren_app_api", "UPDATE ref.sys_user_group SET name = 'x' WHERE false"),
        # 0031 (ADR-0035): görev çalışanı karar veremez, CMDB'ye ve kanal ayarına yazamaz; API teslim alanlarına yazamaz,
        # görev silemez; görevleri salt okur ve bağlayıcı göremez
        ("evren_app_governance", "UPDATE gov.task SET state = 'done' WHERE false"),
        ("evren_app_governance", "UPDATE gov.task SET outcome = 'confirmed' WHERE false"),
        ("evren_app_governance", "UPDATE cmdb.cmdb_ci SET name = 'x' WHERE false"),
        ("evren_app_governance", "UPDATE gov.task_channel SET is_default = true WHERE false"),
        ("evren_app_governance", "SELECT count(*) FROM ingest.source_record"),
        ("evren_app_api", "UPDATE gov.task SET external_ref = NULL WHERE false"),
        ("evren_app_api", "UPDATE gov.task SET delivery_status = 'delivered' WHERE false"),
        ("evren_app_api", "DELETE FROM gov.task WHERE false"),
        ("evren_app_api", "UPDATE gov.task_event SET actor = 'x' WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM gov.task"),
        ("evren_app_connector", "SELECT count(*) FROM gov.task"),
        ("evren_app_ingest", "SELECT count(*) FROM gov.task"),
        # 0032 (ADR-0036): görev çalışanı kampanya tanımını ve durumunu değiştiremez; API çalışma sonucunu yazamaz,
        # kampanya silemez; kampanyaları salt okur ve bağlayıcı göremez
        ("evren_app_governance", "UPDATE gov.campaign SET state = 'active' WHERE false"),
        ("evren_app_governance", "UPDATE gov.campaign SET scope_query = '' WHERE false"),
        ("evren_app_governance", "DELETE FROM gov.campaign_run WHERE false"),
        ("evren_app_api", "UPDATE gov.campaign_run SET status = 'done' WHERE false"),
        ("evren_app_api", "UPDATE gov.campaign SET created_by = 'x' WHERE false"),
        ("evren_app_api", "DELETE FROM gov.campaign WHERE false"),
        ("evren_app_api", "INSERT INTO gov.campaign_run (campaign, trigger, status, created) "
                          "SELECT sys_id, 'manual', 'done', 5 FROM gov.campaign WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM gov.campaign"),
        ("evren_app_connector", "SELECT count(*) FROM gov.campaign_run"),
        # 0033 (ADR-0037): bulguyu ve sağlık ölçümünü görev çalışanı yazar; API kural tanımlar, istisna verir ama
        # bulgu açamaz/silemez, ölçüm yazamaz; çalışan kural tanımlayamaz, istisna veremez; salt okur bulgu görmez
        ("evren_app_api", "INSERT INTO insight.finding (rule, ci_sys_id) SELECT sys_id, sys_id FROM insight.quality_rule WHERE false"),
        ("evren_app_api", "DELETE FROM insight.finding WHERE false"),
        ("evren_app_api", "UPDATE insight.finding SET details = '{}' WHERE false"),
        ("evren_app_api", "INSERT INTO insight.health_snapshot (overall) VALUES (1)"),
        ("evren_app_api", "UPDATE insight.quality_rule_run SET status = 'done' WHERE false"),
        ("evren_app_api", "DELETE FROM insight.quality_rule WHERE false"),
        ("evren_app_governance", "UPDATE insight.quality_rule SET params = '{}' WHERE false"),
        ("evren_app_governance", "UPDATE insight.finding SET exception_by = 'x' WHERE false"),
        ("evren_app_governance", "DELETE FROM insight.finding WHERE false"),
        ("evren_app_governance", "DELETE FROM insight.health_snapshot WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM insight.finding"),
        ("evren_app_connector", "SELECT count(*) FROM insight.quality_rule"),
        # 0030 (ADR-0034): API kişi kaydında yalnızca izinli sütunlara yazar, silemez; eşleme Entra kimliğine yazamaz
        ("evren_app_api", "UPDATE ref.sys_user SET department = NULL WHERE false"),
        ("evren_app_api", "DELETE FROM ref.sys_user WHERE false"),
        ("evren_app_api", "INSERT INTO ref.sys_user_grmember (\"group\", \"user\") SELECT sys_id, sys_id FROM ref.sys_user WHERE false"),
        ("evren_app_api", "DELETE FROM ref.sys_user_group WHERE false"),
        ("evren_app_reconciler", "UPDATE ref.sys_user SET entra_oid = NULL WHERE false"),
        ("evren_app_reconciler", "UPDATE ref.sys_user_group SET entra_group_id = NULL WHERE false"),
        ("evren_auditor_ro", "INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, justification) "
                             "VALUES ('user', 'x', 'evren_reader', 'y', 'z')"),
        # 0034 (ADR-0039): mühürleri yalnızca görev çalışanı ekler; kimse mühür, doğrulama veya inceleme
        # değiştiremez/silemez; API mühür yazamaz; çalışan inceleme göremez; salt okur (kendi rolü) denetim izini göremez
        ("evren_app_api", "INSERT INTO audit.seal (stream, period_start, period_end, row_count, digest, prev_chain, "
                          "chain, sealed_by) SELECT stream, period_start, period_end, 0, digest, prev_chain, chain, "
                          "'x' FROM audit.seal WHERE false"),
        ("evren_app_api", "UPDATE audit.seal SET row_count = 0 WHERE false"),
        ("evren_app_api", "UPDATE audit.review SET outcome = 'no_findings' WHERE false"),
        ("evren_app_api", "DELETE FROM audit.seal_check WHERE false"),
        ("evren_app_api", "INSERT INTO audit.seal_check (seal, checked_by, ok, checked_at) "
                          "SELECT id, 'x', true, now() FROM audit.seal WHERE false"),
        ("evren_app_governance", "UPDATE audit.seal SET chain = chain WHERE false"),
        ("evren_app_governance", "DELETE FROM audit.seal WHERE false"),
        ("evren_app_governance", "SELECT count(*) FROM audit.review"),
        ("evren_app_governance", "INSERT INTO audit.security_event (principal, action, decision) VALUES ('t', 'login', 'allow')"),
        ("evren_app_governance", "UPDATE audit.ci_change SET actor = 'x' WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM audit.trail"),
        ("evren_readonly", "SELECT count(*) FROM audit.seal"),
        ("evren_app_connector", "SELECT count(*) FROM audit.review"),
        ("evren_app_ingest", "SELECT count(*) FROM audit.seal"),
        ("evren_auditor_ro", "INSERT INTO audit.review (reviewer, period_start, period_end, events, denied, seals_checked, "
                             "seals_ok, outcome, note) VALUES ('x', now(), now() + interval '1 hour', 0, 0, 0, true, "
                             "'findings', 'salt okur yazamaz')"),
        # 0035 (ADR-0040): uyarıyı görev çalışanı açar ve çözer, API yalnızca onaylar; bildirimi API silemez ve
        # teslim durumunu yazamaz; salt okur ve bağlayıcı uyarı ve bildirim göremez; denetçi salt okur onaylayamaz
        ("evren_app_api", "UPDATE ops.alert SET state = 'resolved' WHERE false"),
        ("evren_app_api", "DELETE FROM notify.notification WHERE false"),
        ("evren_app_api", "UPDATE notify.delivery SET status = 'sent' WHERE false"),
        ("evren_app_api", "INSERT INTO notify.cursor (stream) VALUES ('sahte_imlec')"),
        ("evren_app_api", "DELETE FROM notify.route WHERE false"),
        ("evren_readonly", "SELECT count(*) FROM notify.notification"),
        ("evren_readonly", "SELECT count(*) FROM ops.alert"),
        ("evren_app_connector", "SELECT count(*) FROM notify.route"),
        ("evren_app_ingest", "SELECT count(*) FROM ops.alert"),
        ("evren_auditor_ro", "UPDATE ops.alert SET ack_note = 'denetçi onaylayamaz' WHERE false"),
        # ADR-0023: kısıtlı duruşu yalnızca uzlaştırıcı yazar; API yalnızca okur (hassasiyet denetimi API'de)
        ("evren_app_api", "UPDATE cmdb.ci_security_posture SET risk_score = 'High' WHERE false"),
        ("evren_app_api", "DELETE FROM cmdb.ci_security_posture WHERE false"),
        ("evren_app_ingest", "SELECT count(*) FROM cmdb.ci_security_posture"),
        ("evren_app_connector", "SELECT count(*) FROM cmdb.ci_security_posture"),
        ("evren_auditor_ro", "UPDATE cmdb.ci_security_posture SET risk_score = 'High' WHERE false"),
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
        ("evren_app_indexer", "SELECT count(*) FROM cmdb.cmdb_ci"),
        ("evren_app_indexer", "SELECT ci_sys_id, data_source FROM ingest.identity_link WHERE active LIMIT 1"),
        ("evren_readonly", "SELECT count(*) FROM meta.cmdb_rel_rule"),
        ("evren_app_api", "SELECT parent_class, rel_type, child_class FROM meta.cmdb_rel_rule LIMIT 1"),
        ("evren_app_mcp", "SELECT value, dependent_value FROM meta.sys_choice LIMIT 1"),
        ("evren_app_api", "SELECT name, data_source, token_hint, state FROM ingest.connector_credential_status"),
        ("evren_auditor_ro", "SELECT name, state FROM ingest.connector_credential_status"),
        ("evren_app_api", "SELECT count(*) FROM ingest.connector_run_request"),
        ("evren_app_api", "SELECT name, schedule, next_run_at FROM ingest.connector_instance"),
        ("evren_app_connector", "SELECT name, lifecycle FROM ingest.data_source"),
        ("evren_app_connector", "UPDATE ingest.connector_run_request SET result_note = result_note WHERE false"),
        ("evren_auditor_ro", "SELECT count(*) FROM ingest.connector_run_request"),
        ("evren_app_api", "SELECT file_name, row_count FROM ingest.import_batch"),
        ("evren_app_reconciler", "SELECT count(*) FROM ingest.import_batch"),
        ("evren_auditor_ro", "SELECT batch_id, row_no FROM ingest.manual_create"),
        ("evren_app_api", "INSERT INTO ingest.attestation_request (ci_sys_id, decision, attester_user, requested_by) "
                          "SELECT sys_id, 'attested', sys_id, 'x' FROM cmdb.cmdb_ci WHERE false"),
        ("evren_app_reconciler", "UPDATE ingest.attestation_request SET status = 'applied', applied_at = now() "
                                 "WHERE false"),
        ("evren_auditor_ro", "SELECT decision, reason, attester_user FROM ingest.attestation_request"),
        ("evren_app_api", "SELECT sys_id FROM ref.sys_user WHERE entra_oid IS NULL LIMIT 1"),
        ("evren_app_api", "SELECT stage, observations FROM insight.staleness_case"),
        ("evren_app_api", "INSERT INTO ingest.retirement_request (case_sys_id, ci_sys_id, action, note, requested_by) "
                          "SELECT sys_id, ci_sys_id, 'dismiss', 'x', 'x' FROM insight.staleness_case WHERE false"),
        ("evren_app_reconciler", "UPDATE insight.staleness_case SET status = 'recovered', closed_at = now() WHERE false"),
        ("evren_auditor_ro", "SELECT action, note FROM ingest.retirement_request"),
        ("evren_app_api", "SELECT parent_ci_sys_id, signature, value FROM ingest.dependent_key"),
        ("evren_app_reconciler", "UPDATE ingest.source_record SET parent = NULL WHERE false"),
        ("evren_app_reconciler", "DELETE FROM cmdb.cmdb_rel_ci_source WHERE false"),
        ("evren_app_reconciler", "UPDATE cmdb.cmdb_rel_ci_source SET last_run = NULL WHERE false"),
        ("evren_app_api", "SELECT data_source, source_key, last_reported FROM cmdb.cmdb_rel_ci_source"),
        ("evren_app_api", "SELECT count(*), min(created_at) FROM events.outbox WHERE published_at IS NULL"),
        ("evren_app_api", "SELECT version, sha256 FROM meta.schema_migration"),
        ("evren_app_ingest", "BEGIN; INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, "
                             "kind, details) VALUES ('gw-test', now(), now(), 30, 'gateway', '{}') "
                             "ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(); ROLLBACK"),
        ("evren_auditor_ro", "SELECT data_source, source_key FROM cmdb.cmdb_rel_ci_source"),
        # 0034 (ADR-0039): denetçi salt okur birleşik izi, mühürleri ve incelemeleri okur; API doğrulama ve inceleme
        # kanıtı ekler; görev çalışanı akışları okur
        ("evren_auditor_ro", "SELECT source, actor, client_ip, request_id FROM audit.trail LIMIT 1"),
        ("evren_auditor_ro", "SELECT chain FROM audit.seal LIMIT 1"),
        ("evren_auditor_ro", "SELECT outcome, note FROM audit.review LIMIT 1"),
        ("evren_app_api", "SELECT count(*) FROM audit.trail WHERE at > now() - interval '1 day'"),
        ("evren_app_api", "BEGIN; INSERT INTO audit.seal_check (seal, checked_by, ok) SELECT id, 'x', true FROM audit.seal "
                          "WHERE false; ROLLBACK"),
        ("evren_app_governance", "SELECT count(*) FROM audit.security_event"),
        ("evren_app_governance", "SELECT count(*) FROM audit.ci_change"),
        ("evren_app_governance", "SELECT count(*) FROM audit.seal_check"),
        # 0035 (ADR-0040): sağlık izlemesi ve bildirim
        ("evren_app_governance", "SELECT count(*), min(created_at) FROM events.outbox WHERE published_at IS NULL"),
        ("evren_app_governance", "SELECT name FROM ingest.data_source WHERE lifecycle = 'active'"),
        ("evren_app_api", "SELECT count(*) FROM ops.alert WHERE state = 'firing'"),
        ("evren_auditor_ro", "SELECT count(*) FROM notify.delivery"),
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


def test_data_model_seeded_with_rules_and_dependent_choices(metadata):
    """U4 (ADR-0019): sınıf kütüphanesi, bağımlı yaşam döngüsü değerleri ve izinli ilişki kuralları tohumlanır."""
    assert int(psql("SELECT count(*) FROM meta.sys_db_object WHERE is_cmdb_class")) == len(metadata.classes)
    assert int(psql("SELECT count(*) FROM meta.cmdb_rel_rule")) == len(metadata.relationship_rules["rules"])
    assert psql("SELECT dependent_value FROM meta.sys_choice WHERE name = 'cmdb_ci' "
                "AND element = 'life_cycle_stage_status' AND value = 'In Use'") == "Operational"
    # sn görünümü yeni sınıflarda da ServiceNow adıyla sorgulanır
    assert psql("SELECT count(*) FROM sn.cmdb_ci_kubernetes_pod") == "0"
    with pytest.raises(SqlError, match="foreign key"):  # tanımsız sınıfa kural yazılamaz
        psql("INSERT INTO meta.cmdb_rel_rule (parent_class, rel_type, child_class) "
             "VALUES ('cmdb_ci_yok', 'Runs on::Runs', 'cmdb_ci')")


def test_credential_view_has_no_secret_column():
    """0018: kimlik görünümünde belirteç özeti yoktur (U5.3)."""
    cols = psql("SELECT string_agg(column_name, ',' ORDER BY ordinal_position) FROM information_schema.columns "
                "WHERE table_schema = 'ingest' AND table_name = 'connector_credential_status'")
    assert "token_sha256" not in cols and "token_hint" in cols and cols.endswith("state")
    assert psql("SELECT count(*) FROM iam.permission WHERE resource = 'data_source' AND operation = 'connector_read'") == "2"
    assert psql("SELECT string_agg(role, ',') FROM iam.permission WHERE operation = 'attest'") == "evren_ci_owner"
    assert psql("SELECT string_agg(role, ',') FROM iam.permission WHERE operation = 'retire'") == "evren_data_steward"


INSTANCE = ("INSERT INTO ingest.connector_instance (name, connector, data_source, schedule, schedule_tz, "
            "max_runtime_minutes, runner_id, seen_at) VALUES ('test-zbx', 'zabbix', 'Zabbix', '15 */2 * * *', "
            "'Europe/Istanbul', 60, 'r1', now()) ON CONFLICT (name) DO UPDATE SET seen_at = now()")


@pytest.fixture
def connector_instance():
    psql(INSTANCE)
    yield "test-zbx"
    psql("DELETE FROM ingest.connector_run_request WHERE instance = 'test-zbx'; "
         "DELETE FROM ingest.connector_instance WHERE name = 'test-zbx'")


def api_request(extra: str = "") -> str:
    return psql("SET ROLE evren_app_api; INSERT INTO ingest.connector_run_request (instance, data_source, mode, "
                f"requested_by, note) VALUES ('test-zbx', 'Zabbix', 'snapshot', 'ayse', 'deneme'){extra} RETURNING id"
                ).splitlines()[-1]


def test_api_run_request_is_manual_and_single_active(connector_instance):
    """0019: API'nin isteği 'manual' kökenli ve 'bekliyor' doğar; örnek başına tek etkin istek."""
    rid = api_request()
    assert psql(f"SELECT origin || ':' || status FROM ingest.connector_run_request WHERE id = '{rid}'") == \
        "manual:pending"
    with pytest.raises(SqlError, match="connector_run_request_active"):
        api_request()
    with pytest.raises(SqlError, match="bu veri kaynağına ait değil"):  # örnek başka kaynağın
        psql("SET ROLE evren_app_api; INSERT INTO ingest.connector_run_request (instance, data_source, "
             "requested_by) VALUES ('test-zbx', 'BMC', 'x')")
    with pytest.raises(SqlError, match="check constraint"):  # since yalnızca delta ile
        psql("SET ROLE evren_app_api; INSERT INTO ingest.connector_run_request (instance, data_source, mode, since, "
             "requested_by) VALUES ('test-zbx', 'Zabbix', 'snapshot', now(), 'x')")


def test_run_request_transitions_are_enforced(connector_instance):
    rid = api_request()
    run = "SET ROLE evren_app_connector; UPDATE ingest.connector_run_request SET "
    with pytest.raises(SqlError, match="geçersiz durum geçişi"):
        psql(run + f"status = 'succeeded', finished_at = now() WHERE id = '{rid}'")
    with pytest.raises(SqlError, match="check constraint"):  # yürümeye geçerken çalıştırıcı ve run_id zorunlu
        psql(run + f"status = 'running' WHERE id = '{rid}'")
    psql(run + f"status = 'running', claimed_by = 'r1', started_at = now(), run_id = gen_random_uuid() "
               f"WHERE id = '{rid}'")
    with pytest.raises(SqlError, match="değiştirilemez"):
        psql(run + f"claimed_by = 'r2' WHERE id = '{rid}'")
    psql(run + f"status = 'succeeded', finished_at = now(), exit_code = 0, summary = '{{\"sent\": 5}}' "
               f"WHERE id = '{rid}'")
    with pytest.raises(SqlError, match="sonuçlanmış"):
        psql(run + f"status = 'failed' WHERE id = '{rid}'")
    with pytest.raises(SqlError, match="sonuçlanmış"):  # sahibi bile değiştiremez
        psql(f"UPDATE ingest.connector_run_request SET note = 'x' WHERE id = '{rid}'")


def test_cancel_function_and_schedule_slot(connector_instance):
    rid = api_request()
    assert psql(f"SET ROLE evren_app_api; SELECT ingest.cancel_connector_run_request('{rid}', 'ayse')"
                ).splitlines()[-1] == "cancelled"
    assert psql(f"SELECT status, cancel_requested_by FROM ingest.connector_run_request WHERE id = '{rid}'") == \
        "cancelled|ayse"
    assert psql("SELECT count(*) FROM audit.security_event WHERE action = 'connector_run_cancel' "
                f"AND resource = 'connector_run_request:{rid}'") == "1"
    assert psql(f"SET ROLE evren_app_api; SELECT ingest.cancel_connector_run_request('{rid}', 'ayse')"
                ).splitlines()[-1] == "final"  # sonuçlanmış: değişiklik ve denetim kaydı yok
    # yürüyen istek: yalnızca iptal isteği yazılır, durum çalıştırıcıda
    rid = api_request()
    psql("SET ROLE evren_app_connector; UPDATE ingest.connector_run_request SET status = 'running', "
         f"claimed_by = 'r1', started_at = now(), run_id = gen_random_uuid() WHERE id = '{rid}'")
    assert psql(f"SET ROLE evren_app_api; SELECT ingest.cancel_connector_run_request('{rid}', 'ayse')"
                ).splitlines()[-1] == "running"
    assert psql(f"SELECT cancel_requested_by IS NOT NULL FROM ingest.connector_run_request WHERE id = '{rid}'") == "t"
    psql("SET ROLE evren_app_connector; UPDATE ingest.connector_run_request SET status = 'cancelled', "
         f"finished_at = now() WHERE id = '{rid}'")
    # zamanlanmış dakika bir kez açılır (birden çok çalıştırıcı)
    slot = ("SET ROLE evren_app_connector; INSERT INTO ingest.connector_run_request (instance, data_source, origin, "
            "scheduled_for, requested_by) VALUES ('test-zbx', 'Zabbix', 'schedule', '2026-11-26 10:15+03', "
            "'zamanlayici') ON CONFLICT DO NOTHING RETURNING id")
    assert psql(slot)
    psql("UPDATE ingest.connector_run_request SET status = 'rejected', finished_at = now() "
         "WHERE instance = 'test-zbx' AND status = 'pending'")
    assert psql(slot) == ""
    with pytest.raises(SqlError, match="check constraint"):  # zamanlanmış istek dakikasız olamaz
        psql("SET ROLE evren_app_connector; INSERT INTO ingest.connector_run_request (instance, data_source, origin, "
             "requested_by) VALUES ('test-zbx', 'Zabbix', 'schedule', 'z')")


def test_credential_functions_validate_and_audit():
    """0019: kimlik yalnızca özetle verilir; emekli/tanımsız kaynağa ve bozuk özete kimlik yok; denetim kaydı."""
    cred = f"test-api-{uuid.uuid4().hex[:8]}"  # denetim kaydı silinmez: her çalışmada yeni ad
    issue = ("SET ROLE evren_app_api; SELECT ingest.issue_connector_credential('{n}', '{ds}', {h}, 'abcd', 100, "
             "{d}, 'ayse')")
    try:
        assert psql(issue.format(n=cred, ds="Zabbix", h="repeat('b', 64)", d=30))
        assert psql("SELECT token_sha256 = repeat('b', 64) AND created_by = 'ayse' AND "
                    "expires_at BETWEEN now() + interval '29 days' AND now() + interval '31 days' "
                    f"FROM ingest.connector_credential WHERE name = '{cred}'") == "t"
        for name, ds, digest, days, err in [
                ("test-api-c2", "Zabbix", "'kisa'", 30, "özeti geçersiz"),
                ("test-api-c2", "Yok", "repeat('c', 64)", 30, "tanımsız"),
                ("test-api-c2", "Zabbix", "repeat('c', 64)", 731, "730"),
                (cred, "Zabbix", "repeat('c', 64)", 30, "unique")]:
            with pytest.raises(SqlError, match=err):
                psql(issue.format(n=name, ds=ds, h=digest, d=days))
        psql("UPDATE ingest.data_source SET lifecycle = 'retired' WHERE name = 'ServiceNowChange'")
        with pytest.raises(SqlError, match="emekli"):
            psql(issue.format(n="test-api-c3", ds="ServiceNowChange", h="repeat('d', 64)", d=30))
        rev = f"SET ROLE evren_app_api; SELECT ingest.revoke_connector_credential('{cred}', 'mehmet')"
        assert psql(rev).splitlines()[-1] == "Zabbix"
        assert psql(rev) == ""  # zaten iptal
        assert psql("SELECT string_agg(action || ':' || principal, ',' ORDER BY id) FROM audit.security_event "
                    f"WHERE resource = 'connector_credential:{cred}'") == \
            "connector_credential_issue:ayse,connector_credential_revoke:mehmet"
    finally:
        psql("UPDATE ingest.data_source SET lifecycle = 'active' WHERE name = 'ServiceNowChange'; "
             "DELETE FROM ingest.connector_credential WHERE name LIKE 'test-api-%'")


def test_reconciler_reference_grants_match_reference_tables():
    """0020/0021: uzlaştırıcının ref sütun yetkileri evren_core.reference.ROLE_TABLES (referans ve değişiklik
    tabloları) ile birebir (ADR-0022, ADR-0025)."""
    from evren_core.reference import ROLE_TABLES

    rows = psql("""SELECT table_name || ':' || privilege_type || ':' || string_agg(column_name, ',' ORDER BY column_name)
                     FROM information_schema.column_privileges
                    WHERE grantee = 'evren_app_reconciler' AND table_schema = 'ref'
                      AND privilege_type IN ('INSERT', 'UPDATE')
                    GROUP BY table_name, privilege_type ORDER BY 1""").splitlines()
    expected = []
    for t in ROLE_TABLES.values():
        cols = set(t.columns) | {"sys_updated_on"}
        expected += [f"{t.db_table}:INSERT:{','.join(sorted(cols | {'sys_id'}))}",
                     f"{t.db_table}:UPDATE:{','.join(sorted(cols))}"]
    assert rows == sorted(expected)


def test_api_inserts_only_test_notifications():
    """0035 (ADR-0040): bildirimleri görev çalışanı üretir; API yalnızca kanal sınaması için "test" ekler. Bildirim ve
    okundu bilgisi değiştirilemez."""
    psql("BEGIN; SET LOCAL ROLE evren_app_api; INSERT INTO notify.notification (kind, audience_kind, audience, title, "
         "source, dedup_key) VALUES ('test', 'role', 'evren_operator', 'Sınama', 'test', 'test:pg-izinli'); ROLLBACK")
    with pytest.raises(SqlError, match="yalnızca sınama"):
        psql("SET ROLE evren_app_api; INSERT INTO notify.notification (kind, audience_kind, audience, title, source, "
             "dedup_key) VALUES ('task_assigned', 'role', 'evren_operator', 'Sahte görev', 'x', 'test:pg-sahte')")
    with pytest.raises(SqlError, match="değiştirilemez"):
        psql("BEGIN; INSERT INTO notify.notification (kind, audience_kind, audience, title, source, dedup_key) "
             "VALUES ('test', 'role', 'evren_operator', 'Sınama', 'test', 'test:pg-degisiklik'); "
             "UPDATE notify.notification SET title = 'değişti' WHERE dedup_key = 'test:pg-degisiklik'; ROLLBACK")
