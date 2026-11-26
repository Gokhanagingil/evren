"""İzinli ilişki kuralları (ADR-0019): hangi sınıf hangi tiple hangi sınıfa bağlanabilir.

ServiceNow bu listeyi yalnızca önerir (``cmdb_rel_type_suggest``). Evren aynı listeyi denetler; ``warn`` kipinde
izinsiz ilişki işaretlenir, ``enforce`` kipinde reddedilir (işletim U7'de). Kurallar alt sınıflara kalıtılır.

Karar üç değerlidir:

* ``allowed``     — bir kural (kalıtımla) eşleşir,
* ``reversed``    — ilişki ters yönde yazılmış; tersi izinli ("sunucu Runs on uygulama"),
* ``not_allowed`` — hiçbir kural eşleşmez.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .classes import ClassHierarchy

__all__ = ["ENFORCEMENT_MODES", "RelationRule", "RelationRules", "RelationVerdict"]

ENFORCEMENT_MODES = ("off", "warn", "enforce")


@dataclass(frozen=True)
class RelationRule:
    parent: str
    type: str
    child: str
    description: str | None = None
    verify: bool = False

    def as_json(self) -> dict[str, Any]:
        return {"parent": self.parent, "type": self.type, "child": self.child, "description": self.description,
                "verify": self.verify}


@dataclass(frozen=True)
class RelationVerdict:
    outcome: str  # allowed | reversed | not_allowed
    rule: RelationRule | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == "allowed"

    def message(self, parent_class: str, rel_type: str, child_class: str) -> str:
        if self.outcome == "allowed":
            return "İlişki izinli."
        if self.outcome == "reversed":
            return (f"Yön ters: {child_class} --{rel_type}--> {parent_class} izinli; "
                    f"ebeveyn ve çocuk yer değiştirmeli.")
        return f"İzinli ilişki kuralı yok: {parent_class} --{rel_type}--> {child_class}."


class RelationRules:
    def __init__(self, rules: Iterable[RelationRule], hierarchy: ClassHierarchy, enforcement: str = "warn") -> None:
        if enforcement not in ENFORCEMENT_MODES:
            raise ValueError(f"Geçersiz zorlama kipi: {enforcement}")
        self.rules = tuple(rules)
        self.hierarchy = hierarchy
        self.enforcement = enforcement
        self._by_type: dict[str, list[RelationRule]] = {}
        for r in self.rules:
            self._by_type.setdefault(r.type, []).append(r)

    @classmethod
    def from_metadata(cls, spec: Mapping[str, Any], hierarchy: ClassHierarchy) -> RelationRules:
        rules = [RelationRule(str(r["parent"]), str(r["type"]), str(r["child"]), r.get("description"),
                              bool(r.get("verify"))) for r in spec.get("rules") or []]
        return cls(rules, hierarchy, str(spec.get("enforcement", "warn")))

    def _match(self, parent_class: str, rel_type: str, child_class: str) -> RelationRule | None:
        if parent_class not in self.hierarchy or child_class not in self.hierarchy:
            return None
        p_chain, c_chain = self.hierarchy.ancestors(parent_class), self.hierarchy.ancestors(child_class)
        best: RelationRule | None = None
        best_rank = (10**6, 10**6)
        for r in self._by_type.get(rel_type, ()):
            if r.parent in p_chain and r.child in c_chain:
                rank = (p_chain.index(r.parent), c_chain.index(r.child))  # en özel kural açıklamada kullanılır
                if rank < best_rank:
                    best, best_rank = r, rank
        return best

    def check(self, parent_class: str, rel_type: str, child_class: str) -> RelationVerdict:
        rule = self._match(parent_class, rel_type, child_class)
        if rule is not None:
            return RelationVerdict("allowed", rule)
        rule = self._match(child_class, rel_type, parent_class)
        if rule is not None:
            return RelationVerdict("reversed", rule)
        return RelationVerdict("not_allowed")

    def suggestions(self, class_name: str) -> list[dict[str, Any]]:
        """Bir sınıf için izinli ilişkiler (arayüzde "ilişki ekle" önerisi): ebeveyn ve çocuk olarak."""
        if class_name not in self.hierarchy:
            return []
        chain = self.hierarchy.ancestors(class_name)
        out: list[dict[str, Any]] = []
        for r in self.rules:
            if r.parent in chain:
                out.append({"direction": "parent", "type": r.type, "other_class": r.child, "rule": r.as_json()})
            if r.child in chain:
                out.append({"direction": "child", "type": r.type, "other_class": r.parent, "rule": r.as_json()})
        return out
