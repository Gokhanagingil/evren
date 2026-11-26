"""CMDB sınıf hiyerarşisi (ServiceNow ``sys_db_object.super_class`` eşdeğeri).

Eşleştirme ve uzlaştırma, sınıfın atalarına göre kural seçer. Bu yüzden
hiyerarşi tek bir yerden yüklenir: ``metadata/classes.yaml``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

__all__ = ["DEFAULT_FAMILIES", "ClassHierarchy"]

#: Eşleştirmenin yapıldığı aile kökleri. Bir kayıt yalnızca kendi ailesindeki
#: CI'larla karşılaştırılır (ör. VM örneği ile misafir sunucu ayrı CI'lardır ve
#: birbirleriyle birleştirilmez; ``Instantiates::Instantiated by`` ilişkisiyle bağlanır).
DEFAULT_FAMILIES: tuple[str, ...] = (
    "cmdb_ci_hardware",
    "cmdb_ci_vm_object",
    "cmdb_ci_appl",
    "cmdb_ci_ip_address",
    "cmdb_ci_network_adapter",
    "cmdb_ci_service",
    "cmdb_ci_business_app",
)


@dataclass
class ClassHierarchy:
    parents: dict[str, str | None]
    families: tuple[str, ...] = field(default=DEFAULT_FAMILIES)

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str | None]], families: tuple[str, ...] = DEFAULT_FAMILIES) -> ClassHierarchy:
        return cls(dict(pairs), families)

    @classmethod
    def from_metadata(cls, classes: Iterable[Mapping[str, object]]) -> ClassHierarchy:
        return cls({str(c["name"]): (str(c["extends"]) if c.get("extends") else None) for c in classes})

    def __contains__(self, name: object) -> bool:
        return name in self.parents

    def ancestors(self, name: str, *, include_self: bool = True) -> list[str]:
        """En özelden en genele: ``cmdb_ci_linux_server`` → ``[..., cmdb_ci]``."""
        chain: list[str] = []
        current: str | None = name if include_self else self.parents.get(name)
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise ValueError(f"Sınıf hiyerarşisinde döngü: {current}")
            seen.add(current)
            chain.append(current)
            current = self.parents.get(current)
        return chain

    def is_a(self, name: str, ancestor: str) -> bool:
        return ancestor in self.ancestors(name)

    def descendants(self, name: str) -> set[str]:
        return {c for c in self.parents if name in self.ancestors(c)}

    def family(self, name: str) -> str | None:
        """Kimlik ailesi: tanımlı aile köklerinden biri; yoksa cmdb_ci'ın doğrudan alt dalı.

        Böylece aileler listesine eklenmemiş bir dalın (ör. ``cmdb_ci_kubernetes_component``,
        ``cmdb_ci_ip_network``) CI'ları ortak bir "ailesiz" havuzda birbirinin adayı olmaz.
        """
        chain = self.ancestors(name)
        for fam in self.families:
            if fam in chain:
                return fam
        return chain[-2] if len(chain) >= 2 else None

    def compatible(self, a: str, b: str) -> bool:
        """Biri diğerinin atasıysa uyumludur (``cmdb_ci_server`` ↔ ``cmdb_ci_linux_server``).

        Kardeş sınıflar (``cmdb_ci_win_server`` ↔ ``cmdb_ci_linux_server``) uyumsuzdur.
        """
        return self.is_a(a, b) or self.is_a(b, a)

    def more_specific(self, a: str, b: str) -> str:
        if self.is_a(a, b):
            return a
        if self.is_a(b, a):
            return b
        raise ValueError(f"Sınıflar uyumsuz: {a} / {b}")
