"""Metadata değişiklik setleri (F1-11, ADR-0015) — ServiceNow "update set"in Git ile denetlenebilir karşılığı.

Bir değişiklik seti sıralı işlemlerden oluşur. Arayüzde hazırlanır, doğrulanır ve etki analizi yapılır; hazırlayan
dışında biri onaylar (veritabanı kısıtı); yayında ``metadata/changes/`` altına konacak bir değişiklik dosyası
üretilir. ``load_metadata`` temel YAML dosyalarını yükledikten sonra bu dosyaları ad sırasıyla uygular; böylece
temel dosyaların yorumları bozulmaz, her ortam aynı sonucu üretir ve geri alma ``git revert``'tür.

İşlemler (CLAUDE.md kural 6: sınıf alanı için DDL yazılmaz; yeni alanlar JSONB'de):

* ``add_class``            yeni sınıf (``u_`` önekli; ServiceNow özel tablo kuralı)
* ``add_field``            yeni alan (``u_`` önekli, JSONB)
* ``modify_field``         etiket, hassasiyet, uzunluk (yalnız artırma)
* ``deprecate_field``      kullanımdan kaldır (alan kalır, düzenlemeden gizlenir)
* ``add_choice``           seçim listesine değer
* ``add_data_source``      aynı rolde yeni ürün — her zaman ``shadow`` başlar (ADR-0009)
* ``set_source_lifecycle`` yaşam döngüsü ve rol sırası (izinli geçişlerle)

Saf modüldür: dosya veya veritabanı erişimi yalnızca ``load_change_files``'tadır.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["OPERATIONS", "TRANSITIONS", "Change", "ChangeError", "apply_changes", "describe", "load_change_files",
           "parse_changes", "render_change_file"]

OPERATIONS = ("add_class", "add_field", "modify_field", "deprecate_field", "add_choice", "add_data_source",
              "set_source_lifecycle")
FIELD_TYPES = ("string", "integer", "decimal", "boolean", "reference", "glide_date", "glide_date_time")
SENSITIVITIES = ("public", "internal", "confidential", "restricted")
#: İzinli yaşam döngüsü geçişleri (ADR-0009). Etkin kaynak doğrudan emekli olmaz: önce devredilir.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "shadow": ("active", "retired"), "active": ("retiring",), "retiring": ("active", "retired"), "retired": ()}
LIFECYCLE_TR = {"active": "etkin", "shadow": "gölge", "retiring": "devrediliyor", "retired": "emekli"}
OP_TR = {"add_class": "Sınıf ekle", "add_field": "Alan ekle", "modify_field": "Alanı değiştir",
         "deprecate_field": "Alanı kullanımdan kaldır", "add_choice": "Seçim değeri ekle",
         "add_data_source": "Veri kaynağı ekle (gölge)", "set_source_lifecycle": "Yaşam döngüsünü değiştir"}
_CUSTOM = re.compile(r"^u_[a-z][a-z0-9_]{0,60}$")
_SOURCE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")


class ChangeError(ValueError):
    """Uygulanamayan değişiklik. ``errors``: Türkçe, kullanıcıya gösterilebilir iletiler (girdi yansıtmaz)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Change:
    op: str
    table: str
    element: str | None
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op, "table": self.table}
        if self.element is not None:
            out["element"] = self.element
        out["payload"] = dict(self.payload)
        return out


def parse_changes(items: Iterable[Mapping[str, Any]]) -> list[Change]:
    changes, errors = [], []
    for i, raw in enumerate(items, 1):
        op = raw.get("op")
        if op not in OPERATIONS:
            errors.append(f"{i}. işlem: tanımsız işlem türü")
            continue
        table = raw.get("table")
        element = raw.get("element")
        payload = raw.get("payload") or {}
        if not isinstance(table, str) or not table or (element is not None and not isinstance(element, str)) \
                or not isinstance(payload, Mapping):
            errors.append(f"{i}. işlem: tablo, öğe veya içerik biçimi geçersiz")
            continue
        changes.append(Change(op, table, element, dict(payload)))
    if not changes and not errors:
        errors.append("Değişiklik seti en az bir işlem içermeli")
    if errors:
        raise ChangeError(errors)
    return changes


def describe(c: Change) -> str:
    """İşlemin Türkçe tek cümlelik açıklaması (arayüz, değişiklik dosyası yorumu, denetim)."""
    p = c.payload
    if c.op == "add_class":
        return f"{c.table} sınıfı eklenir (üst sınıf {p.get('parent')}, etiket \"{p.get('label_tr')}\")"
    if c.op == "add_field":
        return f"{c.table}.{c.element} alanı eklenir ({p.get('type')}, \"{p.get('label_tr')}\", {p.get('sensitivity', 'internal')})"
    if c.op == "modify_field":
        parts = [f"{k} → {v}" for k, v in p.items()]
        return f"{c.table}.{c.element} alanı değişir: " + ", ".join(parts)
    if c.op == "deprecate_field":
        return f"{c.table}.{c.element} alanı kullanımdan kaldırılır (değerler kalır, düzenlenemez)"
    if c.op == "add_choice":
        return f"{c.table}.{c.element} seçim listesine \"{p.get('value')}\" ({p.get('label_tr')}) eklenir"
    if c.op == "add_data_source":
        return f"{c.element} veri kaynağı {p.get('role')} rolünde gölge modda eklenir"
    if c.op == "set_source_lifecycle":
        rank = f", rol sırası {p['role_rank']}" if p.get("role_rank") is not None else ""
        return f"{c.element} veri kaynağı {LIFECYCLE_TR.get(str(p.get('lifecycle')), p.get('lifecycle'))} olur{rank}"
    return c.op  # pragma: no cover


def _find_class(md: Any, name: str) -> dict[str, Any] | None:
    return next((c for c in md.classes if c["name"] == name), None)


def _descendants(md: Any, name: str) -> list[str]:
    return [c["name"] for c in md.classes if name in md.hierarchy.ancestors(c["name"]) and c["name"] != name]


def apply_changes(md: Any, changes: Iterable[Change]) -> Any:
    """Değişiklikleri metadata'nın kopyasına uygular ve sonucu tam doğrulamadan geçirir.

    Hata varsa ``ChangeError`` (tüm iletilerle); başarıda yeni ``Metadata`` döner. Girdi değişmez.
    """
    from .metadata import Metadata, validate  # döngüsel içe aktarmayı önler

    def rebuild(base: Any, **kw: Any) -> Any:
        return Metadata(classes=kw.get("classes", base.classes), reference_tables=base.reference_tables,
                        relationship_types=base.relationship_types, choices=kw.get("choices", base.choices),
                        data_sources=kw.get("data_sources", base.data_sources), identification=base.identification,
                        reconciliation=base.reconciliation, source_roles=base.source_roles, health=base.health,
                        relationship_rules=base.relationship_rules)

    cur = rebuild(md, classes=copy.deepcopy(md.classes), choices=copy.deepcopy(md.choices),
                  data_sources=copy.deepcopy(md.data_sources))
    errors: list[str] = []
    for i, c in enumerate(changes, 1):
        where = f"{i}. işlem ({OP_TR.get(c.op, c.op)})"
        p = dict(c.payload)
        try:
            if c.op == "add_class":
                if not _CUSTOM.match(c.table):
                    raise ChangeError([f"{where}: sınıf adı u_ ile başlamalı (küçük harf, rakam, alt çizgi)"])
                if _find_class(cur, c.table):
                    raise ChangeError([f"{where}: bu sınıf zaten var"])
                parent = p.get("parent")
                if not _find_class(cur, str(parent)):
                    raise ChangeError([f"{where}: üst sınıf tanımsız"])
                if not (isinstance(p.get("label_tr"), str) and 1 <= len(p["label_tr"].strip()) <= 80):
                    raise ChangeError([f"{where}: Türkçe etiket zorunlu (en fazla 80 karakter)"])
                cur.classes.append({"name": c.table, "label": p.get("label") or p["label_tr"].strip(),
                                    "label_tr": p["label_tr"].strip(), "extends": parent, "fields": []})
                cur = rebuild(cur)
            elif c.op == "add_field":
                cls = _find_class(cur, c.table)
                if cls is None:
                    raise ChangeError([f"{where}: sınıf tanımsız"])
                if not (c.element and _CUSTOM.match(c.element)):
                    raise ChangeError([f"{where}: alan adı u_ ile başlamalı (küçük harf, rakam, alt çizgi)"])
                if c.element in cur.fields_of(c.table) or any(
                        c.element in cur.fields_of(d, inherited=False) for d in _descendants(cur, c.table)):
                    raise ChangeError([f"{where}: bu ad sınıfta veya alt sınıflarında zaten kullanılıyor"])
                ftype = p.get("type")
                if ftype not in FIELD_TYPES:
                    raise ChangeError([f"{where}: alan tipi geçersiz"])
                label = p.get("label_tr")
                if not (isinstance(label, str) and 1 <= len(label.strip()) <= 80):
                    raise ChangeError([f"{where}: Türkçe etiket zorunlu (en fazla 80 karakter)"])
                sens = p.get("sensitivity", "internal")
                if sens not in SENSITIVITIES:
                    raise ChangeError([f"{where}: hassasiyet geçersiz"])
                field: dict[str, Any] = {"name": c.element, "type": ftype, "label_tr": label.strip(), "storage": "jsonb",
                                         "sensitivity": sens}
                if ftype == "string":
                    ml = p.get("max_length", 255)
                    if not isinstance(ml, int) or isinstance(ml, bool) or not 1 <= ml <= 4000:
                        raise ChangeError([f"{where}: uzunluk 1–4000 olmalı"])
                    field["max_length"] = ml
                    if p.get("choice"):
                        field["choice"] = True
                if ftype == "reference":
                    ref = p.get("reference")
                    if ref not in {t["name"] for t in cur.reference_tables}:
                        raise ChangeError([f"{where}: referans tablosu tanımsız"])
                    field["reference"] = ref
                cls.setdefault("fields", []).append(field)
            elif c.op in ("modify_field", "deprecate_field"):
                cls = _find_class(cur, c.table)
                field = next((f for f in (cls or {}).get("fields") or [] if f["name"] == c.element), None)
                if field is None:
                    raise ChangeError([f"{where}: alan bu sınıfta tanımlı değil (kalıtılan alan, tanımlandığı sınıfta değiştirilir)"])
                if c.op == "deprecate_field":
                    if field.get("storage", "jsonb") != "jsonb":
                        raise ChangeError([f"{where}: fiziksel kolon (çekirdek alan) kullanımdan kaldırılamaz"])
                    field["deprecated"] = True
                else:
                    unknown = set(p) - {"label_tr", "sensitivity", "max_length"}
                    if unknown or not p:
                        raise ChangeError([f"{where}: yalnızca etiket, hassasiyet ve uzunluk değiştirilebilir"])
                    if "label_tr" in p:
                        if not (isinstance(p["label_tr"], str) and 1 <= len(p["label_tr"].strip()) <= 80):
                            raise ChangeError([f"{where}: Türkçe etiket 1–80 karakter olmalı"])
                        field["label_tr"] = p["label_tr"].strip()
                    if "sensitivity" in p:
                        if p["sensitivity"] not in SENSITIVITIES:
                            raise ChangeError([f"{where}: hassasiyet geçersiz"])
                        field["sensitivity"] = p["sensitivity"]
                    if "max_length" in p:
                        ml = p["max_length"]
                        if field.get("type") != "string" or not isinstance(ml, int) or isinstance(ml, bool) \
                                or ml < int(field.get("max_length", 255)) or ml > 4000:
                            raise ChangeError([f"{where}: uzunluk yalnızca metin alanında ve yalnızca artırılabilir (en fazla 4000)"])
                        field["max_length"] = ml
            elif c.op == "add_choice":
                fdef = cur.fields_of(c.table).get(str(c.element)) if _find_class(cur, c.table) else None
                if not fdef or not fdef.get("choice"):
                    raise ChangeError([f"{where}: seçim listeli bir alan değil"])
                table = fdef["_table"]
                value, label = p.get("value"), p.get("label_tr")
                if not (isinstance(value, str | int) and str(value).strip() and isinstance(label, str) and label.strip()):
                    raise ChangeError([f"{where}: değer ve Türkçe etiket zorunlu"])
                entry = next((ch for ch in cur.choices if ch["table"] == table and ch["element"] == c.element), None)
                if entry is None:
                    entry = {"table": table, "element": c.element, "values": []}
                    cur.choices.append(entry)
                if any(str(v["value"]) == str(value) for v in entry["values"]):
                    raise ChangeError([f"{where}: bu değer listede zaten var"])
                entry["values"].append({"value": value, "label": p.get("label") or label.strip(),
                                        "label_tr": label.strip(), "sequence": len(entry["values"]) * 10 + 10})
            elif c.op == "add_data_source":
                if not (c.element and _SOURCE.match(c.element)):
                    raise ChangeError([f"{where}: veri kaynağı adı geçersiz"])
                if any(s["name"] == c.element for s in cur.data_sources):
                    raise ChangeError([f"{where}: bu veri kaynağı zaten var"])
                role = p.get("role")
                if role not in {r["name"] for r in cur.source_roles}:
                    raise ChangeError([f"{where}: kaynak rolü tanımsız"])
                label = p.get("label_tr")
                if not (isinstance(label, str) and 1 <= len(label.strip()) <= 80):
                    raise ChangeError([f"{where}: Türkçe etiket zorunlu"])
                ranks = [int(s.get("role_rank", 1)) for s in cur.data_sources if s.get("role") == role]
                cur.data_sources.append({"name": c.element, "label_tr": label.strip(), "role": role,
                                         "role_rank": max(ranks, default=0) + 1, "lifecycle": "shadow",
                                         "connector": p.get("connector") or None, "mappings": list(p.get("mappings") or [])})
            elif c.op == "set_source_lifecycle":
                src = next((s for s in cur.data_sources if s["name"] == c.element), None)
                if src is None:
                    raise ChangeError([f"{where}: veri kaynağı tanımsız"])
                current = src.get("lifecycle", "active")
                target = p.get("lifecycle")
                if target not in TRANSITIONS.get(current, ()):
                    allowed = ", ".join(LIFECYCLE_TR[t] for t in TRANSITIONS.get(current, ())) or "yok"
                    raise ChangeError([f"{where}: {LIFECYCLE_TR.get(current, current)} → {LIFECYCLE_TR.get(str(target), target)} "
                                       f"geçişine izin yok (izinli: {allowed})"])
                src["lifecycle"] = target
                if p.get("role_rank") is not None:
                    rank = p["role_rank"]
                    if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= 99:
                        raise ChangeError([f"{where}: rol sırası 1–99 olmalı"])
                    src["role_rank"] = rank
        except ChangeError as exc:
            errors.extend(exc.errors)
    if errors:
        raise ChangeError(errors)
    result = rebuild(cur)
    report = validate(result)
    if report.errors:
        raise ChangeError([f"Doğrulama: {e}" for e in report.errors])
    return result


def render_change_file(meta: Mapping[str, Any], changes: Iterable[Change]) -> str:
    """Yayınlanan setin ``metadata/changes/`` dosyası (YAML). Başlık yorumu insan için, gövde makine için."""
    items = list(changes)
    lines = [f"# Evren değişiklik seti: {meta.get('name')}",
             f"# Hazırlayan: {meta.get('prepared_by')} · Onaylayan: {meta.get('approved_by')} · Yayın: {meta.get('published_on')}",
             "# Bu dosya arayüzde onaylanan setten üretildi; elle düzenlemeyin (yeni set açın). İşlemler:"]
    lines += [f"#   {i}. {describe(c)}" for i, c in enumerate(items, 1)]
    doc = {"change_set": meta.get("id"), "name": meta.get("name"), "description": meta.get("description"),
           "prepared_by": meta.get("prepared_by"), "approved_by": meta.get("approved_by"),
           "approved_on": meta.get("approved_on"), "published_on": meta.get("published_on"),
           "changes": [c.as_dict() for c in items]}
    return "\n".join(lines) + "\n" + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)


def load_change_files(directory: Path) -> list[tuple[str, list[Change]]]:
    """``metadata/changes/*.yaml`` dosyalarını ad sırasıyla okur."""
    out = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        try:
            out.append((path.name, parse_changes(doc.get("changes") or [])))
        except ChangeError as exc:
            raise ChangeError([f"{path.name}: {e}" for e in exc.errors]) from None
    return out
