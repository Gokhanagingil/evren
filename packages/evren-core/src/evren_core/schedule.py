"""Zamanlama ifadeleri (U5.3b, ADR-0021 §4): 5 alanlı cron, standart kütüphane, ek bağımlılık yok.

    dakika saat ayın-günü ay haftanın-günü        ör. "15 */2 * * *", "30 2 * * 1-5", "0 3 1,15 * *"

* Desteklenen: ``*``, sayı, aralık ``a-b``, adım ``*/n`` ve ``a-b/n``, liste ``a,b,c``. Ay/gün adları ve ``@daily``
  gibi kısaltmalar desteklenmez (açık hata).
* Haftanın günü 0–7 (0 ve 7 pazar). Ayın günü ve haftanın günü ikisi de kısıtlıysa (``*`` değilse) Vixie cron gibi
  **VEYA** uygulanır: ``0 3 1 * 1`` → her ayın 1'i ve her pazartesi.
* Zaman dilimi çağıranındır: ``matches``/``next_after`` kendisine verilen yerel saatle çalışır (``zoneinfo``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["CronError", "CronSchedule"]

_FIELDS = (("dakika", 0, 59), ("saat", 0, 23), ("ayın günü", 1, 31), ("ay", 1, 12), ("haftanın günü", 0, 7))
_MAX_SCAN_MINUTES = 366 * 24 * 60 * 5  # 5 yıl: "30 şubat" gibi hiç gelmeyen ifadelerde sonsuz döngü yok


class CronError(ValueError):
    """Geçersiz zamanlama ifadesi."""


def _number(text: str, name: str, lo: int, hi: int) -> int:
    if not text.isdigit():
        raise CronError(f"{name}: '{text}' sayı değil")
    value = int(text)
    if not lo <= value <= hi:
        raise CronError(f"{name}: {value} {lo}–{hi} aralığında değil")
    return value


def _field(text: str, name: str, lo: int, hi: int) -> frozenset[int]:
    if not text:
        raise CronError(f"{name}: boş")
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = 1
        if step_text or part.endswith("/"):
            step = _number(step_text, name, 1, hi - lo + 1 if hi > lo else 1)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _number(a, name, lo, hi), _number(b, name, lo, hi)
            if start > end:
                raise CronError(f"{name}: aralık {start}-{end} ters")
        else:
            start = _number(base, name, lo, hi)
            end = hi if step_text else start  # "5/15" → 5'ten başlayarak 15'er
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = pazar … 6 = cumartesi (7 → 0)
    day_restricted: bool
    weekday_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> CronSchedule:
        if not isinstance(expression, str):
            raise CronError("zamanlama metin olmalı")
        parts = expression.split()
        if len(parts) != 5:
            raise CronError(f"zamanlama 5 alan olmalı (dakika saat gün ay haftanın-günü), {len(parts)} alan var")
        sets = [_field(p, name, lo, hi) for p, (name, lo, hi) in zip(parts, _FIELDS, strict=True)]
        weekdays = frozenset(0 if d == 7 else d for d in sets[4])
        return cls(" ".join(parts), sets[0], sets[1], sets[2], sets[3], weekdays,
                   day_restricted=parts[2] != "*", weekday_restricted=parts[4] != "*")

    def _day_matches(self, dt: datetime) -> bool:
        dom = dt.day in self.days
        dow = (dt.isoweekday() % 7) in self.weekdays  # isoweekday: pazartesi 1 … pazar 7
        if self.day_restricted and self.weekday_restricted:
            return dom or dow
        return dom and dow

    def matches(self, dt: datetime) -> bool:
        """Verilen yerel dakika zamanlamaya uyuyor mu (saniye yok sayılır)."""
        return (dt.minute in self.minutes and dt.hour in self.hours and dt.month in self.months
                and self._day_matches(dt))

    def next_after(self, dt: datetime) -> datetime | None:
        """``dt``'den sonraki ilk uyan dakika (aynı dakika dâhil değil). Hiç gelmeyen ifadede ``None``."""
        cur = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        scanned = 0
        while scanned < _MAX_SCAN_MINUTES:
            if cur.month not in self.months:
                # ayın başına atla
                year, month = (cur.year + 1, 1) if cur.month == 12 else (cur.year, cur.month + 1)
                nxt = cur.replace(year=year, month=month, day=1, hour=0, minute=0)
                scanned += int((nxt - cur).total_seconds() // 60)
                cur = nxt
                continue
            if not self._day_matches(cur):
                nxt = (cur + timedelta(days=1)).replace(hour=0, minute=0)
                scanned += int((nxt - cur).total_seconds() // 60)
                cur = nxt
                continue
            if cur.hour not in self.hours:
                nxt = (cur + timedelta(hours=1)).replace(minute=0)
                scanned += int((nxt - cur).total_seconds() // 60)
                cur = nxt
                continue
            if cur.minute in self.minutes:
                return cur
            cur += timedelta(minutes=1)
            scanned += 1
        return None
