"""Eskime işletimi (F1-06, ADR-0028): kural seçimi ve CI başına karar. Saf mantık, G/Ç yok.

Kural ``metadata/reconciliation_rules.yaml`` → ``staleness``; sınıf hiyerarşisinde en yakın atanın kuralı geçerlidir.
Karar yalnızca gözlemci rollerin kaynak kayıtlarıyla verilir (``SourceRegistry.counts_for_staleness``):

* gözlemci kaydı yoksa → değerlendirilmez (``not_observed``),
* bir gözlemci kaydı ``active`` ise → görülüyor (``seen``),
* bağlı bir gözlemci kaynağın son çalışması tamam ve ``OK`` değilse → karar verilmez (``suppressed``, kural 4),
* bütün gözlemci kayıtları ``missing`` ise en yeni son görülmeye göre: ``stale`` (eskimiş) veya ``candidate``
  (emekliye ayırma adayı); süre dolmadıysa ``absent`` (görülmüyor ama süre dolmadı).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .classes import ClassHierarchy

__all__ = ["STAGE_TR", "Observation", "StalenessError", "StalenessPolicy", "StalenessRule", "Verdict",
           "validate_staleness"]

STAGE_TR = {"stale": "Eskimiş", "candidate": "Emekliye ayırma adayı"}
OUTCOME_TR = {"not_applicable": "Eskime kuralı yok", "not_observed": "Gözlemci kaynak kaydı yok",
              "seen": "Görülüyor", "suppressed": "Karar verilmedi (kaynak sağlıksız)", "absent": "Görülmüyor; süre dolmadı",
              "stale": STAGE_TR["stale"], "candidate": STAGE_TR["candidate"]}
#: Emekliye ayırmada CI'ın Manual kaydına yazılan yaşam döngüsü değerleri (ADR-0028 §4)
RETIRED_VALUES: dict[str, Any] = {"life_cycle_stage": "End of Life", "life_cycle_stage_status": "Retired",
                                  "install_status": 7, "operational_status": 6}


class StalenessError(ValueError):
    """Eskime kuralı geçersiz."""


@dataclass(frozen=True)
class StalenessRule:
    applies_to: str
    stale_after: timedelta
    candidate_after: timedelta
    require_all_sources_absent: bool = True

    def as_json(self) -> dict[str, Any]:
        return {"applies_to": self.applies_to, "stale_after_days": self.stale_after.days,
                "candidate_after_days": self.candidate_after.days,
                "require_all_sources_absent": self.require_all_sources_absent}


@dataclass(frozen=True)
class Observation:
    """Bir gözlemci kaynağın CI'a bağlı kaydı ve kaynağın son çalışması."""
    data_source: str
    last_seen: datetime
    status: str  # active | missing
    source_ok: bool  # son çalışma tamamlandı ve devre kesici OK

    def as_json(self) -> dict[str, Any]:
        return {"data_source": self.data_source, "last_seen": self.last_seen.isoformat(), "status": self.status,
                "source_ok": self.source_ok}


@dataclass(frozen=True)
class Verdict:
    outcome: str  # not_applicable | not_observed | seen | suppressed | absent | stale | candidate
    rule: StalenessRule | None = None
    last_seen: datetime | None = None
    observations: tuple[Observation, ...] = field(default_factory=tuple)

    @property
    def stage(self) -> str | None:
        return self.outcome if self.outcome in ("stale", "candidate") else None

    @property
    def label(self) -> str:
        return OUTCOME_TR[self.outcome]


@dataclass(frozen=True)
class StalenessPolicy:
    rules: Mapping[str, StalenessRule]

    @classmethod
    def from_rules(cls, items: Iterable[Mapping[str, Any]]) -> StalenessPolicy:
        rules: dict[str, StalenessRule] = {}
        for r in items:
            rule = StalenessRule(str(r["applies_to"]), timedelta(days=float(r["stale_after_days"])),
                                 timedelta(days=float(r["absent_candidate_after_days"])),
                                 bool(r.get("require_all_sources_absent", True)))
            if rule.candidate_after <= rule.stale_after:
                raise StalenessError(f"{rule.applies_to}: absent_candidate_after_days > stale_after_days olmalı")
            if not rule.require_all_sources_absent:
                raise StalenessError(f"{rule.applies_to}: require_all_sources_absent: false bu sürümde desteklenmez")
            rules[rule.applies_to] = rule
        return cls(rules)

    def rule_for(self, class_name: str, hierarchy: ClassHierarchy) -> StalenessRule | None:
        """Sınıfın veya en yakın atasının kuralı."""
        if class_name not in hierarchy:
            return None
        for anc in hierarchy.ancestors(class_name):
            if anc in self.rules:
                return self.rules[anc]
        return None

    @property
    def classes(self) -> list[str]:
        return sorted(self.rules)

    def evaluate(self, class_name: str, observations: Sequence[Observation], hierarchy: ClassHierarchy,
                 now: datetime) -> Verdict:
        rule = self.rule_for(class_name, hierarchy)
        obs = tuple(sorted(observations, key=lambda o: o.data_source))
        if rule is None:
            return Verdict("not_applicable", None, None, obs)
        if not obs:
            return Verdict("not_observed", rule, None, obs)
        last_seen = max(o.last_seen for o in obs)
        if any(o.status == "active" for o in obs):
            return Verdict("seen", rule, last_seen, obs)
        if not all(o.source_ok for o in obs):
            return Verdict("suppressed", rule, last_seen, obs)
        age = now - last_seen
        if age >= rule.candidate_after:
            return Verdict("candidate", rule, last_seen, obs)
        if age >= rule.stale_after:
            return Verdict("stale", rule, last_seen, obs)
        return Verdict("absent", rule, last_seen, obs)


def validate_staleness(items: Iterable[Mapping[str, Any]], hierarchy: ClassHierarchy) -> list[str]:
    """``staleness`` kurallarını doğrular: sınıf tanımlı, süreler pozitif ve sıralı, tüm kaynaklar şartı açık."""
    errors: list[str] = []
    seen: set[str] = set()
    for r in items:
        cls = r.get("applies_to")
        if cls not in hierarchy:
            errors.append(f"Eskime kuralı sınıfı tanımsız: {cls}")
        if cls in seen:
            errors.append(f"Eskime kuralı tekrarlanıyor: {cls}")
        seen.add(str(cls))
        try:
            stale, cand = float(r["stale_after_days"]), float(r["absent_candidate_after_days"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{cls}: stale_after_days ve absent_candidate_after_days sayı olmalı")
            continue
        if stale <= 0:
            errors.append(f"{cls}: stale_after_days pozitif olmalı")
        if cand <= stale:
            errors.append(f"{cls}: absent_candidate_after_days > stale_after_days olmalı")
        if r.get("require_all_sources_absent", True) is not True:
            errors.append(f"{cls}: require_all_sources_absent: false bu sürümde desteklenmez (ADR-0028)")
    return errors
