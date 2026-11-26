"""Elle CI oluşturma (U6.1, ADR-0026) — gerçek uzlaştırıcı, PostgreSQL, en az yetkili rol.

İstekler sahip rolüyle eklenir (API'nin yazdığı gibi), uzlaştırıcı ``evren_app_reconciler`` rolüyle uygular.
Kimlik çözümlemesi: kesin eşleşme açılmaz, belirsiz eşleşme ve aynı ad karar ister, gerekçeli zorlamayla açılır;
açılan CI'ın kimlik anahtarları dizine girer ve keşif kaydı sonradan bu CI'a bağlanır. Veriler sentetiktir.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from evren_core.envelope import IngestRecord
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
SERIAL = "ELLE-" + uuid.uuid4().hex[:8].upper()


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def request(conn, cls, attributes, *, by="ayse", force=False, note=None) -> str:
    conn.execute("RESET ROLE")
    (rid,), = fetch(conn, """INSERT INTO ingest.manual_create (sys_class_name, attributes, force_new, note, requested_by)
                             VALUES (%s, %s, %s, %s, %s) RETURNING sys_id""", cls, Jsonb(attributes), force, note, by)
    conn.execute("SET ROLE evren_app_reconciler")
    return rid


def outcome(conn, rid):
    (row,) = fetch(conn, """SELECT status, result_ci, result, result_note FROM ingest.manual_create
                            WHERE sys_id = %s""", rid)
    return row


def test_new_server_is_created_with_identity_keys_and_discovery_attaches_later(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = request(as_reconciler, "cmdb_ci_linux_server",
                  {"name": "elle-srv-01", "serial_number": SERIAL, "short_description": "Laboratuvar sunucusu"})
    (res,) = store.apply_manual_creates()
    assert res.outcome == "created"
    status, ci, result, note = outcome(as_reconciler, rid)
    assert status == "created" and ci == res.ci_sys_id and result["outcome"] == "new" and result["identity_keys"]
    assert note.startswith("CI açıldı") and "Kimlik anahtarı" not in note
    assert fetch(as_reconciler, """SELECT name, sys_class_name, discovery_source, sys_created_by,
                                          short_description FROM cmdb.cmdb_ci WHERE sys_id = %s""", ci) == [
        ("elle-srv-01", "cmdb_ci_linux_server", "Manual", "ayse", "Laboratuvar sunucusu")]
    assert fetch(as_reconciler, """SELECT source_key, method, rule_ref, decided_by FROM ingest.identity_link
                                   WHERE ci_sys_id = %s AND active""", ci) == [(ci, "manual", "elle oluşturma", "ayse")]
    as_reconciler.execute("RESET ROLE")
    audit = fetch(as_reconciler, "SELECT DISTINCT reason, actor FROM audit.ci_change WHERE ci_sys_id = %s", ci)
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    assert audit == [("manual", "ayse")]
    # keşif aynı sunucuyu sonradan bulur: seri dizinde olduğu için elle açılan CI'a bağlanır, mükerrer açılmaz
    later = IngestRecord(data_source="BMC", run_id=uuid.uuid4(), source_key="elle-test/Host", class_hint="cmdb_ci_linux_server",
                         attributes={"name": "elle-srv-01", "serial_number": SERIAL, "os": "Ubuntu 24.04"},
                         observed_at=NOW + timedelta(hours=1))
    linked = store.apply(later)
    assert linked.outcome == "rule_match" and linked.ci_sys_id == ci
    # keşif sahipli alan artık keşiften; yönetişim alanı elle kalır
    assert fetch(as_reconciler, """SELECT class_attributes->>'os', short_description FROM cmdb.cmdb_ci
                                   WHERE sys_id = %s""", ci) == [("Ubuntu 24.04", "Laboratuvar sunucusu")]


def test_exact_identity_match_is_a_duplicate_and_cannot_be_forced(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    request(as_reconciler, "cmdb_ci_linux_server", {"name": "elle-srv-02", "serial_number": SERIAL})
    (first,) = store.apply_manual_creates()
    rid = request(as_reconciler, "cmdb_ci_linux_server", {"name": "baska-ad", "serial_number": SERIAL},
                  force=True, note="yine de aç")
    (res,) = store.apply_manual_creates()
    status, ci, result, note = outcome(as_reconciler, rid)
    assert (res.outcome, status, ci) == ("create_duplicate", "duplicate", first.ci_sys_id)
    assert result["outcome"] == "rule_match" and "elle-srv-02" in note and "yeni CI açılmadı" in note
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.cmdb_ci WHERE name = 'baska-ad'") == [(0,)]


def test_business_app_name_rule_makes_second_request_a_duplicate(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    name = f"Bordro-{uuid.uuid4().hex[:6]}"
    request(as_reconciler, "cmdb_ci_business_app", {"name": name})
    (first,) = store.apply_manual_creates()
    assert first.outcome == "created"
    request(as_reconciler, "cmdb_ci_business_app", {"name": name.upper()})
    (second,) = store.apply_manual_creates()
    assert second.outcome == "create_duplicate" and second.ci_sys_id == first.ci_sys_id  # sınıfın kuralı adla tanır


def test_hardware_name_rule_matches_like_ire(as_reconciler, metadata):
    """ServiceNow donanım kuralı adı son çare olarak kullanır (öncelik 300): adı aynı donanım kesin eşleşmedir."""
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    name = f"adsiz-{uuid.uuid4().hex[:6]}"
    request(as_reconciler, "cmdb_ci_linux_server", {"name": name})
    (first,) = store.apply_manual_creates()
    assert first.outcome == "created" and "Kimlik anahtarı (seri, FQDN, IP, MAC) yok" in first.summary
    request(as_reconciler, "cmdb_ci_linux_server", {"name": name.upper()})
    (second,) = store.apply_manual_creates()
    assert second.outcome == "create_duplicate" and second.ci_sys_id == first.ci_sys_id


def test_same_name_without_keys_needs_a_reasoned_decision(as_reconciler, metadata):
    """Ad kuralı olmayan sınıfta (sanal makine örneği: vCenter UUID + nesne kimliği) aynı ad yalnızca uyarıdır."""
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    name = f"vm-elle-{uuid.uuid4().hex[:6]}"
    request(as_reconciler, "cmdb_ci_vm_instance", {"name": name})
    (first,) = store.apply_manual_creates()
    assert first.outcome == "created" and "Kimlik anahtarı (seri, FQDN, IP, MAC) yok" in first.summary
    rid = request(as_reconciler, "cmdb_ci_vm_instance", {"name": name.upper()})
    (res,) = store.apply_manual_creates()
    status, ci, result, note = outcome(as_reconciler, rid)
    assert (res.outcome, status, ci) == ("create_needs_decision", "needs_decision", None)
    assert [c["sys_id"] for c in result["candidates"]] == [first.ci_sys_id] and "gerekçe" in note
    rid = request(as_reconciler, "cmdb_ci_vm_instance", {"name": name.upper()}, force=True,
                  note="Aynı adlı ikinci sanal makine; farklı küme")
    (forced,) = store.apply_manual_creates()
    status, ci, result, _ = outcome(as_reconciler, rid)
    assert forced.outcome == "created" and status == "created" and result["forced"] is True
    assert fetch(as_reconciler, "SELECT rule_ref FROM ingest.identity_link WHERE ci_sys_id = %s AND active", ci) == [
        ("elle oluşturma (gerekçeli)",)]
    # zorlama gerekçesiz olamaz (veritabanı)
    as_reconciler.execute("RESET ROLE")
    with pytest.raises(psycopg.errors.CheckViolation), as_reconciler.transaction():
        as_reconciler.execute("""INSERT INTO ingest.manual_create (sys_class_name, attributes, force_new,
                                 requested_by) VALUES ('cmdb_ci_server', '{"name": "x"}', true, 'ayse')""")
    as_reconciler.execute("SET ROLE evren_app_reconciler")


def test_root_class_is_rejected_and_reconciler_cannot_alter_the_request(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    rid = request(as_reconciler, "cmdb_ci", {"name": "kok"})
    (res,) = store.apply_manual_creates()
    assert res.outcome == "create_rejected" and outcome(as_reconciler, rid)[0] == "rejected"
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_reconciler.transaction():
        as_reconciler.execute("UPDATE ingest.manual_create SET attributes = '{}' WHERE sys_id = %s", (rid,))
