"""CMDB sağlık puanlaması (F1-09) — saf hesap: ölçüm oranlarından boyut ve genel puan.

Ölçümler (veritabanından toplanan sayaçlar) API'de toplanır; burada yalnızca kurallar (metadata/health_rules.yaml)
ve puan formülü vardır. Böylece formül veritabanından bağımsız test edilir ve tek yerde değişir.

Puanlar 0–100, bir ondalık. Ölçülemeyen ölçüm (paydası 0) puana katılmaz; tümü ölçülemezse boyut ``None``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["DIMENSIONS", "Ratio", "completeness_ratio", "dimension_score", "grade", "overall_score"]

DIMENSIONS = ("completeness", "correctness", "compliance", "coverage")
DIMENSION_TR = {"completeness": "Tamlık", "correctness": "Doğruluk", "compliance": "Uyum", "coverage": "Kapsam"}
DEFAULT_WEIGHTS = {"completeness": 30, "correctness": 30, "compliance": 20, "coverage": 20}
RECOMMENDED_WEIGHT = 0.5


@dataclass(frozen=True)
class Ratio:
    """``good`` / ``total``: iyi durumdaki sayı / ölçülen sayı (ör. dolu alan / beklenen alan)."""

    good: int
    total: int

    @property
    def pct(self) -> float | None:
        return round(100 * self.good / self.total, 1) if self.total else None

    @classmethod
    def from_bad(cls, bad: int, total: int) -> Ratio:
        """Sorun sayısından (ör. eskimiş CI) iyi oranı."""
        return cls(max(total - bad, 0), total)


def completeness_ratio(ci_count: int, required: Mapping[str, int], recommended: Mapping[str, int]) -> Ratio:
    """Zorunlu alanlar tam, önerilen alanlar yarım ağırlıkla. ``required``/``recommended``: alan → dolu CI sayısı.

    Ağırlıklı oran tamsayıya çevrilirken 2 ile çarpılır (önerilen alan = 1, zorunlu = 2 birim).
    """
    good = 2 * sum(required.values()) + sum(recommended.values())
    total = ci_count * (2 * len(required) + len(recommended))
    return Ratio(good, total)


def dimension_score(ratios: Sequence[Ratio]) -> float | None:
    """Boyut puanı: ölçülebilen oranların eşit ağırlıklı ortalaması."""
    pcts = [r.pct for r in ratios if r.pct is not None]
    return round(sum(pcts) / len(pcts), 1) if pcts else None


def overall_score(scores: Mapping[str, float | None], weights: Mapping[str, int] | None = None) -> float | None:
    """Genel puan: ölçülebilen boyutların ağırlıklı ortalaması (ağırlıklar ölçülebilenlere yeniden dağıtılır)."""
    w = dict(weights or DEFAULT_WEIGHTS)
    known = {d: s for d, s in scores.items() if s is not None and w.get(d)}
    total_w = sum(w[d] for d in known)
    return round(sum(s * w[d] for d, s in known.items()) / total_w, 1) if total_w else None


def grade(score: float | None, thresholds: Mapping[str, Any] | None = None) -> str:
    """``good`` / ``warn`` / ``bad`` / ``unknown`` (arayüz rengi ve uyarı için)."""
    if score is None:
        return "unknown"
    t = thresholds or {"good": 85, "warn": 70}
    return "good" if score >= t["good"] else "warn" if score >= t["warn"] else "bad"
