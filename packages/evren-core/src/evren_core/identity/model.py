"""Kimlik çözümleme veri yapıları."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

__all__ = ["KEY_TYPES", "Feature", "IdentityKeys", "Outcome", "Resolution", "SourceRecord"]

#: Aday dizininde kullanılan anahtar türleri.
KEY_TYPES = ("name", "compact", "fqdn", "serial", "uuid", "mac", "ip", "ext", "correlation", "vmobj")


@dataclass(frozen=True)
class SourceRecord:
    """Bir kaynak sistemin tek bir nesne hakkındaki normalleştirilmiş gözlemi.

    ``attributes`` ServiceNow alan adlarıyla gelir (bağlayıcının eşleme dosyası
    bunu sağlar). ``lookup`` IRE'deki *lookup* listelerinin karşılığıdır: bir
    sunucunun birden çok MAC/IP/seri numarası olabilir.
    """

    data_source: str
    source_key: str
    class_hint: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    lookup: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    external_ids: Mapping[str, str] = field(default_factory=dict)
    observed_at: datetime | None = None


@dataclass(frozen=True)
class IdentityKeys:
    names: frozenset[str] = frozenset()
    compacts: frozenset[str] = frozenset()
    fqdns: frozenset[str] = frozenset()
    serials: frozenset[str] = frozenset()
    vmware_serials: frozenset[str] = frozenset()
    uuids: frozenset[str] = frozenset()
    macs: frozenset[str] = frozenset()
    weak_macs: frozenset[str] = frozenset()
    ips: frozenset[str] = frozenset()
    external_ids: frozenset[str] = frozenset()
    correlation_ids: frozenset[str] = frozenset()
    vm_objects: frozenset[str] = frozenset()
    os_families: frozenset[str] = frozenset()

    def merged(self, other: IdentityKeys) -> IdentityKeys:
        return replace(
            self,
            **{name: getattr(self, name) | getattr(other, name) for name in self.__dataclass_fields__},
        )

    def by_type(self) -> dict[str, frozenset[str]]:
        return {
            "name": self.names,
            "compact": self.compacts,
            "fqdn": self.fqdns,
            "serial": self.serials | self.vmware_serials,
            "uuid": self.uuids,
            "mac": self.macs,
            "ip": self.ips,
            "ext": self.external_ids,
            "correlation": self.correlation_ids,
            "vmobj": self.vm_objects,
        }

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.names, self.fqdns, self.serials, self.vmware_serials, self.uuids, self.macs,
             self.external_ids, self.correlation_ids, self.vm_objects)
        )


@dataclass(frozen=True)
class Feature:
    """Eşleştirme kararına katkı yapan tek bir kanıt (açıklanabilirlik için)."""

    code: str
    weight: float
    detail: str


class Outcome(StrEnum):
    LINKED = "linked"            # daha önce bağlanmış kaynak kaydı, kimliği tutarlı
    RULE_MATCH = "rule_match"    # tanımlama kuralı (IRE semantiği) ile tek eşleşme
    SCORE_MATCH = "score_match"  # olasılıksal skor otomatik eşiği geçti
    REVIEW = "review"            # insan onayı gerekli (eşleştirme kuyruğu)
    NEW = "new"                  # yeni CI oluşturulacak
    INSUFFICIENT = "insufficient"  # kimlik için yeterli veri yok (IRE "partial payload")


@dataclass(frozen=True)
class Resolution:
    outcome: Outcome
    ci_sys_id: str | None = None
    probability: float | None = None
    rule: str | None = None
    features: tuple[Feature, ...] = ()
    candidates: tuple[tuple[str, float], ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def summary_tr(self) -> str:
        labels = {
            Outcome.LINKED: "Önceki bağlantı korundu",
            Outcome.RULE_MATCH: "Tanımlama kuralıyla eşleşti",
            Outcome.SCORE_MATCH: "Skorla otomatik eşleşti",
            Outcome.REVIEW: "İnceleme kuyruğuna alındı",
            Outcome.NEW: "Yeni CI",
            Outcome.INSUFFICIENT: "Kimlik verisi yetersiz",
        }
        head = labels[self.outcome]
        if self.rule:
            head += f" ({self.rule})"
        if self.probability is not None:
            head += f" — olasılık %{self.probability * 100:.1f}"
        return head + ("; " + "; ".join(self.reasons) if self.reasons else "")
