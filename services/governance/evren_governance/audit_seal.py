"""Denetim izi mühürleri (U8.8, ADR-0039): saatlik zincirli mühür ve günlük kendiliğinden doğrulama.

Görev çalışanı her turda bitmiş ve bekleme süresi (``GRACE``) geçmiş dönemleri mühürler; günde bir de son
``VERIFY_DAYS`` günün mühürlerini dönemin bugünkü kayıtlarıyla karşılaştırır ve sonucu ``audit.seal_check``'e yazar.
Her mühür ve her doğrulama aynı işlemde olay omurgasına (``evren.audit.v1`` → SIEM) gider (tetikleyici).

Çalışan mühür ekleyebilir; değiştiremez, silemez (veritabanı yetkisi ve salt ekleme tetikleyicisi). Bir yarışta ikinci
çalışanın eklemesi zincir tetikleyicisine veya benzersizlik kısıtına takılır ve o akış bir sonraki tura kalır.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from evren_core.audit import (
    FIRST_SQL,
    GENESIS,
    SEAL_SQL,
    STREAMS,
    SealRecord,
    chain_of,
    digest_of,
    seal_windows,
    verify_chain,
)

__all__ = ["SEAL_ACTOR", "VERIFY_ACTOR", "rows_for", "seal_streams", "verify_recent"]

log = logging.getLogger("evren.governance.audit")
SEAL_ACTOR = "evren:muhur"
VERIFY_ACTOR = "evren:muhur-dogrulama"
VERIFY_EVERY = timedelta(days=1)
VERIFY_DAYS = 7


def rows_for(conn: psycopg.Connection[Any], seal: SealRecord) -> Iterator[Sequence[Any]]:
    """Mührün döneminin bugünkü kayıtları (akış başına sabit sütun ve sıra; büyük dönemde satır satır)."""
    yield from conn.cursor().stream(SEAL_SQL[seal.stream], (seal.period_start, seal.period_end))


def _last_seal(conn: psycopg.Connection[Any], stream: str, before: datetime | None = None) -> SealRecord | None:
    if before is None:
        row = conn.execute(f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s "  # noqa: S608 — sabit sütun listesi
                           "ORDER BY period_start DESC LIMIT 1", (stream,)).fetchone()
    else:
        row = conn.execute(f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s AND period_start < %s "  # noqa: S608
                           "ORDER BY period_start DESC LIMIT 1", (stream, before)).fetchone()
    return SealRecord.from_row(row) if row else None


def seal_streams(conn: psycopg.Connection[Any], *, now: datetime | None = None, actor: str = SEAL_ACTOR) -> int:
    """Bekleyen dönemleri mühürler; mühürlenen dönem sayısı."""
    now = now or datetime.now(UTC)
    sealed = 0
    for stream in STREAMS:
        last = _last_seal(conn, stream)
        first = None if last else conn.execute(FIRST_SQL[stream]).fetchone()[0]
        prev = last.chain if last else GENESIS
        for start, end in seal_windows(last.period_end if last else None, first, now):
            probe = SealRecord(stream, start, end, 0, GENESIS, prev, GENESIS)
            digest, count = digest_of(rows_for(conn, probe))
            chain = chain_of(prev, digest)
            try:
                conn.execute("""INSERT INTO audit.seal (stream, period_start, period_end, row_count, digest, prev_chain,
                                                        chain, sealed_by)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                             (stream, start, end, count, digest, prev, chain, actor))
            except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation):
                log.info("mühür yarışı: %s akışı bir sonraki tura kaldı", stream)
                break
            prev = chain
            sealed += 1
    return sealed


def verify_recent(conn: psycopg.Connection[Any], *, now: datetime | None = None, force: bool = False,
                  days: int = VERIFY_DAYS) -> tuple[int, int]:
    """Günde bir (veya ``force``) son ``days`` günün mühürlerini doğrular. (doğrulanan, sorunlu) sayısı."""
    now = now or datetime.now(UTC)
    if not force:
        last = conn.execute("SELECT max(checked_at) FROM audit.seal_check WHERE checked_by = %s",
                            (VERIFY_ACTOR,)).fetchone()[0]
        if last is not None and now - last < VERIFY_EVERY:
            return 0, 0
    since = now - timedelta(days=days)
    checked = failed = 0
    for stream in STREAMS:
        seals = [SealRecord.from_row(r) for r in conn.execute(
            f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s AND period_end > %s "  # noqa: S608
            "ORDER BY period_start", (stream, since)).fetchall()]
        if not seals:
            continue
        previous = _last_seal(conn, stream, before=seals[0].period_start)
        for seal, problem in verify_chain(seals, previous, lambda s: rows_for(conn, s)):
            conn.execute("INSERT INTO audit.seal_check (seal, checked_by, ok, problem) VALUES (%s, %s, %s, %s)",
                         (seal.id, VERIFY_ACTOR, problem is None, problem))
            checked += 1
            if problem:
                failed += 1
                log.warning("denetim izi mührü doğrulanamadı: akış=%s dönem=%s %s", stream,
                            seal.period_start.isoformat(), problem)
    return checked, failed
