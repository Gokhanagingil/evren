"""Denetim izi mühürleyicisi (U8.8, ADR-0039): ardışık zincirli mühür, yeniden çalıştırmada çift mühür yok, zincir
tetikleyicisi atlamayı ve sahte bağı reddeder, çalışan mühür değiştiremez/silemez, günlük doğrulama kurcalamayı yakalar
ve sonucu kanıt olarak yazar, mühür ve doğrulama olay omurgasına (SIEM) gider.

Her test tek bir işlemde çalışır ve sonunda geri alınır (``force_rollback``): paylaşılan test veritabanında kalıcı mühür
veya kurcalama bırakılmaz. Zaman ``now`` ile ileri alınır; geriye tarihli kayıt yalnızca işlem içindedir."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import psycopg
import pytest

from evren_core.audit import GENESIS, SealRecord, chain_of, digest_of
from evren_governance.audit_seal import SEAL_ACTOR, VERIFY_ACTOR, seal_streams, verify_recent

ROLE = "evren_app_governance"


@pytest.fixture
def tx():
    """Geri alınan işlem; veritabanında mühür varsa mühürleyici en sonuncusundan devam eder."""
    with psycopg.connect() as conn, conn.transaction(force_rollback=True):
        yield conn


def as_worker(conn):
    conn.execute(f"SET LOCAL ROLE {ROLE}")


def as_owner(conn):
    conn.execute("RESET ROLE")


def last_seal(conn, stream="security") -> SealRecord | None:
    row = conn.execute(f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s "
                       "ORDER BY period_start DESC LIMIT 1", (stream,)).fetchone()
    return SealRecord.from_row(row) if row else None


def test_seals_are_contiguous_chained_and_not_repeated(tx):
    as_worker(tx)
    later = datetime.now(UTC) + timedelta(hours=5)
    n = seal_streams(tx, now=later)
    assert n >= 3  # her akış en az bir dönem
    again = seal_streams(tx, now=later)
    assert again == 0  # aynı dönem iki kez mühürlenmez
    for stream in ("security", "ci", "task"):
        rows = [SealRecord.from_row(r) for r in tx.execute(
            f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s ORDER BY period_start",
            (stream,)).fetchall()]
        assert rows[0].prev_chain == GENESIS  # zincirin başı
        for a, b in pairwise(rows):
            assert a.period_end == b.period_start and b.prev_chain == a.chain
            assert b.chain == chain_of(b.prev_chain, b.digest)
        assert rows[-1].period_end <= later
    assert tx.execute("SELECT DISTINCT sealed_by FROM audit.seal WHERE sealed_at >= now()").fetchall() == [(SEAL_ACTOR,)]


def test_seal_counts_the_period_rows(tx):
    as_owner(tx)
    tx.execute("""INSERT INTO audit.security_event (principal, action, decision, detail)
                  VALUES ('muhur-test', 'login', 'allow', '{}'), ('muhur-test', 'logout', 'allow', '{}')""")
    as_worker(tx)
    seal_streams(tx, now=datetime.now(UTC) + timedelta(hours=3))
    s = tx.execute("""SELECT row_count, digest, period_start, period_end FROM audit.seal
                       WHERE stream = 'security' AND period_start <= now() AND period_end > now()""").fetchone()
    assert s is not None
    rows = tx.execute("""SELECT id, at, principal, action, resource, decision, detail, host(client_ip), request_id
                           FROM audit.security_event WHERE at >= %s AND at < %s ORDER BY id""", (s[2], s[3])).fetchall()
    assert (s[1], s[0]) == digest_of(rows) and s[0] >= 2


def test_link_trigger_rejects_gaps_and_forged_chains(tx):
    as_worker(tx)
    seal_streams(tx, now=datetime.now(UTC) + timedelta(hours=2))
    last = last_seal(tx)
    assert last is not None
    d = digest_of([])[0]
    insert = """INSERT INTO audit.seal (stream, period_start, period_end, row_count, digest, prev_chain, chain, sealed_by)
                VALUES ('security', %s, %s, 0, %s, %s, %s, 'saldirgan')"""
    for start, prev in ((last.period_end + timedelta(hours=1), last.chain),  # boşluk
                        (last.period_end, GENESIS)):                          # sahte bağ
        with pytest.raises(psycopg.errors.CheckViolation), tx.transaction():
            tx.execute(insert, (start, start + timedelta(hours=1), d, prev, chain_of(prev, d)))


@pytest.mark.parametrize("stmt", [
    "UPDATE audit.seal SET row_count = 0",
    "DELETE FROM audit.seal",
    "UPDATE audit.seal_check SET ok = true",
    "DELETE FROM audit.seal_check",
    "INSERT INTO audit.security_event (principal, action, decision) VALUES ('x', 'login', 'allow')",
    "UPDATE audit.security_event SET principal = 'x'",
    "SELECT count(*) FROM audit.review",
])
def test_worker_cannot_alter_seals_or_the_trail(tx, stmt):
    as_worker(tx)
    seal_streams(tx, now=datetime.now(UTC) + timedelta(hours=2))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tx.transaction():
        tx.execute(stmt)


def test_daily_verification_detects_tampering_and_records_evidence(tx):
    as_worker(tx)
    later = datetime.now(UTC) + timedelta(hours=3)
    seal_streams(tx, now=later)
    checked, failed = verify_recent(tx, now=later, force=True)
    assert checked >= 3 and failed == 0
    # Günde bir: hemen ikinci tur doğrulamaz
    assert verify_recent(tx, now=later + timedelta(hours=1)) == (0, 0)
    # Kurcalama: mühürlü döneme kayıt eklenir, bir kayıt değiştirilir, bir kayıt silinir
    s = last_seal(tx)
    as_owner(tx)
    tx.execute("""INSERT INTO audit.security_event (at, principal, action, decision, detail)
                  VALUES (%s, 'kurcalama', 'login', 'allow', '{}')""", (s.period_start + timedelta(minutes=5),))
    tx.execute("ALTER TABLE audit.ci_change DISABLE TRIGGER USER")
    ci = last_seal(tx, "ci")
    tx.execute("""UPDATE audit.ci_change SET actor = 'kurcalama'
                   WHERE id = (SELECT max(id) FROM audit.ci_change WHERE changed_at >= %s AND changed_at < %s)""",
               (ci.period_start, ci.period_end))
    as_worker(tx)
    checked, failed = verify_recent(tx, now=later + timedelta(days=1, minutes=1))
    assert failed >= 1
    problems = [p for (p,) in tx.execute("SELECT problem FROM audit.seal_check WHERE NOT ok AND checked_by = %s",
                                         (VERIFY_ACTOR,)).fetchall()]
    assert any("kayıt eklenmiş" in p for p in problems)
    if ci.row_count:
        assert any("değiştirilmiş" in p for p in problems)


def test_seals_and_checks_go_to_the_event_backbone(tx):
    as_worker(tx)
    later = datetime.now(UTC) + timedelta(hours=2)
    seal_streams(tx, now=later)
    verify_recent(tx, now=later, force=True)
    as_owner(tx)
    kinds = dict(tx.execute("""SELECT event_type, count(*) FROM events.outbox
                                WHERE event_type IN ('audit.seal', 'audit.seal_check') AND created_at >= now()
                                GROUP BY 1""").fetchall())
    assert kinds.get("audit.seal", 0) >= 3 and kinds.get("audit.seal_check", 0) >= 3
    tx.execute("INSERT INTO audit.security_event (principal, action, decision) VALUES ('siem-test', 'login', 'deny')")
    payload = tx.execute("""SELECT payload FROM events.outbox WHERE event_type = 'audit.security_event'
                             ORDER BY id DESC LIMIT 1""").fetchone()[0]
    assert payload["principal"] == "siem-test" and payload["decision"] == "deny" and "request_id" in payload
