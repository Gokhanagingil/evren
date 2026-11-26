"""Yetkisiz değişiklik tespiti (F1-04, ADR-0025): kural dosyası, onaylı pencere ve değerlendirme. Saf mantık, G/Ç yok.

Gözlem: gözlemci rolündeki kaynağın kendi kaydında izlenen öznitelik değişti; değişiklik ``observed_from`` (eski değerin
son görüldüğü an) ile ``observed_at`` (yeni değerin ilk görüldüğü an) arasındadır. Aday değişiklik kayıtları CI'ın
kendisine (``cmdb_ci``) veya etkilenen CI listesine (``task_ci``) bağlı olanlardır; seçimi çağıran yapar.

Kayıt gözlemi kapsar, eğer:

* **yetkiliyse** — durumu ``authorization.states`` içinde ve onayı ``approvals`` içinde (önceden onaylı türde onay
  aranmaz); iptal/ret/taslak kapsamaz,
* **penceresi** (planlı ``start_date``–``end_date`` veya gerçekleşen ``work_start``–``work_end``; uygulamadaki kaydın
  gerçekleşen penceresi şimdiye uzar) toleransla gözlem aralığıyla örtüşüyorsa.

Değerler Evren'in değişiklik sözlüğündedir (``evren_core.reference``); ürün kodu eşleme dosyasında çevrilir.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .classes import ClassHierarchy
from .reference import CHANGE_APPROVALS, CHANGE_STATES, CHANGE_TYPES

__all__ = ["ANOMALY_STATUS_TR", "APPROVAL_TR", "DISABLED", "SEVERITIES", "STATE_TR", "TYPE_TR", "VERDICT_TR",
           "ChangeControlError", "ChangePolicy", "ChangeRecord", "Verdict", "evaluate", "validate_change_control"]

SEVERITIES = ("low", "medium", "high", "critical")
LOCAL_TZ = ZoneInfo("Europe/Istanbul")
STATE_TR = {"new": "yeni", "assess": "değerlendirme", "authorize": "yetkilendirme", "scheduled": "planlandı",
            "implement": "uygulamada", "review": "gözden geçirme", "closed": "kapandı", "canceled": "iptal",
            "other": "tanınmayan"}
APPROVAL_TR = {"not_requested": "istenmedi", "requested": "bekliyor", "approved": "onaylandı", "rejected": "reddedildi",
               "cancelled": "iptal", "not_required": "gerekmiyor", "skipped": "atlandı", "other": "tanınmayan"}
TYPE_TR = {"standard": "standart", "normal": "normal", "emergency": "acil", "other": "tanınmayan"}
VERDICT_TR = {"pending": "Değerlendirme bekliyor", "authorized": "Onaylı değişiklik",
              "unauthorized": "Yetkisiz değişiklik", "suppressed": "Değerlendirilmedi (devre kesici)"}
ANOMALY_STATUS_TR = {"open": "Açık", "acknowledged": "İncelendi", "resolved": "Kapandı", "false_positive": "Yanlış alarm"}
_MAX_CONSIDERED = 10


class ChangeControlError(ValueError):
    """Kural dosyası geçersiz."""


def fmt_local(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


@dataclass(frozen=True)
class SeverityRule:
    field: str
    equals: Any
    severity: str


@dataclass(frozen=True)
class ChangePolicy:
    observer_roles: frozenset[str]
    watched: Mapping[str, frozenset[str]]
    states: frozenset[str]
    approvals: frozenset[str]
    preapproved_types: frozenset[str]
    tolerance: timedelta = timedelta(minutes=60)
    grace: timedelta = timedelta(minutes=60)
    recheck: timedelta = timedelta(days=7)
    severity_default: str = "medium"
    severity_rules: tuple[SeverityRule, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.observer_roles and self.watched)

    @classmethod
    def from_document(cls, doc: Mapping[str, Any] | None) -> ChangePolicy:
        """``metadata/change_control.yaml`` belgesi → kural nesnesi. Belge yoksa tespit kapalıdır (``DISABLED``)."""
        if not doc:
            return DISABLED
        errors = _structural_errors(doc)
        if errors:
            raise ChangeControlError("; ".join(errors))
        auth = doc.get("authorization") or {}
        ev = doc.get("evaluation") or {}
        sev = doc.get("severity") or {}
        return cls(
            observer_roles=frozenset(doc.get("observer_roles") or ()),
            watched={c: frozenset(a) for c, a in (doc.get("watched") or {}).items()},
            states=frozenset(auth.get("states") or ()), approvals=frozenset(auth.get("approvals") or ()),
            preapproved_types=frozenset(auth.get("preapproved_types") or ()),
            tolerance=timedelta(minutes=int(auth.get("tolerance_minutes", 60))),
            grace=timedelta(minutes=int(ev.get("grace_minutes", 60))),
            recheck=timedelta(days=int(ev.get("recheck_days", 7))),
            severity_default=str(sev.get("default", "medium")),
            severity_rules=tuple(SeverityRule(str(r["field"]), r["equals"], str(r["severity"]))
                                 for r in sev.get("rules") or ()))

    def watched_for(self, class_name: str, hierarchy: ClassHierarchy) -> frozenset[str]:
        """Sınıf için izlenen öznitelikler (üst sınıflardan kalıtılır)."""
        if class_name not in hierarchy:
            return frozenset()
        out: set[str] = set()
        for ancestor in hierarchy.ancestors(class_name):
            out |= self.watched.get(ancestor, frozenset())
        return frozenset(out)

    def severity_for(self, ci: Mapping[str, Any]) -> str:
        for rule in self.severity_rules:
            if ci.get(rule.field) == rule.equals:
                return rule.severity
        return self.severity_default


DISABLED = ChangePolicy(frozenset(), {}, frozenset(), frozenset(), frozenset())


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _structural_errors(doc: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    auth = doc.get("authorization") or {}
    ev = doc.get("evaluation") or {}
    sev = doc.get("severity") or {}
    for key, allowed in (("states", CHANGE_STATES), ("approvals", CHANGE_APPROVALS),
                         ("preapproved_types", CHANGE_TYPES)):
        values = auth.get(key) or []
        if not isinstance(values, list):
            errors.append(f"authorization.{key}: liste olmalı")
            continue
        errors += [f"authorization.{key}: sözlükte olmayan değer {v!r}" for v in values if v not in allowed]
        if "other" in values:
            errors.append(f"authorization.{key}: tanınmayan değer (other) yetki sayılamaz")
    if not auth.get("states"):
        errors.append("authorization.states boş olamaz")
    if not auth.get("approvals"):
        errors.append("authorization.approvals boş olamaz")
    for path, value in (("authorization.tolerance_minutes", auth.get("tolerance_minutes", 60)),
                        ("evaluation.grace_minutes", ev.get("grace_minutes", 60)),
                        ("evaluation.recheck_days", ev.get("recheck_days", 7))):
        if not _positive(value):
            errors.append(f"{path}: pozitif tam sayı olmalı")
    if sev.get("default", "medium") not in SEVERITIES:
        errors.append(f"severity.default: {', '.join(SEVERITIES)} olmalı")
    for i, rule in enumerate(sev.get("rules") or []):
        if not isinstance(rule, Mapping) or set(rule) != {"field", "equals", "severity"}:
            errors.append(f"severity.rules[{i}]: field, equals, severity zorunlu")
        elif rule["severity"] not in SEVERITIES:
            errors.append(f"severity.rules[{i}]: önem {', '.join(SEVERITIES)} olmalı")
    if not isinstance(doc.get("observer_roles") or [], list):
        errors.append("observer_roles: liste olmalı")
    watched = doc.get("watched") or {}
    if not isinstance(watched, Mapping) or not all(isinstance(v, list) and v for v in watched.values()):
        errors.append("watched: sınıf → öznitelik listesi olmalı")
    return errors


def validate_change_control(doc: Mapping[str, Any] | None, *, roles: set[str], hierarchy: ClassHierarchy,
                            fields_of: Callable[[str], Mapping[str, Any]], products: Sequence[str] = ()) -> list[str]:
    """Kural dosyasını sözlüğe karşı doğrular: roller, sınıflar, alanlar, sözlük değerleri, ürün adı yok (kural 8)."""
    if not doc:
        return []
    errors = [f"change_control: {e}" for e in _structural_errors(doc)]
    errors += [f"change_control: tanımsız rol {r}" for r in doc.get("observer_roles") or [] if r not in roles]
    for cls, attrs in (doc.get("watched") or {}).items():
        if cls not in hierarchy:
            errors.append(f"change_control.watched: tanımsız sınıf {cls}")
            continue
        known = fields_of(cls)
        errors += [f"change_control.watched.{cls}: sınıfta olmayan alan {a}" for a in attrs if a not in known]
    base = fields_of("cmdb_ci") if "cmdb_ci" in hierarchy else {}
    for rule in (doc.get("severity") or {}).get("rules") or []:
        if isinstance(rule, Mapping) and rule.get("field") not in base:
            errors.append(f"change_control.severity: cmdb_ci'da olmayan alan {rule.get('field')}")
    text = " ".join(_strings(doc)).lower()
    errors += [f"change_control: ürün adı içeremez ({p})" for p in products
               if p and re.search(rf"(?<![\w]){re.escape(p.lower())}(?![\w])", text)]
    return errors


def _strings(value: Any) -> list[str]:
    """Belgedeki bütün metin değerleri ve anahtarları (ürün adı denetimi için)."""
    if isinstance(value, Mapping):
        return [s for k, v in value.items() for s in (str(k), *_strings(v))]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return [str(value)]


# ---------------------------------------------------------------------------------------------------- değerlendirme
@dataclass(frozen=True)
class ChangeRecord:
    sys_id: str
    number: str
    type: str | None = None
    state: str | None = None
    approval: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    work_start: datetime | None = None
    work_end: datetime | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ChangeRecord:
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})

    def authorization(self, policy: ChangePolicy) -> str | None:
        """Yetkili değilse Türkçe gerekçe; yetkiliyse ``None``."""
        state_ok = self.state in policy.states
        approval_ok = self.approval in policy.approvals or self.type in policy.preapproved_types
        if state_ok and approval_ok:
            return None
        return (f"yetkili değil (tür: {TYPE_TR.get(self.type or '', 'yok')}, "
                f"durum: {STATE_TR.get(self.state or '', 'yok')}, onay: {APPROVAL_TR.get(self.approval or '', 'yok')})")

    def windows(self, now: datetime) -> list[tuple[str, datetime, datetime]]:
        out: list[tuple[str, datetime, datetime]] = []
        if self.start_date and self.end_date and self.end_date >= self.start_date:
            out.append(("planlı", self.start_date, self.end_date))
        if self.work_start:
            end = self.work_end or (now if self.state == "implement" else None)
            if end and end >= self.work_start:
                out.append(("gerçekleşen", self.work_start, end))
        return out


@dataclass(frozen=True)
class Verdict:
    authorized: bool
    change: ChangeRecord | None = None
    window: str | None = None
    considered: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def explanation(self, observed_from: datetime | None, observed_at: datetime) -> str:
        span = (f"{fmt_local(observed_from)} – {fmt_local(observed_at)}" if observed_from
                else fmt_local(observed_at))
        if self.authorized and self.change:
            return f"{self.change.number} ({self.window} pencere) gözlem aralığını ({span}) kapsıyor."
        if not self.considered:
            return f"Gözlem aralığında ({span}) CI'a bağlı değişiklik kaydı yok."
        return (f"Gözlem aralığını ({span}) kapsayan onaylı değişiklik kaydı yok; bakılan kayıtlar: "
                + "; ".join(f"{c['number']}: {c['reason']}" for c in self.considered))


def evaluate(observed_from: datetime | None, observed_at: datetime, candidates: Sequence[ChangeRecord],
             policy: ChangePolicy, now: datetime) -> Verdict:
    """Gözlem aralığını kapsayan yetkili değişiklik kaydını arar; yoksa neden kapsamadıklarını listeler."""
    lo = observed_from or observed_at
    hi = observed_at
    tol = policy.tolerance
    considered: list[dict[str, str]] = []
    for c in sorted(candidates, key=lambda c: (c.number, c.sys_id)):
        reason = c.authorization(policy)
        windows = c.windows(now)
        if reason is None:
            for label, start, end in windows:
                if start - tol <= hi and end + tol >= lo:
                    return Verdict(True, c, label, tuple(considered[:_MAX_CONSIDERED]))
            reason = ("planlı veya gerçekleşen pencere yok" if not windows else
                      "pencere (" + ", ".join(f"{lb} {fmt_local(s)} – {fmt_local(e)}" for lb, s, e in windows)
                      + ") gözlem aralığıyla örtüşmüyor")
        considered.append({"number": c.number, "reason": reason})
    return Verdict(False, None, None, tuple(considered[:_MAX_CONSIDERED]))
