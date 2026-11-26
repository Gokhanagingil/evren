"""Tanımlama kuralları — ServiceNow IRE ``cmdb_identifier`` / ``cmdb_identifier_entry`` semantiği.

* Kural bir sınıfa uygulanır ve alt sınıflara kalıtılır (en yakın ata kazanır).
* Girdiler öncelik sırasıyla (küçük sayı önce) denenir.
* Kayıtta girdinin özniteliklerinden biri yoksa girdi atlanır.
* Girdi tek CI bulursa eşleşme; birden çok CI bulursa çoklu eşleşme (inceleme);
  hiç bulamazsa sonraki girdiye geçilir.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..classes import ClassHierarchy

__all__ = ["ATTRIBUTE_KEY_TYPES", "CLASS_KEY_ATTRIBUTES", "CONTEXT_KEY_ATTRIBUTES", "IdentificationRule",
           "IdentifierEntry", "RuleSet"]

#: Sınıfa özgü, kurum genelinde tekil kimlik öznitelikleri (ADR-0019). Kayıttan ``attr:<alan>`` anahtarı
#: olarak çıkarılır, aday dizinine girer, eşleşmesi güçlü kanıt, farklı değeri çelişkidir.
CLASS_KEY_ATTRIBUTES: tuple[str, ...] = ("account_id", "cluster_id", "subnet")

#: Yalnızca üst CI bağlamında anlamlı öznitelikler (Kubernetes ad alanı, bağlama noktası, bölge…). Bağımlı
#: kurallarda kullanılır; küresel dizine girmez (``default`` ad alanı yüzlerce kümede vardır).
CONTEXT_KEY_ATTRIBUTES: tuple[str, ...] = ("namespace", "mount_point", "region", "vlan_id")

#: Kural özniteliği → aday dizini anahtar türü. ``None``: eşleştirmede değiştirici, anahtar değil.
ATTRIBUTE_KEY_TYPES: Mapping[str, str | None] = {
    "serial_number": "serial",
    "serial_number_type": None,
    "name": "name",
    "fqdn": "fqdn",
    "mac_address": "mac",
    "ip_address": "ip",
    "bios_uuid": "uuid",
    "correlation_id": "correlation",
    "vcenter_uuid": "vmobj",
    "object_id": "vmobj",
    "external_id": "ext",
    **{a: "attr:" + a for a in CLASS_KEY_ATTRIBUTES},
    **{a: "attr:" + a for a in CONTEXT_KEY_ATTRIBUTES},
}


@dataclass(frozen=True)
class IdentifierEntry:
    priority: int
    attributes: tuple[str, ...]
    search_table: str | None = None

    def key_types(self) -> tuple[str, ...]:
        types: list[str] = []
        for attr in self.attributes:
            if attr not in ATTRIBUTE_KEY_TYPES:
                raise KeyError(f"Tanımlama özniteliği desteklenmiyor: {attr}")
            kt = ATTRIBUTE_KEY_TYPES[attr]
            if kt and kt not in types:
                types.append(kt)
        return tuple(types)

    @property
    def label(self) -> str:
        table = f"{self.search_table}." if self.search_table else ""
        return f"öncelik {self.priority}: {table}{'+'.join(self.attributes)}"


@dataclass(frozen=True)
class IdentificationRule:
    applies_to: str
    entries: tuple[IdentifierEntry, ...]
    independent: bool = True
    description: str = ""

    def ordered(self) -> list[IdentifierEntry]:
        return sorted(self.entries, key=lambda e: e.priority)


class RuleSet:
    def __init__(self, rules: Iterable[IdentificationRule], hierarchy: ClassHierarchy):
        self._rules = {r.applies_to: r for r in rules}
        self.hierarchy = hierarchy

    @classmethod
    def from_metadata(cls, items: Iterable[Mapping[str, object]], hierarchy: ClassHierarchy) -> RuleSet:
        rules = []
        for item in items:
            entries = tuple(
                IdentifierEntry(int(e["priority"]), tuple(e["attributes"]), e.get("search_table"))  # type: ignore[index,arg-type]
                for e in item["entries"]  # type: ignore[union-attr]
            )
            rules.append(IdentificationRule(str(item["applies_to"]), entries, bool(item.get("independent", True)),
                                            str(item.get("description", ""))))
        return cls(rules, hierarchy)

    def for_class(self, class_name: str) -> IdentificationRule | None:
        for ancestor in self.hierarchy.ancestors(class_name):
            rule = self._rules.get(ancestor)
            if rule is not None:
                return rule
        return None
