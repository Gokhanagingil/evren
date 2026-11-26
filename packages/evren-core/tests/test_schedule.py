"""Zamanlama ifadeleri (U5.3b, ADR-0021 §4): 5 alanlı cron ayrıştırıcı, sonraki çalışma, metadata doğrulaması."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from evren_core.metadata import validate
from evren_core.schedule import CronError, CronSchedule

IST = ZoneInfo("Europe/Istanbul")


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def test_fields_ranges_steps_and_lists():
    c = CronSchedule.parse("15 */2 * * *")
    assert c.minutes == {15} and c.hours == set(range(0, 24, 2))
    c = CronSchedule.parse("0-10/5,30 8-17 1,15 * 1-5")
    assert c.minutes == {0, 5, 10, 30} and c.hours == set(range(8, 18)) and c.days == {1, 15}
    assert c.weekdays == {1, 2, 3, 4, 5}
    assert CronSchedule.parse("5/20 * * * *").minutes == {5, 25, 45}
    assert CronSchedule.parse("0 0 * * 7").weekdays == {0}  # 7 = pazar
    assert CronSchedule.parse("  0   3 *  * * ").expression == "0 3 * * *"


@pytest.mark.parametrize("expr", [
    "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8",
    "*/0 * * * *", "10-5 * * * *", "a * * * *", "@daily", "0 3 * JAN *", "0 3 * * MON", "*/ * * * *", "1,,2 * * * *",
    "-1 * * * *",
])
def test_invalid_expressions_are_rejected(expr):
    with pytest.raises(CronError):
        CronSchedule.parse(expr)


def test_non_string_is_rejected():
    with pytest.raises(CronError):
        CronSchedule.parse(15)  # type: ignore[arg-type]


def test_matches_and_vixie_or_rule():
    c = CronSchedule.parse("30 2 * * *")
    assert c.matches(at(2026, 11, 26, 2, 30)) and not c.matches(at(2026, 11, 26, 2, 31))
    both = CronSchedule.parse("0 3 1 * 1")  # ayın 1'i VEYA pazartesi
    assert both.matches(at(2026, 12, 1, 3, 0))      # salı, ayın 1'i
    assert both.matches(at(2026, 11, 30, 3, 0))     # pazartesi
    assert not both.matches(at(2026, 11, 26, 3, 0))  # perşembe, 26'sı
    only_dow = CronSchedule.parse("0 3 * * 0")  # yalnızca pazar: VE (gün kısıtsız)
    assert only_dow.matches(at(2026, 11, 29, 3, 0)) and not only_dow.matches(at(2026, 11, 28, 3, 0))


def test_next_after_skips_current_minute_and_crosses_boundaries():
    c = CronSchedule.parse("15 */2 * * *")
    assert c.next_after(at(2026, 11, 26, 10, 15)) == at(2026, 11, 26, 12, 15)
    assert c.next_after(at(2026, 11, 26, 23, 59)) == at(2026, 11, 27, 0, 15)
    yearly = CronSchedule.parse("0 0 1 1 *")
    assert yearly.next_after(at(2026, 11, 26, 7, 0)) == at(2027, 1, 1, 0, 0)
    assert CronSchedule.parse("*/15 * * * *").next_after(at(2026, 11, 26, 7, 1)) == at(2026, 11, 26, 7, 15)
    leap = CronSchedule.parse("0 12 29 2 *")
    assert leap.next_after(at(2026, 11, 26, 7, 0)) == at(2028, 2, 29, 12, 0)


def test_impossible_date_returns_none():
    assert CronSchedule.parse("0 0 30 2 *").next_after(at(2026, 11, 26, 7, 0)) is None


def test_metadata_schedules_are_valid_and_validator_rejects_bad_ones(metadata):
    assert not [e for e in validate(metadata).errors if "zamanlama" in e]
    bad = dataclasses.replace(metadata, data_sources=[
        {**d, "schedule": "0 25 * * *"} if d["name"] == "Zabbix" else
        {**d, "schedule": "0 3 * * *"} if d["name"] == "Manual" else d for d in metadata.data_sources])
    errors = validate(bad).errors
    assert any(e.startswith("Zabbix: zamanlama geçersiz") for e in errors)
    assert any(e.startswith("Manual: bağlayıcısı olmayan") for e in errors)
