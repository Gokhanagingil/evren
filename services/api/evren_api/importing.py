"""CSV ile toplu CI içe aktarma (U6.3, ADR-0026 §6): ayrıştırma, başlık eşleme, satır doğrulama. Hiçbir şey yazmaz.

* Ayraç başlık satırından bulunur (virgül, noktalı virgül, sekme); UTF-8, BOM'lu olabilir; en çok 5000 satır.
* Başlık alan adı (``serial_number``) veya sözlükteki Türkçe etiket (``Seri numarası``) olabilir; ``sys_class_name``
  sütunu varsa satırın sınıfıdır, yoksa seçilen sınıf. Tanınmayan sütun yok sayılır ve önizlemede gösterilir.
* Hücreler elle düzenlemeyle aynı politikayla doğrulanır (``EditPolicy``). Evet/hayır, seçim listesinin Türkçe etiketi
  ve referansın adı (destek grubu "Linux Sistem Ekibi") değerine çevrilir; ad birden çok kayda uyarsa satır hatalıdır.
* Formül enjeksiyonu: ``=``, ``+``, ``@`` ile veya sekme/satır başıyla başlayan hücre reddedilir (değer sonradan bir
  elektronik tabloya aktarılırsa çalıştırılmasın).
"""
from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

from evren_core.metadata import Metadata

from .authz import Grant
from .editing import EditPolicy
from .errors import ApiError
from .present import REF_TABLES

__all__ = ["MAX_CHARS", "MAX_ROWS", "Column", "ImportPreview", "Row", "parse_import"]

MAX_ROWS = 5000
MAX_CHARS = 2_000_000
FORMULA_START = ("=", "+", "@", "\t", "\r")
TRUE = {"true", "1", "evet", "e", "yes", "y", "doğru"}
FALSE = {"false", "0", "hayır", "h", "no", "n", "yanlış"}
DELIMITERS = {",": "virgül", ";": "noktalı virgül", "\t": "sekme"}
_SYSID = re.compile(r"^[0-9a-f]{32}$")


def _fold(text: str) -> str:
    """Türkçe büyük/küçük harf duyarsız karşılaştırma (İ → i, I → ı)."""
    return text.replace("İ", "i").replace("I", "ı").casefold().strip()


@dataclass
class Column:
    index: int
    header: str
    field: str | None
    label: str
    status: str  # ok | class | unknown | duplicate

    def as_json(self) -> dict[str, Any]:
        messages = {"ok": "", "class": "Satırın sınıfı", "unknown": "Tanınmadı; yok sayılır",
                    "duplicate": "Aynı alana ikinci sütun; yok sayılır"}
        return {"index": self.index, "header": self.header, "field": self.field, "label": self.label,
                "status": self.status, "message": messages[self.status]}


@dataclass
class Row:
    row_no: int
    cls: str
    name: str
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {"row_no": self.row_no, "class": self.cls, "name": self.name, "valid": not self.errors,
                "errors": self.errors, "attributes": sorted(self.values)}


@dataclass
class ImportPreview:
    delimiter: str
    columns: list[Column]
    rows: list[Row]

    @property
    def valid(self) -> list[Row]:
        return [r for r in self.rows if not r.errors]

    def as_json(self, *, limit: int = 500) -> dict[str, Any]:
        invalid = [r for r in self.rows if r.errors]
        shown = (invalid + self.valid)[:limit]
        return {"delimiter": DELIMITERS[self.delimiter], "columns": [c.as_json() for c in self.columns],
                "total": len(self.rows), "valid_count": len(self.valid), "invalid_count": len(invalid),
                "rows": [r.as_json() for r in sorted(shown, key=lambda r: r.row_no)]}


def _delimiter(first_line: str) -> str:
    counts = {d: first_line.count(d) for d in DELIMITERS}
    best = max(counts, key=lambda d: (counts[d], d == ","))
    return best if counts[best] else ","


def _map_columns(md: Metadata, headers: list[str], default_cls: str) -> list[Column]:
    all_fields = {f["name"] for c in md.classes for f in c.get("fields") or []}
    by_label: dict[str, str] = {}
    for name, f in md.fields_of(default_cls).items():
        by_label.setdefault(_fold(str(f.get("label_tr", ""))), name)
    for c in md.classes:
        for f in c.get("fields") or []:
            by_label.setdefault(_fold(str(f.get("label_tr", ""))), f["name"])
    labels = {n: str(f.get("label_tr", n)) for n, f in md.fields_of(default_cls).items()}
    cols: list[Column] = []
    used: set[str] = set()
    for i, raw in enumerate(headers):
        header = raw.strip()
        key = header.lower()
        if key in ("sys_class_name", "sınıf", "sinif"):
            status, name = ("class" if "sys_class_name" not in used else "duplicate"), "sys_class_name"
        else:
            name = key if key in all_fields else by_label.get(_fold(header))
            status = "unknown" if name is None else ("duplicate" if name in used else "ok")
        if name and status in ("ok", "class"):
            used.add(name)
        cols.append(Column(i, header[:120], name if status in ("ok", "class") else None,
                           labels.get(name or "", "Sınıf" if name == "sys_class_name" else (name or "")), status))
    return cols


def _resolve_ref(conn: psycopg.Connection[Any], table: str, value: str) -> str:
    if _SYSID.match(value):
        return value
    if table not in REF_TABLES:
        raise ValueError("Başvuru sys_id olmalı")
    extra = sql.SQL(" OR lower(user_name) = lower(%s)") if table == "sys_user" else sql.SQL("")
    params = (value, value) if table == "sys_user" else (value,)
    rows = conn.execute(sql.SQL("SELECT sys_id FROM {} WHERE lower(name) = lower(%s){} LIMIT 2").format(
        sql.Identifier("ref", table), extra), params).fetchall()
    if not rows:
        raise ValueError(f"'{value[:60]}' bulunamadı")
    if len(rows) > 1:
        raise ValueError(f"'{value[:60]}' birden çok kayda uyuyor; sys_id yazın")
    return str(rows[0][0])


def _pre_coerce(conn: psycopg.Connection[Any], policy: EditPolicy, cls: str, name: str, value: str) -> Any:
    """CSV metnini EditPolicy'nin beklediği türe çevirir (evet/hayır, seçim etiketi, referans adı)."""
    spec = policy._all(cls).get(name)
    if spec is None:
        return value  # EditPolicy anlamlı hata verir
    if spec.choices is not None:
        if value in spec.choices:
            return value
        for key, label in spec.choices.items():
            if _fold(label) == _fold(value):
                return key
        return value
    if spec.type == "boolean":
        folded = _fold(value)
        if folded in TRUE:
            return True
        if folded in FALSE:
            return False
        raise ValueError("Evet/hayır bekleniyor")
    if spec.type == "reference":
        return _resolve_ref(conn, spec.reference or "", value)
    return value


def parse_import(conn: psycopg.Connection[Any], md: Metadata, policy: EditPolicy, text: str, default_cls: str,
                 grant_for: Callable[[str], Grant]) -> ImportPreview:
    """CSV'yi ayrıştırır ve her satırı doğrular. Biçim hatası (boş dosya, çok satır) ``ApiError``; satır hatası satırda."""
    if len(text) > MAX_CHARS:
        raise ApiError(413, "dosya_cok_buyuk", f"Dosya en çok {MAX_CHARS // 1_000_000} MB olabilir.")
    text = text.lstrip("﻿")
    if not text.strip():
        raise ApiError(422, "bos_dosya", "Dosya boş.")
    delimiter = _delimiter(text.splitlines()[0])
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
    except csv.Error:
        raise ApiError(422, "csv_bozuk", "CSV ayrıştırılamadı (tırnak işaretlerini denetleyin).") from None
    header, body = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    if len(body) > MAX_ROWS:
        raise ApiError(422, "cok_satir", f"Tek dosyada en çok {MAX_ROWS} satır içe aktarılabilir.")
    if not body:
        raise ApiError(422, "satir_yok", "Başlık dışında satır yok.")
    columns = _map_columns(md, header, default_cls)
    if not any(c.field == "name" for c in columns):
        raise ApiError(422, "ad_sutunu_yok", "Ad sütunu (name veya 'Ad') zorunludur.")
    cls_col = next((c for c in columns if c.status == "class"), None)
    out: list[Row] = []
    grants: dict[str, Grant] = {}
    for n, cells in enumerate(body, start=1):
        cls = (cells[cls_col.index].strip() if cls_col and cls_col.index < len(cells) else "") or default_cls
        row = Row(n, cls[:80], "")
        if cls not in md.hierarchy or cls == "cmdb_ci":
            row.errors.append("Sınıf tanımsız veya kök sınıf")
            out.append(row)
            continue
        if cls not in grants:
            grants[cls] = grant_for(cls)
        if not grants[cls].allowed:
            row.errors.append("Bu sınıfta CI açma izniniz yok")
            out.append(row)
            continue
        raw: dict[str, Any] = {}
        for col in columns:
            if col.status != "ok" or col.index >= len(cells):
                continue
            value = cells[col.index].strip()
            if not value:
                continue
            if cells[col.index].startswith(FORMULA_START) or value.startswith(FORMULA_START):
                row.errors.append(f"{col.label or col.header}: formül olabilecek değer (=, +, @ ile başlıyor) kabul "
                                  "edilmez")
                continue
            try:
                raw[col.field] = _pre_coerce(conn, policy, cls, col.field or "", value)  # type: ignore[index]
            except ValueError as exc:
                row.errors.append(f"{col.label or col.header}: {exc}")
        row.name = str(raw.get("name") or "")[:255]
        if not row.name:
            row.errors.append("Ad boş")
        if not row.errors:
            try:
                row.values = {k: v for k, v in policy.validate(conn, cls, grants[cls], raw).items() if v is not None}
            except ApiError as exc:
                labels = {c.field: c.label or c.header for c in columns if c.field}
                row.errors += [f"{labels.get(d.get('alan'), d.get('alan'))}: {d.get('hata')}" for d in exc.details] \
                    or [exc.message]
        out.append(row)
    return ImportPreview(delimiter, columns, out)
