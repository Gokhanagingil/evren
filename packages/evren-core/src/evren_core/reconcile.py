"""Öznitelik düzeyinde uzlaştırma — ServiceNow reconciliation & data refresh kuralları semantiği.

* Kurallar **kaynak rolüne** yazılır (ADR-0009); veri kaynağı → rol eşlemesi ``SourceRegistry``'dedir.
  Keşif ürünü değişse de kurallar değişmez.
* Bir (sınıf, öznitelik) için kural varsa yalnızca yetkili rollerdeki kaynaklar o özniteliği
  yazabilir; öncelik numarası küçük olan kazanır. Aynı roldeki kaynaklar ``role_rank`` ile sıralanır.
* Kurallar alt sınıflara kalıtılır; alt sınıfta aynı öznitelik için kural
  tanımlanmışsa ata sınıfın kuralı devre dışı kalır (en özel seviye kazanır).
* Veri tazeleme kuralı: yüksek öncelikli rolün verisi belirtilen süreden eskiyse
  sıradaki yetkili kaynağın daha taze verisi kabul edilir.
* Kural yoksa en güncel gözlem kazanır.
* ``None`` değerler hiçbir zaman mevcut değeri silmez (null overwrite yok).
* Gölge (``shadow``) kaynaklar karar vermez; değerleri karşılaştırma için ``shadow`` alanında döner.
  Emekli kaynaklar ve CI özniteliği yazmayan roller (ör. değişiklik kayıtları) yok sayılır.

Her karar ``AttributeDecision`` olarak gerekçesiyle döner; UI'daki
"bu değer nereden geldi?" görünümü ve denetim izi bundan beslenir.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .classes import ClassHierarchy
from .sources import SourceRegistry

__all__ = ["AttributeDecision", "DataRefreshRule", "Reconciler", "ReconciliationRule", "SourceValues"]


@dataclass(frozen=True)
class ReconciliationRule:
    applies_to: str
    role: str
    priority: int
    attributes: tuple[str, ...] | None = None  # None → tüm öznitelikler ("*")

    def covers(self, attribute: str) -> bool:
        return self.attributes is None or attribute in self.attributes


@dataclass(frozen=True)
class DataRefreshRule:
    applies_to: str
    role: str
    effective_duration: timedelta


@dataclass(frozen=True)
class SourceValues:
    data_source: str
    values: Mapping[str, Any]
    observed_at: datetime


@dataclass(frozen=True)
class AttributeDecision:
    attribute: str
    value: Any
    data_source: str
    observed_at: datetime
    reason: str
    role: str = ""
    overridden: tuple[tuple[str, Any], ...] = ()
    unauthorized: tuple[str, ...] = ()
    shadow: tuple[tuple[str, Any], ...] = ()


class Reconciler:
    def __init__(self, hierarchy: ClassHierarchy, registry: SourceRegistry,
                 rules: Iterable[ReconciliationRule] = (), refresh_rules: Iterable[DataRefreshRule] = ()):
        self.hierarchy = hierarchy
        self.registry = registry
        self.rules = list(rules)
        self.refresh_rules = list(refresh_rules)

    @classmethod
    def from_metadata(cls, hierarchy: ClassHierarchy, document: Mapping[str, Any],
                      registry: SourceRegistry) -> Reconciler:
        rules = []
        for item in document.get("rules", []):
            attrs = item.get("attributes", "*")
            for role, priority in item["priorities"].items():
                rules.append(ReconciliationRule(item["applies_to"], role, int(priority),
                                                None if attrs == "*" else tuple(attrs)))
        refresh = [
            DataRefreshRule(r["applies_to"], r["role"], timedelta(days=float(r["effective_days"])))
            for r in document.get("data_refresh", [])
        ]
        return cls(hierarchy, registry, rules, refresh)

    def authorized(self, class_name: str, attribute: str) -> dict[str, int] | None:
        """(sınıf, öznitelik) için yetkili rol → öncelik; kural yoksa ``None``."""
        for ancestor in self.hierarchy.ancestors(class_name):
            level = {r.role: r.priority for r in self.rules if r.applies_to == ancestor and r.covers(attribute)}
            if level:
                return level
        return None

    def authorized_sources(self, class_name: str, attribute: str) -> list[tuple[str, int, int]]:
        """Yetkili veri kaynakları (ad, öncelik, rank) — arayüzdeki kural ızgarası için."""
        auth = self.authorized(class_name, attribute) or {}
        out = [(s.name, auth[s.role], s.role_rank) for s in self.registry.sources.values()
               if s.role in auth and self.registry.writes_attributes(s.name)]
        return sorted(out, key=lambda x: (x[1], x[2], x[0]))

    def refresh_window(self, class_name: str, role: str) -> timedelta | None:
        for ancestor in self.hierarchy.ancestors(class_name):
            for r in self.refresh_rules:
                if r.applies_to == ancestor and r.role == role:
                    return r.effective_duration
        return None

    def reconcile(self, class_name: str, inputs: Sequence[SourceValues], now: datetime) -> dict[str, AttributeDecision]:
        reg = self.registry
        writers = [sv for sv in inputs if reg.writes_attributes(sv.data_source)]
        shadows = [sv for sv in inputs if reg.is_shadow(sv.data_source)]
        attributes = sorted({a for sv in writers for a, v in sv.values.items() if v is not None})
        decisions: dict[str, AttributeDecision] = {}
        for attribute in attributes:
            candidates = [(sv.data_source, sv.values[attribute], sv.observed_at)
                          for sv in writers if sv.values.get(attribute) is not None]
            auth = self.authorized(class_name, attribute)
            if auth is None:
                source, value, observed = max(candidates, key=lambda c: (c[2], c[0]))
                reason = "Kural yok: en güncel gözlem"
                unauthorized: tuple[str, ...] = ()
            else:
                ranks = auth
                eligible = sorted((c for c in candidates if reg.role_of(c[0]) in ranks),
                                  key=lambda c: (ranks[reg.role_of(c[0])], reg.rank_of(c[0]), -c[2].timestamp()))
                unauthorized = tuple(sorted({c[0] for c in candidates if reg.role_of(c[0]) not in auth}))
                if not eligible:
                    continue  # yetkili kaynak değer vermedi; mevcut değer korunur

                def is_stale(cand: tuple[str, Any, datetime]) -> bool:
                    window = self.refresh_window(class_name, reg.role_of(cand[0]))
                    return window is not None and now - cand[2] > window

                top = eligible[0]
                fresh = [c for c in eligible if not is_stale(c)]
                if is_stale(top) and fresh:
                    chosen = fresh[0]
                    reason = (f"Veri tazeleme kuralı: {top[0]} verisi {(now - top[2]).days} gün eski; "
                              f"{chosen[0]} kabul edildi")
                else:
                    chosen = top
                    reason = f"Uzlaştırma kuralı: {top[0]} ({reg.role_of(top[0])}) önceliği {auth[reg.role_of(top[0])]}"
                source, value, observed = chosen
            overridden = tuple((c[0], c[1]) for c in candidates if c[0] != source and c[1] != value)
            shadow = tuple((sv.data_source, sv.values[attribute]) for sv in shadows
                           if sv.values.get(attribute) is not None)
            decisions[attribute] = AttributeDecision(attribute, value, source, observed, reason, reg.role_of(source),
                                                     overridden, unauthorized, shadow)
        return decisions
