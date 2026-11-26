"""Zaman içinde sorgu (F1-19, ADR-0031): geçmiş bir andaki öznitelik ve ilişki hâli, zaman çizelgesi anları."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evren_core.timeline import Change, RelationRow, attributes_at, group_moments, relations_at

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
A, B, C = "a" * 32, "b" * 32, "c" * 32
DEP = "Depends on::Used by"


def ch(day, seq, attribute, old, new, **kw):
    return Change(at=T0 + day * DAY, seq=seq, attribute=attribute, old=old, new=new, **kw)


HISTORY = [
    ch(0, 1, "name", None, "web-01"),
    ch(0, 2, "os", None, "RHEL 8"),
    ch(10, 3, "os", "RHEL 8", "RHEL 9"),
    ch(10, 4, "ram", None, 8192),
    ch(20, 5, "os", "RHEL 9", "RHEL 9.4"),
    ch(30, 6, "ram", 8192, None),
]
NOW = {"name": "web-01", "os": "RHEL 9.4", "ram": None}


def test_attributes_rewind_to_the_value_of_the_first_change_after_the_moment():
    assert attributes_at(NOW, HISTORY, T0 + 5 * DAY) == {"name": "web-01", "os": "RHEL 8"}
    assert attributes_at(NOW, HISTORY, T0 + 15 * DAY) == {"name": "web-01", "os": "RHEL 9", "ram": 8192}
    assert attributes_at(NOW, HISTORY, T0 + 25 * DAY) == {"name": "web-01", "os": "RHEL 9.4", "ram": 8192}
    assert attributes_at(NOW, HISTORY, T0 + 40 * DAY) == {"name": "web-01", "os": "RHEL 9.4"}
    # Oluşmadan önce: hiçbir değer yok
    assert attributes_at(NOW, HISTORY, T0 - DAY) == {}


def test_attributes_at_the_exact_moment_of_a_change_include_that_change():
    assert attributes_at(NOW, HISTORY, T0 + 10 * DAY)["os"] == "RHEL 9"


def test_same_moment_changes_are_ordered_by_sequence():
    hist = [ch(1, 1, "os", "A", "B"), ch(1, 2, "os", "B", "C")]
    assert attributes_at({"os": "C"}, hist, T0) == {"os": "A"}


def rel(parent, child, day):
    return RelationRow(DEP, parent, child, T0 + day * DAY)


def ev(day, seq, *, added=None, removed=None, effect=None):
    return ch(day, seq, "relation", removed and {"type": DEP, "parent": removed[0], "child": removed[1]},
              added and {"type": DEP, "parent": added[0], "child": added[1]},
              correlation={"effect": effect} if effect else {})


def test_relations_rewind_additions_and_removals():
    current = [rel(A, B, 0), rel(A, C, 12)]          # A→B eskiden beri; A→C 12. gün açıldı
    events = [ev(0, 1, added=(A, B)), ev(12, 2, added=(A, C)),
              ev(5, 3, added=(C, A)), ev(8, 4, removed=(C, A))]   # C→A 5. gün açıldı, 8. gün kalktı
    assert relations_at(current, events, T0 + 1 * DAY) == {(DEP, A, B)}
    assert relations_at(current, events, T0 + 6 * DAY) == {(DEP, A, B), (DEP, C, A)}
    assert relations_at(current, events, T0 + 9 * DAY) == {(DEP, A, B)}
    assert relations_at(current, events, T0 + 20 * DAY) == {(DEP, A, B), (DEP, A, C)}


def test_relation_removed_and_readded_and_events_without_effect():
    # 3. gün kalktı, 6. gün yeniden açıldı (yeni satır); 4. gün elle ekleme satırı değiştirmedi
    current = [rel(A, B, 6)]
    events = [ev(3, 1, removed=(A, B)), ev(4, 2, added=(A, B), effect="none"), ev(6, 3, added=(A, B))]
    assert relations_at(current, events, T0 + 1 * DAY) == {(DEP, A, B)}   # göç öncesi: olay yok, vardı
    assert relations_at(current, events, T0 + 5 * DAY) == set()
    assert relations_at(current, events, T0 + 7 * DAY) == {(DEP, A, B)}


def test_moments_group_rows_of_the_same_transaction_source_and_reason():
    rows = sorted(HISTORY, key=lambda c: (c.at, c.seq), reverse=True)
    moments = group_moments(rows)
    assert [(m.at.day, len(m.changes)) for m in moments] == [(1, 1), (21, 1), (11, 2), (1, 2)]
    assert moments[0].reason_label == "Uzlaştırma"
    manual = [ch(1, 1, "os", "A", "B", reason="manual", actor="ayse"), ch(1, 2, "ram", 1, 2, reason="reconciliation")]
    assert len(group_moments(manual)) == 2


def test_moments_join_a_run_whose_relations_were_written_at_run_end():
    run = {"run_id": "r-1"}
    rows = [ch(2, 9, "relation", None, {"type": DEP, "parent": A, "child": C}, source="BMC", correlation=run),
            ch(1, 8, "os", "A", "B", source="BMC", correlation=run),
            ch(1, 7, "os", None, "A", source="BMC", correlation={"run_id": "r-0"})]
    moments = group_moments(rows)
    assert [len(m.changes) for m in moments] == [2, 1]
    assert moments[0].at == T0 + 2 * DAY  # anın zamanı en yeni değişiklik
