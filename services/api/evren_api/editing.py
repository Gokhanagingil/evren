"""Elle düzenleme politikası: düzenlenebilir alanlar ve değer doğrulaması.

Kullanıcıdan gelen alan adları yalnızca metadata sözlüğünden çözülür (CLAUDE.md güvenlik kuralı). Bir alan
elle düzenlenebilir ise:

* sınıfın sözlüğünde vardır, sistem tarafından türetilmez (keşif zamanları, ``sys_*``);
* uzlaştırma kuralı varsa ``Manual`` rolü o alan için yetkilidir (yoksa değer zaten reddedilirdi);
* kişinin yazma izni alanın hassasiyetini kapsar.

``governed``: uzlaştırma kuralında Manual en öncelikli roldür (yönetişim alanları) — elle değer doğrudan geçerli olur.
Değilse elle değer yalnızca yedektir (daha öncelikli kaynak bildirdiği sürece geçmez) veya kural yoksa en güncel
gözlem kazanır. Arayüz bunu gösterir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg import sql

from evren_core.metadata import Metadata
from evren_core.reconcile import Reconciler

from .authz import Grant
from .errors import ApiError
from .present import REF_TABLES

__all__ = ["EditPolicy", "EditableField"]

SYSTEM_MANAGED = frozenset({"discovery_source", "first_discovered", "last_discovered", "duplicate_of",
                            "sys_class_name"})
SUPPORTED_TYPES = frozenset({"string", "integer", "decimal", "boolean", "reference", "glide_date", "glide_date_time"})
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,79}$")
_SYSID = re.compile(r"^[0-9a-f]{32}$")
MAX_ATTRIBUTES = 50


@dataclass(frozen=True)
class EditableField:
    name: str
    label: str
    type: str
    max_length: int
    sensitivity: str
    choices: dict[str, str] | None
    reference: str | None
    governed: bool
    authority: tuple[str, ...]  # öncelik sırasıyla yetkili roller (boş: kural yok, en güncel gözlem kazanır)

    def as_json(self) -> dict[str, Any]:
        return {"attribute": self.name, "label": self.label, "type": self.type, "max_length": self.max_length,
                "choices": [{"value": v, "label": lbl} for v, lbl in self.choices.items()] if self.choices else None,
                "reference": self.reference, "governed": self.governed, "authority": list(self.authority)}


class EditPolicy:
    def __init__(self, md: Metadata) -> None:
        self.md = md
        self.reconciler = Reconciler.from_metadata(md.hierarchy, md.reconciliation, md.sources)
        self.choices = {(c["table"], c["element"]): {str(v["value"]): v["label_tr"] for v in c["values"]}
                        for c in md.choices}
        self._cache: dict[str, dict[str, EditableField]] = {}

    def _all(self, cls: str) -> dict[str, EditableField]:
        if cls in self._cache:
            return self._cache[cls]
        out: dict[str, EditableField] = {}
        for name, f in self.md.fields_of(cls).items():
            ftype = f.get("type", "string")
            if name in SYSTEM_MANAGED or name.startswith("sys_") or ftype not in SUPPORTED_TYPES or f.get("deprecated"):
                continue
            if ftype == "reference" and f.get("reference") not in REF_TABLES:
                continue  # referans hedefi Evren'de yansıtılmıyor: doğrulanamaz, düzenlenmez
            auth = self.reconciler.authorized(cls, name)
            if auth is not None and "Manual" not in auth:
                continue
            order = tuple(sorted(auth, key=lambda r: (auth[r], r))) if auth else ()
            choices = self.choices.get((f.get("_table", "cmdb_ci"), name)) or self.choices.get(("cmdb_ci", name)) \
                if f.get("choice") else None
            out[name] = EditableField(
                name=name, label=f.get("label_tr", name), type=ftype, max_length=int(f.get("max_length") or 255),
                sensitivity=f.get("sensitivity", "internal"), choices=choices, reference=f.get("reference"),
                governed=bool(order) and order[0] == "Manual", authority=order)
        self._cache[cls] = out
        return out

    def fields(self, cls: str, grant: Grant) -> dict[str, EditableField]:
        if not grant.allowed:
            return {}
        return {n: f for n, f in self._all(cls).items() if grant.can_see(f.sensitivity)}

    def validate(self, conn: psycopg.Connection[Any], cls: str, grant: Grant,
                 attributes: dict[str, Any]) -> dict[str, Any]:
        """İstekteki değerleri doğrular ve normalleştirir. ``None`` (veya boş metin): elle değeri kaldır."""
        if not attributes:
            raise ApiError(422, "bos_istek", "En az bir alan gönderin.")
        if len(attributes) > MAX_ATTRIBUTES:
            raise ApiError(422, "cok_alan", f"Tek istekte en fazla {MAX_ATTRIBUTES} alan değiştirilebilir.")
        known = self.md.fields_of(cls)
        editable = self._all(cls)
        out: dict[str, Any] = {}
        errors: list[dict[str, str]] = []
        for name, value in attributes.items():
            shown = name if _IDENT.match(name) else "(geçersiz ad)"  # girdi yansıtılmaz, yalnızca güvenli ad
            if name not in known:
                errors.append({"alan": shown, "hata": "Sözlükte bu sınıf için tanımlı değil"})
                continue
            spec = editable.get(name)
            if spec is None:
                errors.append({"alan": shown, "hata": "Elle yazılamaz (sistem alanı veya Manual bu alana yetkili değil)"})
                continue
            if not grant.can_see(spec.sensitivity):
                raise ApiError(403, "alan_yetkisi_yok", "Bu alanı düzenleme yetkiniz yok.", [{"alan": name}])
            try:
                out[name] = self._coerce(conn, spec, value)
            except ValueError as exc:
                errors.append({"alan": name, "hata": str(exc)})
        if errors:
            raise ApiError(422, "gecersiz_alan", "Bazı alanlar doğrulanamadı.", errors)
        return out

    def _coerce(self, conn: psycopg.Connection[Any], spec: EditableField, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if spec.choices is not None:
            key = str(value)
            if key not in spec.choices:
                raise ValueError("Seçim listesinde olmayan değer")
            return int(key) if spec.type == "integer" else key
        if spec.type == "string":
            if not isinstance(value, str):
                raise ValueError("Metin bekleniyor")
            text = value.strip()
            if len(text) > spec.max_length:
                raise ValueError(f"En fazla {spec.max_length} karakter")
            if any(ord(ch) < 32 and ch not in "\t\n" for ch in text):
                raise ValueError("Denetim karakteri içeremez")
            return text
        if spec.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int | str):
                raise ValueError("Tam sayı bekleniyor")
            try:
                return int(value)
            except ValueError:
                raise ValueError("Tam sayı bekleniyor") from None
        if spec.type == "decimal":
            if isinstance(value, bool):
                raise ValueError("Sayı bekleniyor")
            try:
                return float(Decimal(str(value)))
            except InvalidOperation:
                raise ValueError("Sayı bekleniyor") from None
        if spec.type == "boolean":
            if not isinstance(value, bool):
                raise ValueError("Evet/hayır bekleniyor")
            return value
        if spec.type == "reference":
            if not isinstance(value, str) or not _SYSID.match(value):
                raise ValueError("32 haneli sys_id bekleniyor")
            row = conn.execute(sql.SQL("SELECT 1 FROM {} WHERE sys_id = %s").format(
                sql.Identifier("ref", spec.reference or "")), (value,)).fetchone()
            if row is None:
                raise ValueError("Başvurulan kayıt bulunamadı")
            return value
        if spec.type in ("glide_date", "glide_date_time"):
            if not isinstance(value, str):
                raise ValueError("Tarih bekleniyor (ISO 8601)")
            try:
                parsed = date.fromisoformat(value) if spec.type == "glide_date" else datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("Tarih bekleniyor (ISO 8601)") from None
            return parsed.isoformat()
        raise ValueError("Desteklenmeyen alan tipi")  # pragma: no cover - SUPPORTED_TYPES ile süzülür
