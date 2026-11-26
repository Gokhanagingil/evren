"""ServiceNow referans ve değişiklik tabloları (F0-08, ADR-0022, ADR-0025): tek liste, sütun türleri, değer denetimi.

Bağlayıcının çektiği alanlar (``sysparm_fields``), eşleme dosyasının doğrulaması ve uzlaştırıcının ``ref.*``
tablolarına yazdığı sütunlar buradan gelir. Listede olmayan sütun (``source``, ``entra_oid``…) bu yoldan yazılmaz;
veritabanı sütun yetkisi de aynısını zorlar (göç 0020, 0021).

* ``REFERENCE_TABLES``: grup, kişi, konum… Yalnızca referans kaynağı rolü (ITSM) yazar.
* ``CHANGE_TABLES``: ``change_request`` → ``ref.sn_change_request``, ``task_ci`` → ``ref.sn_task_ci``. Yalnızca
  değişiklik kaynağı rolü yazar. Durum, onay ve tür sütunlarında Evren'in değişiklik sözlüğü durur (``CHANGE_STATES``
  …); ürün kodları eşleme dosyasında çevrilir (kural 8).

Kişisel veri en azdır: ``sys_user`` yalnızca kullanıcı adı, ad, e-posta, departman ve etkinlik taşır (KVKK).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

__all__ = ["CHANGE_APPROVALS", "CHANGE_STATES", "CHANGE_TABLES", "CHANGE_TYPES", "REFERENCE_TABLES", "ROLE_TABLES",
           "ReferenceDataError", "ReferenceTable", "coerce_reference", "is_reference", "parse_sn_datetime"]

ColumnType = Literal["sysid", "text", "bool", "datetime", "choice"]
_SYSID = re.compile(r"^[0-9a-f]{32}$")
MAX_TEXT = 1000

# Evren'in değişiklik sözlüğü (ADR-0025). Ürünün kodu eşleme dosyasında buna çevrilir; tanınmayan değer ``other``.
CHANGE_STATES = ("new", "assess", "authorize", "scheduled", "implement", "review", "closed", "canceled", "other")
CHANGE_APPROVALS = ("not_requested", "requested", "approved", "rejected", "cancelled", "not_required", "skipped",
                    "other")
CHANGE_TYPES = ("standard", "normal", "emergency", "other")


class ReferenceDataError(ValueError):
    """Referans kaydı değeri tabloya uymuyor (ileti değeri içermez)."""


@dataclass(frozen=True)
class ReferenceTable:
    name: str                       # ServiceNow tablo adı (sınıf ipucu)
    columns: dict[str, ColumnType]
    required: tuple[str, ...] = ("name",)   # boş olamayan sütunlar (görünen ad, bağ uçları)
    has_source: bool = False        # ref satırının kaynağı (servicenow/entra/evren); yalnızca servicenow satırı ezilir
    has_active: bool = False        # silinen kayıt pasifleşir
    table: str = ""                 # ``ref`` şemasındaki tablo; boşsa ``name``
    writer: Literal["reference", "change"] = "reference"   # yazan rol yeteneği (reference_source / change_source)
    choices: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def fields(self) -> tuple[str, ...]:
        """ServiceNow Table API'den istenecek alanlar."""
        return ("sys_id", *self.columns, "sys_updated_on")

    @property
    def db_table(self) -> str:
        return self.table or self.name


REFERENCE_TABLES: dict[str, ReferenceTable] = {t.name: t for t in (
    ReferenceTable("sys_user", {"user_name": "text", "name": "text", "email": "text", "department": "sysid",
                                "active": "bool"}, required=("user_name",), has_source=True, has_active=True),
    ReferenceTable("sys_user_group", {"name": "text", "manager": "sysid", "email": "text", "active": "bool"},
                   has_source=True, has_active=True),
    ReferenceTable("cmn_location", {"name": "text", "full_name": "text", "parent": "sysid", "city": "text",
                                    "country": "text"}),
    ReferenceTable("core_company", {"name": "text", "manufacturer": "bool", "vendor": "bool"}),
    ReferenceTable("cmn_department", {"name": "text", "company": "sysid"}),
    ReferenceTable("cmn_cost_center", {"name": "text", "account_number": "text", "manager": "sysid",
                                       "parent": "sysid", "active": "bool"}, has_source=True, has_active=True),
    ReferenceTable("cmdb_model", {"name": "text", "display_name": "text", "manufacturer": "sysid",
                                  "model_number": "text"}),
)}

# Değişiklik kayıtları (ADR-0025): onaylı pencere ve etkilenen CI'lar. Silinen kayıt satır olarak kalır.
CHANGE_TABLES: dict[str, ReferenceTable] = {t.name: t for t in (
    ReferenceTable("change_request", {"number": "text", "short_description": "text", "type": "choice",
                                      "state": "choice", "approval": "choice", "start_date": "datetime",
                                      "end_date": "datetime", "work_start": "datetime", "work_end": "datetime",
                                      "cmdb_ci": "sysid"},
                   required=("number",), table="sn_change_request", writer="change",
                   choices={"type": CHANGE_TYPES, "state": CHANGE_STATES, "approval": CHANGE_APPROVALS}),
    ReferenceTable("task_ci", {"task": "sysid", "ci_item": "sysid"}, required=("task", "ci_item"),
                   table="sn_task_ci", writer="change"),
)}
ROLE_TABLES: dict[str, ReferenceTable] = {**REFERENCE_TABLES, **CHANGE_TABLES}


def is_reference(class_hint: str) -> bool:
    """Kayıt CI değil, ``ref.*`` tablosu satırı mı (referans veya değişiklik kaydı)?"""
    return class_hint in ROLE_TABLES


def parse_sn_datetime(value: Any) -> datetime:
    """ServiceNow iç tarih biçimi (``YYYY-MM-DD HH:MM:SS``, UTC) veya saat dilimli ISO 8601 → UTC ``datetime``."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ReferenceDataError("tarih saat dilimi içermeli")
        return value.astimezone(UTC)
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ReferenceDataError("tarih biçimi tanınmadı") from None
    if parsed.tzinfo is None:
        raise ReferenceDataError("tarih saat dilimi içermeli")
    return parsed.astimezone(UTC)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    raise ReferenceDataError("mantıksal değer bekleniyor")


def coerce_reference(table: str, sys_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
    """Kaynak kaydının özniteliklerini tablo sütunlarına çevirir. Boş referans/metin → NULL; bilinmeyen anahtar,
    bozuk sys_id, aşırı uzun metin veya eksik görünen ad hata verir (kayıt yazılmaz)."""
    spec = ROLE_TABLES.get(table)
    if spec is None:
        raise ReferenceDataError("tanımsız referans tablosu")
    if not _SYSID.match(sys_id):
        raise ReferenceDataError("sys_id 32 haneli onaltılık olmalı")
    unknown = set(attributes) - set(spec.columns)
    if unknown:
        raise ReferenceDataError(f"tabloda olmayan sütunlar: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for column, kind in spec.columns.items():
        value = attributes.get(column)
        if value is None or value == "":
            out[column] = None
            continue
        if kind == "sysid":
            if not isinstance(value, str) or not _SYSID.match(value):
                raise ReferenceDataError(f"{column}: sys_id bekleniyor")
            out[column] = value
        elif kind == "bool":
            try:
                out[column] = _bool(value)
            except ReferenceDataError:
                raise ReferenceDataError(f"{column}: mantıksal değer bekleniyor") from None
        elif kind == "datetime":
            try:
                out[column] = parse_sn_datetime(value)
            except ReferenceDataError as exc:
                raise ReferenceDataError(f"{column}: {exc}") from None
        elif kind == "choice":
            if str(value) not in spec.choices[column]:
                raise ReferenceDataError(f"{column}: sözlükte olmayan değer")
            out[column] = str(value)
        else:
            text = str(value).strip()
            if len(text) > MAX_TEXT:
                raise ReferenceDataError(f"{column}: {MAX_TEXT} karakteri aşıyor")
            out[column] = text or None
    for column in spec.required:
        if not out.get(column):
            raise ReferenceDataError(f"{column} boş olamaz")
    if spec.has_active and out.get("active") is None:
        out["active"] = True
    return out
