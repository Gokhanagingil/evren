"""Veri modeli gezgini için metadata görünümü (U4.4, ADR-0019).

Tek kaynak ``metadata/*.yaml`` (+ yayınlanmış değişiklik dosyaları). Bu modül onu arayüzün okuyacağı biçime çevirir:
sınıf ağacı (derinlik öncelikli, Türkçe ada göre), her sınıfın kendi alanları, geçerli kimlik kuralı (kalıtılan da),
seçim listeleri, izinli ilişki kuralları ve ServiceNow'dan doğrulanacak öğeler. Kalıtılan alanlar ve bir sınıfa düşen
ilişki önerileri istemcide ata zincirinden hesaplanır (``RelationRules.suggestions`` ile aynı kural); dosya küçük kalır.

Aynı çıktı iki yerde kullanılır: API (``GET /api/v1/schema/model``) ve demo modunun statik dosyası
(``apps/web/content/model.json``, ``tools/build_model.py``). Çıktı deterministiktir (zaman damgası yok) ki statik dosya
CI'da ``--check`` ile denetlenebilsin.
"""
from __future__ import annotations

from typing import Any

from .identity.rules import RuleSet
from .metadata import Metadata, verification_backlog
from .normalize import ascii_fold

__all__ = ["model_view"]

_FIELD_KEYS = ("type", "max_length", "reference", "storage", "sensitivity")


def _field(f: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"name": f["name"], "label_tr": f.get("label_tr", f["name"])}
    for k in _FIELD_KEYS:
        if f.get(k) is not None:
            out[k] = f[k]
    out.setdefault("storage", "jsonb")
    out.setdefault("sensitivity", "internal")
    for flag in ("choice", "indexed", "verify", "deprecated"):
        if f.get(flag):
            out[flag] = True
    return out


def model_view(md: Metadata) -> dict[str, Any]:
    h = md.hierarchy
    cmap = md.class_map
    children: dict[str, list[str]] = {}
    for c in md.classes:
        if c.get("extends"):
            children.setdefault(c["extends"], []).append(c["name"])
    for kids in children.values():
        kids.sort(key=lambda n: (ascii_fold(cmap[n]["label_tr"]), n))

    rules = RuleSet.from_metadata(md.identification, h)
    raw_rules = {r["applies_to"]: r for r in md.identification}
    backlog = verification_backlog(md)

    def identification(name: str) -> dict[str, Any] | None:
        rule = rules.for_class(name)
        if rule is None:
            return None
        raw = raw_rules[rule.applies_to]
        context = None
        if raw.get("parent_relation"):
            pr = raw["parent_relation"]
            context = {"kind": "relation", "type": pr["type"], "parent_class": pr["parent_class"]}
        elif raw.get("parent_reference"):
            context = {"kind": "reference", "field": raw["parent_reference"]}
        return {
            "applies_to": rule.applies_to, "inherited": rule.applies_to != name, "independent": rule.independent,
            "description": rule.description, "verify": bool(raw.get("verify")), "context": context,
            "entries": [{"priority": e.priority, "attributes": list(e.attributes), "search_table": e.search_table,
                         "evren_extension": bool(next((x.get("evren_extension") for x in raw["entries"]
                                                       if int(x["priority"]) == e.priority), False))}
                        for e in rule.ordered()],
        }

    ordered: list[dict[str, Any]] = []

    def walk(name: str, depth: int) -> None:
        c = cmap[name]
        own = [_field(f) for f in c.get("fields") or []]
        ordered.append({
            "name": name, "label": c.get("label", name), "label_tr": c["label_tr"], "parent": c.get("extends"),
            "depth": depth, "family": h.family(name), "verify": bool(c.get("verify")),
            "children": children.get(name, []), "fields": own,
            "field_count": len(md.fields_of(name)),
            "identification": identification(name),
            "sn_view": f"sn.{name}",
        })
        for kid in children.get(name, []):
            walk(kid, depth + 1)

    walk("cmdb_ci", 0)

    rr = md.relationship_rules or {}
    return {
        "version": 1,
        "stats": {
            "classes": len(md.classes),
            "fields": sum(len(c.get("fields") or []) for c in md.classes),
            "core_columns": sum(1 for f in cmap["cmdb_ci"].get("fields") or [] if f.get("storage") == "column"),
            "choice_lists": len(md.choices),
            "relationship_types": len(md.relationship_types),
            "relationship_rules": len(rr.get("rules") or []),
            "identification_rules": len(md.identification),
            "reference_tables": len(md.reference_tables),
            "to_verify": sum(len(v) for v in backlog.values()),
        },
        "classes": ordered,
        "choices": [{"table": ch["table"], "element": ch["element"], "depends_on": ch.get("depends_on"),
                     "verify": bool(ch.get("verify")),
                     "label_tr": (md.fields_of(ch["table"]).get(ch["element"]) or {}).get("label_tr", ch["element"]),
                     "values": [{k: v for k, v in val.items() if k in (
                         "value", "label", "label_tr", "dependent_value", "legacy")} for val in ch["values"]]}
                    for ch in md.choices],
        "relationship_types": [{"name": t["name"], "label_tr": t.get("label_tr"), "verify": bool(t.get("verify"))}
                               for t in md.relationship_types],
        "relationship_rules": {
            "enforcement": rr.get("enforcement", "warn"),
            "rules": [{"parent": r["parent"], "type": r["type"], "child": r["child"],
                       "description": r.get("description"), "verify": bool(r.get("verify"))}
                      for r in rr.get("rules") or []],
        },
        "reference_tables": [{"name": t["name"], "label_tr": t.get("label_tr", t["name"])}
                             for t in md.reference_tables],
        "backlog": backlog,
    }
