"""ServiceNow referans tabloları (F0-08, ADR-0022): tek liste, sütun türleri, değer denetimi.

Bağlayıcının çektiği alanlar (``sysparm_fields``), eşleme dosyasının doğrulaması ve uzlaştırıcının ``ref.*``
tablolarına yazdığı sütunlar buradan gelir. Listede olmayan sütun (``source``, ``entra_oid``…) bu yoldan yazılmaz;
veritabanı sütun yetkisi de aynısını zorlar (göç 0020).

Kişisel veri en azdır: ``sys_user`` yalnızca kullanıcı adı, ad, e-posta, departman ve etkinlik taşır (KVKK).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["REFERENCE_TABLES", "ReferenceDataError", "ReferenceTable", "coerce_reference", "is_reference"]

ColumnType = Literal["sysid", "text", "bool"]
_SYSID = re.compile(r"^[0-9a-f]{32}$")
MAX_TEXT = 1000


class ReferenceDataError(ValueError):
    """Referans kaydı değeri tabloya uymuyor (ileti değeri içermez)."""


@dataclass(frozen=True)
class ReferenceTable:
    name: str
    columns: dict[str, ColumnType]
    required: str = "name"          # boş olamayan görünen ad sütunu
    has_source: bool = False        # ref satırının kaynağı (servicenow/entra/evren); yalnızca servicenow satırı ezilir
    has_active: bool = False        # silinen kayıt pasifleşir

    @property
    def fields(self) -> tuple[str, ...]:
        """ServiceNow Table API'den istenecek alanlar."""
        return ("sys_id", *self.columns, "sys_updated_on")


REFERENCE_TABLES: dict[str, ReferenceTable] = {t.name: t for t in (
    ReferenceTable("sys_user", {"user_name": "text", "name": "text", "email": "text", "department": "sysid",
                                "active": "bool"}, required="user_name", has_source=True, has_active=True),
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


def is_reference(class_hint: str) -> bool:
    return class_hint in REFERENCE_TABLES


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
    spec = REFERENCE_TABLES.get(table)
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
        else:
            text = str(value).strip()
            if len(text) > MAX_TEXT:
                raise ReferenceDataError(f"{column}: {MAX_TEXT} karakteri aşıyor")
            out[column] = text or None
    if not out.get(spec.required):
        raise ReferenceDataError(f"{spec.required} boş olamaz")
    if spec.has_active and out.get("active") is None:
        out["active"] = True
    return out
