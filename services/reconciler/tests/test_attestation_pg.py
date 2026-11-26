"""Sahiplik onayı (U6.4, F1-17, ADR-0027) — gerçek uzlaştırıcı, PostgreSQL, en az yetkili rol.

İstekler sahip rolüyle eklenir (API'nin yazdığı gibi), uzlaştırıcı ``evren_app_reconciler`` rolüyle uygular. Onay alanları
CI'ın Manual kaynak kaydından ve uzlaştırmadan geçer; kişi uygulama anında yeniden denetlenir; ret anomali açar; süre
dolumu ve sahip değişikliği onayı geçersiz kılar. Veriler sentetiktir.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from evren_core.envelope import IngestRecord
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def as_owner(conn, sql, *params):
    """Sahip rolüyle çalıştırır (API'nin veya kurulumun yazdığı satırlar). Hata olursa çağıranın kayıt noktası geri
    alınır ve rol de geri döner (SET ROLE işlemseldir)."""
    conn.execute("RESET ROLE")
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else None
    conn.execute("SET ROLE evren_app_reconciler")
    return rows


def user(conn, name: str) -> str:
    sys_id = uuid.uuid4().hex
    as_owner(conn, "INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'evren')",
             sys_id, f"{name}-{sys_id[:6]}", name.title())
    return sys_id


def server(store, tag: str, *, owner: str | None, cls: str = "cmdb_ci_linux_server", used_for: str = "Production",
           observed: datetime = NOW, run: uuid.UUID | None = None) -> str:
    attrs = {"name": f"onay-{tag}", "serial_number": f"ONAY{tag.upper()}", "used_for": used_for}
    if owner:
        attrs["owned_by"] = owner
    res = store.apply(IngestRecord(data_source="ServiceNow", run_id=run or uuid.uuid4(), source_key=f"sn-onay-{tag}",
                                   class_hint=cls, attributes=attrs, observed_at=observed))
    return res.ci_sys_id


def request(conn, ci, attester, decision="attested", *, reason=None, note=None, by="ayse") -> str:
    (rid,), = as_owner(conn, """INSERT INTO ingest.attestation_request (ci_sys_id, decision, reason, note, attester_user,
                                                                        requested_by)
                                VALUES (%s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                       ci, decision, reason, note, attester, by)
    return rid


def ci_state(conn, ci):
    (row,) = fetch(conn, """SELECT attestation_status, attested, attested_by, attested_date FROM cmdb.cmdb_ci
                            WHERE sys_id = %s""", ci)
    return row


def req_state(conn, rid):
    (row,) = fetch(conn, "SELECT status, result_note FROM ingest.attestation_request WHERE sys_id = %s", rid)
    return row


def test_owner_attests_through_manual_record_with_change_history(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    owner = user(as_reconciler, "sahip")
    tag = uuid.uuid4().hex[:6]
    ci = server(store, tag, owner=owner)
    rid = request(as_reconciler, ci, owner)
    (res,) = store.apply_attestations()
    assert res.outcome == "attest_applied" and {c["attribute"] for c in res.changes} >= {"attestation_status", "attested"}
    assert ci_state(as_reconciler, ci) == ("Attested", True, owner, NOW)
    status, note = req_state(as_reconciler, rid)
    assert status == "applied" and "Üretimdeki" in note and "25.05.2027" in note  # 180 gün
    (manual,) = fetch(as_reconciler, "SELECT attributes FROM ingest.source_record WHERE data_source = 'Manual' "
                                     "AND source_key = %s", ci)
    assert manual[0]["attestation_status"] == "Attested" and manual[0]["attested_by"] == owner
    audit = as_owner(as_reconciler, "SELECT DISTINCT reason, actor FROM audit.ci_change WHERE ci_sys_id = %s "
                                    "AND attribute LIKE 'attest%%'", ci)
    assert audit == [("attestation", "ayse")]
    # Keşif ve ITSM sonradan aynı CI'ı yazınca onay kalır (onayı yalnızca Manual yazar)
    server(store, tag, owner=owner, observed=NOW + DAY)
    assert ci_state(as_reconciler, ci)[0] == "Attested"


def test_attester_is_rechecked_when_applied(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    owner, other = user(as_reconciler, "sahip"), user(as_reconciler, "baskasi")
    ci = server(store, uuid.uuid4().hex[:6], owner=owner)
    rid = request(as_reconciler, ci, other)
    (res,) = store.apply_attestations()
    assert res.outcome == "attest_rejected" and req_state(as_reconciler, rid) == (
        "rejected", "Onay veren artık bu CI'ın sorumlusu değil; onay uygulanmadı")
    assert ci_state(as_reconciler, ci) == (None, None, None, None)
    vm = server(store, uuid.uuid4().hex[:6], owner=owner, cls="cmdb_ci_vmware_instance")  # kapsam dışı sınıf
    rid = request(as_reconciler, vm, owner)
    store.apply_attestations()
    assert req_state(as_reconciler, rid)[0] == "rejected" and "kapsamında değil" in req_state(as_reconciler, rid)[1]


def test_reject_opens_one_anomaly_and_later_attestation_resolves_it(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    owner = user(as_reconciler, "sahip")
    ci = server(store, uuid.uuid4().hex[:6], owner=owner)
    request(as_reconciler, ci, owner, "rejected", reason="not_exists", note="Sunucu Ekim'de kapatıldı")
    store.apply_attestations()
    assert ci_state(as_reconciler, ci)[:3] == ("Rejected", False, owner)
    anomalies = fetch(as_reconciler, """SELECT status, severity, explanation, details->>'reason' FROM insight.anomaly
                                        WHERE ci_sys_id = %s AND kind = 'attestation_rejected'""", ci)
    assert anomalies == [("open", "medium", "Sorumlu sahiplik onayını reddetti: Bu CI artık yok. Gerekçe: Sunucu "
                                            "Ekim'de kapatıldı", "not_exists")]
    rid = request(as_reconciler, ci, owner, "rejected", reason="data_wrong", note="Seri yanlış")
    store.apply_attestations()
    assert "açık anomali zaten var" in req_state(as_reconciler, rid)[1]
    request(as_reconciler, ci, owner)
    store.apply_attestations()
    assert ci_state(as_reconciler, ci)[0] == "Attested"
    (status, explanation, resolution), = fetch(
        as_reconciler, """SELECT status, explanation, details->>'resolution' FROM insight.anomaly
                          WHERE ci_sys_id = %s AND kind = 'attestation_rejected'""", ci)
    assert (status, resolution) == ("resolved", "attested") and explanation.endswith("sonradan onayladı.")


def test_expiry_and_owner_change_invalidate_attestation(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    owner, successor = user(as_reconciler, "sahip"), user(as_reconciler, "yeni")
    prod = server(store, uuid.uuid4().hex[:6], owner=owner)                       # üretim: 180 gün
    test = server(store, uuid.uuid4().hex[:6], owner=owner, used_for="Test")      # genel: 365 gün
    moved = server(store, tag := uuid.uuid4().hex[:6], owner=owner, used_for="Test")
    for ci in (prod, test, moved):
        request(as_reconciler, ci, owner)
        store.apply_attestations()
    assert store.expire_attestations() == {"expired": 0, "owner_changed": 0}
    server(store, tag, owner=successor, used_for="Test", observed=NOW + DAY)  # ITSM sorumluyu değiştirdi
    later = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW + 200 * DAY)
    assert later.expire_attestations() == {"expired": 1, "owner_changed": 1}
    assert ci_state(as_reconciler, prod) == ("Not Yet Reviewed", False, owner, NOW)  # son onayın kanıtı kalır
    assert ci_state(as_reconciler, test)[0] == "Attested"
    assert ci_state(as_reconciler, moved)[:2] == ("Not Yet Reviewed", False)
    assert later.expire_attestations() == {"expired": 0, "owner_changed": 0}  # tekrar yazmaz
    assert as_owner(as_reconciler, "SELECT DISTINCT reason, actor FROM audit.ci_change WHERE ci_sys_id = %s "
                                   "AND attribute = 'attestation_status' ORDER BY actor", prod) == [
        ("attestation", "ayse"), ("attestation", "evren_reconciler")]


def test_database_requires_reason_and_one_pending_request(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    owner = user(as_reconciler, "sahip")
    ci = server(store, uuid.uuid4().hex[:6], owner=owner)
    with pytest.raises(psycopg.errors.CheckViolation), as_reconciler.transaction():
        request(as_reconciler, ci, owner, "rejected", reason="not_owner", note="  ")
    with pytest.raises(psycopg.errors.CheckViolation), as_reconciler.transaction():
        request(as_reconciler, ci, owner, "attested", reason="not_owner")
    request(as_reconciler, ci, owner)
    with pytest.raises(psycopg.errors.UniqueViolation), as_reconciler.transaction():
        request(as_reconciler, ci, owner)
    (role,), = fetch(as_reconciler, "SELECT current_user")
    assert role == "evren_app_reconciler"
