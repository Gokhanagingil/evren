"""Zaman içinde sorgu (F1-19, ADR-0031): CI'ın geçmiş bir andaki hâli değişim geçmişinden çıkarılır.

Golden record'un yalnızca bugünkü hâli saklanır; geçmiş ``audit.ci_change`` içindedir (öznitelik başına eski → yeni,
ilişki açılması/kalkması ``relation`` kaydı). Bir andaki hâl, bugünkü hâlden o andan sonraki değişiklikler yeniden
eskiye doğru geri sarılarak bulunur:

* Öznitelik: o andan sonraki *ilk* değişikliğin eski değeri; sonra hiç değişmediyse bugünkü değer.
* İlişki: bugün var olup o andan önce açılmış ilişkiler, o andan sonraki ilişki olayları geri sarılarak (sonradan
  açılan çıkar, sonradan kalkan geri gelir). Satırı değiştirmeyen olay (başka kaynak da bildiriyordu, ``effect:
  none``) atlanır.

Saf mantıktır: veritabanı ve sunum API katmanındadır.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "REASON_TR",
    "Change",
    "Moment",
    "RelationKey",
    "RelationRow",
    "attributes_at",
    "group_moments",
    "relations_at",
]

#: İlişki anahtarı: (tip, ebeveyn sys_id, çocuk sys_id)
RelationKey = tuple[str, str, str]

#: Değişim nedeni → Türkçe (audit.ci_change.reason)
REASON_TR = {
    "reconciliation": "Uzlaştırma", "manual": "Elle düzenleme", "merge": "Birleştirme", "split": "Ayırma",
    "reclassification": "Sınıf değişikliği", "staleness": "Eskime kararı", "import": "İçe aktarma",
    "schema": "Şema değişikliği", "sn_pull": "ServiceNow'dan çekme", "attestation": "Sahiplik onayı",
}


@dataclass(frozen=True)
class Change:
    """Değişim geçmişinin bir satırı. ``seq`` aynı andaki satırların sırasıdır (``audit.ci_change.id``)."""

    at: datetime
    seq: int
    attribute: str
    old: Any
    new: Any
    source: str | None = None
    actor: str = ""
    reason: str = "reconciliation"
    correlation: Mapping[str, Any] = field(default_factory=dict)

    @property
    def no_effect(self) -> bool:
        return self.correlation.get("effect") == "none"


@dataclass(frozen=True)
class RelationRow:
    """Bugün var olan bir ilişki satırı ve açıldığı an (``cmdb_rel_ci.sys_created_on``)."""

    type: str
    parent: str
    child: str
    created_on: datetime

    @property
    def key(self) -> RelationKey:
        return (self.type, self.parent, self.child)


def _after(changes: Iterable[Change], at: datetime) -> list[Change]:
    """``at``'ten sonraki değişiklikler, yeniden eskiye."""
    return sorted((c for c in changes if c.at > at), key=lambda c: (c.at, c.seq), reverse=True)


def attributes_at(current: Mapping[str, Any], changes: Iterable[Change], at: datetime) -> dict[str, Any]:
    """``at`` anındaki öznitelik değerleri. Değeri olmayan öznitelik sonuçta yer almaz."""
    values = {k: v for k, v in current.items() if v is not None}
    for c in _after(changes, at):
        if c.attribute == "relation":
            continue
        if c.old is None:
            values.pop(c.attribute, None)
        else:
            values[c.attribute] = c.old
    return values


def _relation_key(value: Any) -> RelationKey | None:
    if isinstance(value, Mapping) and all(isinstance(value.get(k), str) for k in ("type", "parent", "child")):
        return (value["type"], value["parent"], value["child"])
    return None


def relations_at(current: Iterable[RelationRow], changes: Iterable[Change], at: datetime) -> set[RelationKey]:
    """``at`` anında var olan ilişkiler (yalnızca verilen olaylarla görülebilenler: CI'ın ilişki olayları)."""
    present = {r.key for r in current if r.created_on <= at}
    for c in _after(changes, at):
        if c.attribute != "relation" or c.no_effect:
            continue
        added, removed = _relation_key(c.new), _relation_key(c.old)
        if added:
            present.discard(added)  # sonradan açıldı: o anda yoktu
        elif removed:
            present.add(removed)  # sonradan kalktı: o anda vardı
    return present


@dataclass
class Moment:
    """Zaman çizelgesinde bir an: aynı işlemde, aynı kaynak ve nedenle yapılan değişiklikler."""

    at: datetime
    source: str | None
    actor: str
    reason: str
    changes: list[Change]

    @property
    def reason_label(self) -> str:
        return REASON_TR.get(self.reason, self.reason)


def _moment_key(c: Change) -> tuple[Any, ...]:
    """Aynı ana düşen değişiklikler: aynı çalışma (``run_id``; öznitelikler kayıtla, ilişkiler çalışma sonunda ayrı
    işlemde yazılır) veya aynı elle istek; ikisi de yoksa aynı işlem anı. Kaynak, kişi ve neden de aynı olmalı."""
    corr = c.correlation or {}
    return (corr.get("run_id") or corr.get("request") or c.at, c.source, c.actor, c.reason)


def group_moments(changes: Iterable[Change]) -> list[Moment]:
    """Değişiklikleri (yeniden eskiye sıralı) anlara toplar; anın zamanı en yeni değişikliğinkidir."""
    out: list[Moment] = []
    last_key: tuple[Any, ...] | None = None
    for c in changes:
        key = _moment_key(c)
        if out and key == last_key:
            out[-1].changes.append(c)
        else:
            out.append(Moment(c.at, c.source, c.actor, c.reason, [c]))
        last_key = key
    return out
