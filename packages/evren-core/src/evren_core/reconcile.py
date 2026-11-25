"""Öznitelik düzeyinde uzlaştırma — ServiceNow reconciliation & data refresh kuralları semantiği.

* Bir (sınıf, öznitelik) için kural varsa yalnızca yetkili kaynaklar o özniteliği
  yazabilir; öncelik numarası küçük olan kazanır.
* Kurallar alt sınıflara kalıtılır; alt sınıfta aynı öznitelik için kural
  tanımlanmışsa ata sınıfın kuralı devre dışı kalır (en özel seviye kazanır).
* Veri tazeleme kuralı: yüksek öncelikli kaynağın verisi belirtilen süreden eskiyse
  sıradaki yetkili kaynağın daha taze verisi kabul edilir.
* Kural yoksa en güncel gözlem kazanır.
* ``None`` değerler hiçbir zaman mevcut değeri silmez (null overwrite yok).

Her karar ``AttributeDecision`` olarak gerekçesiyle döner; UI'daki
"bu değer nereden geldi?" görünümü ve denetim izi bundan beslenir.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .classes import ClassHierarchy

__all__ = ["AttributeDecision", "DataRefreshRule", "Reconciler", "ReconciliationRule", "SourceValues"]


@dataclass(frozen=True)
class ReconciliationRule:
    applies_to: str
    data_source: str
    priority: int
    attributes: tuple[str, ...] | None = None  # None → tüm öznitelikler ("*")

    def covers(self, attribute: str) -> bool:
        return self.attributes is None or attribute in self.attributes


@dataclass(frozen=True)
class DataRefreshRule:
    applies_to: str
    data_source: str
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
    overridden: tuple[tuple[str, Any], ...] = ()
    unauthorized: tuple[str, ...] = ()


class Reconciler:
    def __init__(self, hierarchy: ClassHierarchy, rules: Iterable[ReconciliationRule] = (),
                 refresh_rules: Iterable[DataRefreshRule] = ()):
        self.hierarchy = hierarchy
        self.rules = list(rules)
        self.refresh_rules = list(refresh_rules)

    @classmethod
    def from_metadata(cls, hierarchy: ClassHierarchy, document: Mapping[str, Any]) -> Reconciler:
        rules = []
        for item in document.get("rules", []):
            attrs = item.get("attributes", "*")
            for source, priority in item["priorities"].items():
                rules.append(ReconciliationRule(item["applies_to"], source, int(priority),
                                                None if attrs == "*" else tuple(attrs)))
        refresh = [
            DataRefreshRule(r["applies_to"], r["data_source"], timedelta(days=float(r["effective_days"])))
            for r in document.get("data_refresh", [])
        ]
        return cls(hierarchy, rules, refresh)

    def authorized(self, class_name: str, attribute: str) -> dict[str, int] | None:
        """(sınıf, öznitelik) için yetkili kaynak → öncelik; kural yoksa ``None``."""
        for ancestor in self.hierarchy.ancestors(class_name):
            level = {r.data_source: r.priority for r in self.rules if r.applies_to == ancestor and r.covers(attribute)}
            if level:
                return level
        return None

    def refresh_window(self, class_name: str, data_source: str) -> timedelta | None:
        for ancestor in self.hierarchy.ancestors(class_name):
            for r in self.refresh_rules:
                if r.applies_to == ancestor and r.data_source == data_source:
                    return r.effective_duration
        return None

    def reconcile(self, class_name: str, inputs: Sequence[SourceValues], now: datetime) -> dict[str, AttributeDecision]:
        attributes = sorted({a for sv in inputs for a, v in sv.values.items() if v is not None})
        decisions: dict[str, AttributeDecision] = {}
        for attribute in attributes:
            candidates = [(sv.data_source, sv.values[attribute], sv.observed_at)
                          for sv in inputs if sv.values.get(attribute) is not None]
            auth = self.authorized(class_name, attribute)
            if auth is None:
                source, value, observed = max(candidates, key=lambda c: (c[2], c[0]))
                reason = "Kural yok: en güncel gözlem"
                unauthorized: tuple[str, ...] = ()
            else:
                eligible = sorted((c for c in candidates if c[0] in auth), key=lambda c: (auth[c[0]], -c[2].timestamp()))
                unauthorized = tuple(sorted({c[0] for c in candidates if c[0] not in auth}))
                if not eligible:
                    continue  # yetkili kaynak değer vermedi; mevcut değer korunur
                def is_stale(cand: tuple[str, Any, datetime]) -> bool:
                    window = self.refresh_window(class_name, cand[0])
                    return window is not None and now - cand[2] > window

                top = eligible[0]
                fresh = [c for c in eligible if not is_stale(c)]
                if is_stale(top) and fresh:
                    chosen = fresh[0]
                    reason = (f"Veri tazeleme kuralı: {top[0]} verisi {(now - top[2]).days} gün eski; "
                              f"{chosen[0]} kabul edildi")
                else:
                    chosen = top
                    reason = f"Uzlaştırma kuralı: {top[0]} önceliği {auth[top[0]]}"
                source, value, observed = chosen
            overridden = tuple((c[0], c[1]) for c in candidates if c[0] != source and c[1] != value)
            decisions[attribute] = AttributeDecision(attribute, value, source, observed, reason, overridden, unauthorized)
        return decisions
