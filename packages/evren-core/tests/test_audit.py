"""Denetim izi çekirdeği (U8.8, ADR-0039): olay kataloğu, sabit satır biçimi, özet ve zincir, mühür dönemleri, mühür
doğrulaması (ekleme, silme, değiştirme, mühür kaldırma ve mühür kaydının değiştirilmesi yakalanır), CSV koruması."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from itertools import pairwise

import pytest

from evren_core.audit import (
    ACTIONS,
    CATEGORIES,
    GENESIS,
    GRACE,
    SEAL_SQL,
    STREAMS,
    SealRecord,
    action_info,
    canonical_line,
    category_actions,
    chain_of,
    check_seal,
    digest_of,
    floor_hour,
    safe_cell,
    seal_windows,
    verify_chain,
)

T0 = datetime(2026, 11, 27, 8, 0, tzinfo=UTC)


def test_catalog_is_consistent_and_turkish():
    assert set(SEAL_SQL) == set(STREAMS)
    for action, (label, category) in ACTIONS.items():
        assert category in CATEGORIES and label and label[0].isupper(), action
    assert "audit_review" in category_actions("audit") and "login" in category_actions("access")


@pytest.mark.parametrize(("source", "action", "label", "category"), [
    ("security", "role_insert", "Rol verildi", "iam"),
    ("security", "write:cmdb_ci", "Yetki reddi: yazma (cmdb_ci)", "access"),
    ("security", "audit_read:audit", "Yetki reddi: denetim izi okuma (audit)", "access"),
    ("security", "yeni_bir_olay", "yeni_bir_olay", "other"),
    ("ci", "ci_attribute_reconciliation", "CI özniteliği değişti (uzlaştırma)", "cmdb"),
    ("ci", "ci_attribute_manual", "CI özniteliği değişti (elle düzenleme)", "cmdb"),
    ("task", "task_delivered", "Görev kanala teslim edildi", "governance"),
])
def test_action_info(source, action, label, category):
    assert action_info(source, action) == (label, category)


def test_canonical_line_is_stable_across_time_zones_and_key_order():
    istanbul = timezone(timedelta(hours=3))
    a = canonical_line([1, T0, {"b": 2, "a": [1, {"y": 1, "x": Decimal("1.50")}]}, None])
    b = canonical_line([1, T0.astimezone(istanbul), {"a": [1, {"x": Decimal("1.50"), "y": 1}], "b": 2}, None])
    assert a == b
    assert b'"2026-11-27T08:00:00.000000+00:00"' in a and b'"1.50"' in a
    # Bilinmeyen türler metin olur (ör. UUID); Türkçe harfler kaçışsız
    assert b"Ayr\xc4\xb1nt\xc4\xb1" in canonical_line(["Ayrıntı", uuid.UUID(int=1)])


def test_digest_and_chain():
    rows = [(1, T0, "a"), (2, T0, "b")]
    d, n = digest_of(rows)
    assert n == 2 and len(d) == 64
    assert digest_of(list(reversed(rows)))[0] != d  # sıra önemli
    assert digest_of([])[1] == 0
    assert chain_of(None, d) == chain_of(GENESIS, d) != chain_of(chain_of(None, d), d)


def test_first_seal_covers_history_then_hourly_without_gaps():
    now = datetime(2026, 11, 27, 10, 20, tzinfo=UTC)
    first = datetime(2026, 11, 20, 3, 5, tzinfo=UTC)
    assert seal_windows(None, first, now) == [(datetime(2026, 11, 20, 3, tzinfo=UTC), datetime(2026, 11, 27, 10, tzinfo=UTC))]
    # Hiç kayıt yok: son tamamlanmış saat (boş dönem de mühürlenir)
    assert seal_windows(None, None, now) == [(datetime(2026, 11, 27, 9, tzinfo=UTC), datetime(2026, 11, 27, 10, tzinfo=UTC))]
    # Bekleme süresi geçmeden dönem mühürlenmez
    assert seal_windows(None, datetime(2026, 11, 27, 9, 30, tzinfo=UTC), datetime(2026, 11, 27, 10, 10, tzinfo=UTC)) == []
    # Çalışan kapalı kaldı: aradaki saatler sırayla, sınırla
    gap = seal_windows(datetime(2026, 11, 27, 5, tzinfo=UTC), None, now)
    assert len(gap) == 5 and all(b == c for (_a, b), (c, _d) in pairwise(gap))
    assert len(seal_windows(datetime(2026, 1, 1, tzinfo=UTC), None, now, limit=10)) == 10
    assert all(end <= now - GRACE for _s, end in gap)
    assert floor_hour(datetime(2026, 11, 27, 13, 59, 59, tzinfo=timezone(timedelta(hours=3)))) == T0.replace(hour=10)


def seal_chain(periods):
    """Ardışık dönemler için doğru mühürler ve dönemlerin satırları."""
    seals, data, prev = [], {}, GENESIS
    for i, rows in enumerate(periods):
        start = T0 + timedelta(hours=i)
        d, n = digest_of(rows)
        chain = chain_of(prev, d)
        s = SealRecord("security", start, start + timedelta(hours=1), n, d, prev, chain, id=i + 1)
        seals.append(s)
        data[s.id] = rows
        prev = chain
    return seals, data


def test_verify_chain_accepts_an_untouched_trail():
    seals, data = seal_chain([[(1, T0, "a")], [], [(2, T0, "b"), (3, T0, "c")]])
    results = verify_chain(seals, None, lambda s: data[s.id])
    assert [p for _s, p in results] == [None, None, None]
    # Aralığın ortasından başlamak: önceki mühür verilir
    assert [p for _s, p in verify_chain(seals[1:], seals[0], lambda s: data[s.id])] == [None, None]


@pytest.mark.parametrize(("tamper", "needle"), [
    (lambda d: d[2].append((9, T0, "sonradan")), "kayıt eklenmiş"),
    (lambda d: d[3].pop(), "kayıt silinmiş"),
    (lambda d: d[3].__setitem__(0, (2, T0, "değişti")), "değiştirilmiş"),
])
def test_verify_chain_detects_record_tampering(tamper, needle):
    seals, data = seal_chain([[(1, T0, "a")], [], [(2, T0, "b"), (3, T0, "c")]])
    data = {k: list(v) for k, v in data.items()}
    tamper(data)
    problems = [p for _s, p in verify_chain(seals, None, lambda s: data[s.id]) if p]
    assert len(problems) == 1 and needle in problems[0]


def test_verify_chain_detects_removed_or_altered_seals():
    seals, data = seal_chain([[(1, T0, "a")], [(2, T0, "b")], [(3, T0, "c")]])
    rows = lambda s: data[s.id]  # noqa: E731
    # Ortadaki mühür kaldırıldı
    problems = [p for _s, p in verify_chain([seals[0], seals[2]], None, rows) if p]
    assert problems and "bağ kopuk" in problems[0]
    # Mühür kaydının sayısı/özeti değiştirildi (zincir artık tutmuyor)
    forged = replace(seals[1], row_count=0)
    assert "zincir değeri" in (check_seal(replace(forged, digest=digest_of([])[0]), seals[0], []) or "")
    # Zincirin başı kayıp (ilk mühür GENESIS ile başlamıyor)
    assert "ilk mührü eksik" in (check_seal(seals[1], None, data[2]) or "")
    # Dönem boşluğu (bağ doğru ama tarih atlıyor)
    shifted = replace(seals[1], period_start=seals[1].period_start + timedelta(minutes=30))
    assert "bitişik değil" in (check_seal(shifted, seals[0], data[2]) or "")


@pytest.mark.parametrize(("value", "expected"), [
    ("=SUM(A1)", "'=SUM(A1)"), ("+90 555", "'+90 555"), ("-1", "'-1"), ("@cmd", "'@cmd"), ("\tx", "'\tx"),
    ("normal", "normal"), (None, ""), (42, "42"), ("a=b", "a=b"),
])
def test_safe_cell(value, expected):
    assert safe_cell(value) == expected
