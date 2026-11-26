"""Kaynak rolleri ve veri kaynağı kaydı — ürün bağımsızlığı (ADR-0009).

Uzlaştırma kuralları ürün adını değil **kaynak rolünü** bilir (``Discovery``, ``Virtualization``…).
Bir veri kaynağı (ör. ``BMC`` = BMC Helix Discovery kurulumu) bir rolü doldurur. Keşif ürünü
değiştiğinde kurallar değişmez; yeni ürün aynı rolde yeni bir veri kaynağı olarak eklenir ve
yaşam döngüsüyle (``shadow`` → ``active``; eskisi ``retiring`` → ``retired``) devreye alınır.

Yaşam döngüsü anlamları
-----------------------
=========  ============  ==========  =================  =========================================
Durum      Öznitelik     CI açar     Eskimede sayılır   Kullanım
=========  ============  ==========  =================  =========================================
active     evet          evet        evet               Normal çalışma
shadow     hayır         hayır       hayır              Yeni ürünün gölge karşılaştırması
retiring   evet          hayır       evet               Ürün değişiminde eski kaynak, devir süreci
retired    hayır         hayır       hayır              Emekli; kayıtları ve bağları geçmiş olarak kalır
=========  ============  ==========  =================  =========================================

Gölge kaynak kimlik çözümlemesinden geçer (kapsam ölçülür) ve değerleri ``AttributeDecision.shadow``
içinde karşılaştırma için raporlanır; golden record'a hiçbir etkisi yoktur.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "LIFECYCLES",
    "ROLE_KINDS",
    "DataSource",
    "SourceRegistry",
    "SourceRegistryError",
    "SourceRole",
]

ROLE_KINDS = frozenset({"discovery", "monitoring", "security", "itsm", "manual", "platform"})
LIFECYCLES = ("active", "shadow", "retiring", "retired")
LIFECYCLE_TR = {"active": "Etkin", "shadow": "Gölge", "retiring": "Devrediliyor", "retired": "Emekli"}
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")


class SourceRegistryError(ValueError):
    """Kaynak kaydı tutarsız veya bilinmeyen bir kaynak istendi."""


@dataclass(frozen=True)
class SourceRole:
    name: str
    label_tr: str
    kind: str
    description_tr: str = ""
    ci_attribute_source: bool = True
    reference_source: bool = False  # ServiceNow referans tablolarını (grup, kişi, konum…) yazar (ADR-0022)
    posture_source: bool = False    # kısıtlı güvenlik duruşunu (cmdb.ci_security_posture) yazar (ADR-0023)
    change_source: bool = False     # değişiklik kayıtlarını (ref.sn_change_request, ref.sn_task_ci) yazar (ADR-0025)


@dataclass(frozen=True)
class DataSource:
    name: str
    label_tr: str
    role: str
    role_rank: int = 1
    lifecycle: str = "active"
    connector: str | None = None
    product: Mapping[str, str] = field(default_factory=dict)
    mappings: tuple[str, ...] = ()
    schedule: str | None = None
    authoritative_for_tr: str = ""
    open_question: str | None = None
    # Kaynak anahtarı ServiceNow sys_id'sidir (ADR-0004): bağı olmayan kayıt kimlik çözümlemesine girmez; sync.sn_ci_map'te
    # eşlemesi varsa Evren CI'ına bağlanır, yoksa CI aynı sys_id ile açılır (ilk yükleme) veya o sys_id'li CI'a bağlanır.
    sys_id_key: bool = False


class SourceRegistry:
    """Rol ↔ veri kaynağı çözümlemesi ve yaşam döngüsü kararları."""

    def __init__(self, roles: Iterable[SourceRole], sources: Iterable[DataSource]):
        self.roles: dict[str, SourceRole] = {}
        for role in roles:
            if role.name in self.roles:
                raise SourceRegistryError(f"Tekrarlanan kaynak rolü: {role.name}")
            self.roles[role.name] = role
        self.sources: dict[str, DataSource] = {}
        for src in sources:
            if src.name in self.sources:
                raise SourceRegistryError(f"Tekrarlanan veri kaynağı: {src.name}")
            if src.role not in self.roles:
                raise SourceRegistryError(f"{src.name}: tanımsız kaynak rolü {src.role}")
            if src.lifecycle not in LIFECYCLES:
                raise SourceRegistryError(f"{src.name}: geçersiz yaşam döngüsü {src.lifecycle}")
            self.sources[src.name] = src

    # ------------------------------------------------------------------ yükleme
    @classmethod
    def from_metadata(cls, document: Mapping[str, Any]) -> SourceRegistry:
        roles = [
            SourceRole(
                name=r["name"], label_tr=r["label_tr"], kind=r["kind"],
                description_tr=r.get("description_tr", ""),
                ci_attribute_source=bool(r.get("ci_attribute_source", True)),
                reference_source=bool(r.get("reference_source", False)),
                posture_source=bool(r.get("posture_source", False)),
                change_source=bool(r.get("change_source", False)),
            )
            for r in document.get("source_roles", [])
        ]
        sources = [
            DataSource(
                name=s["name"], label_tr=s["label_tr"], role=s["role"],
                role_rank=int(s.get("role_rank", 1)), lifecycle=s.get("lifecycle", "active"),
                connector=s.get("connector"), product=dict(s.get("product") or {}),
                mappings=tuple(s.get("mappings") or ()), schedule=s.get("schedule"),
                authoritative_for_tr=s.get("authoritative_for_tr", ""), open_question=s.get("open_question"),
                sys_id_key=bool(s.get("sys_id_key", False)),
            )
            for s in document.get("data_sources", [])
        ]
        return cls(roles, sources)

    # ------------------------------------------------------------------ sorgular
    def source(self, name: str) -> DataSource:
        try:
            return self.sources[name]
        except KeyError:
            raise SourceRegistryError(f"Tanımsız veri kaynağı: {name}") from None

    def role_of(self, name: str) -> str:
        return self.source(name).role

    def rank_of(self, name: str) -> int:
        return self.source(name).role_rank

    def writes_attributes(self, name: str) -> bool:
        """Kaynak golden record özniteliği yazabilir mi (rol izin veriyor ve kaynak etkin/devrediliyor)."""
        src = self.source(name)
        return self.roles[src.role].ci_attribute_source and src.lifecycle in ("active", "retiring")

    def writes_references(self, name: str) -> bool:
        """Kaynak ``ref.*`` referans tablolarını yazabilir mi (rolü referans kaynağı, kaynak etkin/devrediliyor)."""
        src = self.source(name)
        return self.roles[src.role].reference_source and src.lifecycle in ("active", "retiring")

    def writes_changes(self, name: str) -> bool:
        """Kaynak değişiklik kayıtlarını yazabilir mi (rolü değişiklik kaynağı, kaynak etkin/devrediliyor)."""
        src = self.source(name)
        return self.roles[src.role].change_source and src.lifecycle in ("active", "retiring")

    def writes_role_table(self, name: str, writer: str) -> bool:
        """``ref.*`` tablosunu yazan rol yeteneği: ``reference`` (ITSM referansları) veya ``change`` (ADR-0025)."""
        return self.writes_changes(name) if writer == "change" else self.writes_references(name)

    def writes_posture(self, name: str) -> bool:
        """Kaynak kısıtlı güvenlik duruşunu yazabilir mi (rolü duruş kaynağı, kaynak etkin/devrediliyor)."""
        src = self.source(name)
        return self.roles[src.role].posture_source and src.lifecycle in ("active", "retiring")

    def is_shadow(self, name: str) -> bool:
        return self.source(name).lifecycle == "shadow"

    def may_create_ci(self, name: str) -> bool:
        """Eşleşmeyen kaynak kaydı yeni CI açabilir mi? Gölge ve devredilen kaynak açamaz."""
        return self.writes_attributes(name) and self.source(name).lifecycle == "active"

    def counts_for_staleness(self, name: str) -> bool:
        """Eskime kararında "bu kaynak CI'ı görüyor mu" sorusuna katılır mı?"""
        return self.writes_attributes(name)

    def sources_in_role(self, role: str, *, include_inactive: bool = False) -> list[DataSource]:
        items = [s for s in self.sources.values() if s.role == role
                 and (include_inactive or s.lifecycle in ("active", "retiring", "shadow"))]
        return sorted(items, key=lambda s: (LIFECYCLES.index(s.lifecycle), s.role_rank, s.name))

    def validate(self) -> list[str]:
        """Ürün değişimini güvenli kılan tutarlılık kuralları (metadata doğrulayıcısı da çağırır)."""
        errors: list[str] = []
        for role in self.roles.values():
            if not _NAME.match(role.name):
                errors.append(f"Geçersiz kaynak rolü adı: {role.name}")
            if role.kind not in ROLE_KINDS:
                errors.append(f"{role.name}: geçersiz kind {role.kind}")
        for src in self.sources.values():
            if not _NAME.match(src.name):
                errors.append(f"Geçersiz veri kaynağı adı: {src.name}")
            if src.role_rank < 1:
                errors.append(f"{src.name}: role_rank 1 veya büyük olmalı")
            if src.lifecycle != "retired" and src.connector is None and self.roles[src.role].kind not in ("manual", "platform"):
                errors.append(f"{src.name}: bağlayıcı (connector) tanımsız")
        writers: dict[tuple[str, int], str] = {}
        for src in self.sources.values():
            if src.lifecycle not in ("active", "retiring"):
                continue
            key = (src.role, src.role_rank)
            if key in writers:
                errors.append(f"{src.role} rolünde {writers[key]} ve {src.name} aynı role_rank ({src.role_rank}) "
                              "ile yazıyor; sıra belirsiz")
            writers[key] = src.name
        return errors
