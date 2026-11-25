#!/usr/bin/env python3
"""ServiceNow CMDB sözlüğünü SALT OKUNUR dışa aktarır ve Evren tohumlarıyla karşılaştırır.

Amaç: "veri deseni ServiceNow ile aynı" isterini kanıtlamak. Kurum örneğindeki sys_db_object,
sys_dictionary, sys_choice ve cmdb_rel_type tablolarından cmdb_ci soyağacı okunur;
metadata/imported/ altına Evren biçiminde yazılır ve metadata/*.yaml ile fark raporu üretilir.

Kimlik bilgileri (repoya veya komut satırına YAZILMAZ):
  EVREN_SN_INSTANCE       https://<örnek>.service-now.com
  EVREN_SN_TOKEN_FILE     OAuth erişim belirtecini içeren dosya (tercih edilen)
  EVREN_SN_USER + EVREN_SN_PASSWORD_FILE   temel kimlik doğrulama (yalnızca geçici)

Kullanım:
  python tools/sn_metadata_export.py --out metadata/imported
  python tools/sn_metadata_export.py --diff-only metadata/imported   # ağ erişimi olmadan fark
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from _bootstrap import REPO_ROOT
from evren_core.metadata import load_metadata

PAGE = 1000

#: ServiceNow internal_type → Evren tipi. Listede olmayanlar 'string' olarak alınır ve raporlanır.
TYPE_MAP = {
    "string": "string", "translated_text": "string", "translated_field": "string", "email": "string", "url": "string",
    "ip_addr": "string", "ip_address": "string", "mac_address": "string", "phone_number_e164": "string",
    "sys_class_name": "string", "domain_id": "string", "GUID": "string", "char": "string", "journal": "string",
    "journal_input": "string", "html": "string", "multi_two_lines": "string", "choice": "string",
    "integer": "integer", "longint": "integer", "decimal": "decimal", "float": "decimal", "currency": "decimal",
    "price": "decimal", "percent_complete": "decimal", "boolean": "boolean",
    "glide_date_time": "glide_date_time", "due_date": "glide_date_time", "glide_date": "glide_date",
    "reference": "reference",
}
SYSTEM_FIELDS = {"sys_id", "sys_class_name", "sys_created_on", "sys_created_by", "sys_updated_on", "sys_updated_by",
                 "sys_mod_count", "sys_domain", "sys_domain_path", "sys_tags", "sys_class_path"}


# ---------------------------------------------------------------------------
# Ağ erişimi (yalnızca GET)
# ---------------------------------------------------------------------------


def _auth_header() -> str:
    token_file = os.environ.get("EVREN_SN_TOKEN_FILE")
    if token_file:
        return "Bearer " + Path(token_file).read_text(encoding="utf-8").strip()
    user, pw_file = os.environ.get("EVREN_SN_USER"), os.environ.get("EVREN_SN_PASSWORD_FILE")
    if user and pw_file:
        raw = f"{user}:{Path(pw_file).read_text(encoding='utf-8').strip()}".encode()
        return "Basic " + base64.b64encode(raw).decode()
    raise SystemExit("Kimlik bilgisi yok: EVREN_SN_TOKEN_FILE veya EVREN_SN_USER + EVREN_SN_PASSWORD_FILE")


def fetch_table(instance: str, table: str, query: str, fields: Iterable[str]) -> list[dict[str, Any]]:
    if not instance.startswith("https://"):
        raise SystemExit("EVREN_SN_INSTANCE https:// ile başlamalı")
    rows: list[dict[str, Any]] = []
    offset = 0
    header = _auth_header()
    while True:
        params = urllib.parse.urlencode({
            "sysparm_query": query, "sysparm_fields": ",".join(fields), "sysparm_limit": PAGE,
            "sysparm_offset": offset, "sysparm_exclude_reference_link": "true", "sysparm_display_value": "false",
        })
        req = urllib.request.Request(f"{instance}/api/now/table/{table}?{params}",  # noqa: S310 - https zorunlu
                                     headers={"Accept": "application/json", "Authorization": header})
        with urllib.request.urlopen(req, timeout=60) as res:  # noqa: S310 - https zorunlu, yukarıda doğrulandı
            batch = json.load(res).get("result", [])
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def export(instance: str) -> dict[str, list[dict[str, Any]]]:
    objects = fetch_table(instance, "sys_db_object", "", ["sys_id", "name", "label", "super_class.name"])
    hierarchy = {o["name"]: o.get("super_class.name") or None for o in objects}
    cmdb = sorted(n for n in hierarchy if "cmdb_ci" in _ancestors(n, hierarchy))
    dictionary: list[dict[str, Any]] = []
    for i in range(0, len(cmdb), 100):
        chunk = cmdb[i:i + 100]
        dictionary += fetch_table(instance, "sys_dictionary", f"nameIN{','.join(chunk)}^elementISNOTEMPTY^active=true",
                                  ["name", "element", "column_label", "internal_type", "max_length", "reference",
                                   "mandatory", "choice"])
    choices = fetch_table(instance, "sys_choice", "name=cmdb_ci^inactive=false^language=en",
                          ["name", "element", "value", "label", "sequence"])
    rel_types = fetch_table(instance, "cmdb_rel_type", "", ["name", "parent_descriptor", "child_descriptor"])
    return {"objects": [o for o in objects if o["name"] in cmdb], "dictionary": dictionary,
            "choices": choices, "rel_types": rel_types}


# ---------------------------------------------------------------------------
# Dönüştürme ve fark (saf fonksiyonlar — testlenir)
# ---------------------------------------------------------------------------


def _ancestors(name: str, hierarchy: Mapping[str, str | None]) -> list[str]:
    chain, seen, cur = [], set(), name
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = hierarchy.get(cur)
    return chain


def to_evren(raw: Mapping[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    fields_by_table: dict[str, list[dict[str, Any]]] = {}
    for d in raw["dictionary"]:
        if d["element"] in SYSTEM_FIELDS:
            continue
        sn_type = d.get("internal_type") or "string"
        ev_type = TYPE_MAP.get(sn_type)
        if ev_type is None:
            ev_type = "string"
            notes.append(f"{d['name']}.{d['element']}: ServiceNow tipi '{sn_type}' string olarak alındı")
        f: dict[str, Any] = {"name": d["element"], "type": ev_type, "label": d.get("column_label") or d["element"]}
        if ev_type == "reference":
            f["reference"] = d.get("reference") or "cmdb_ci"
        if str(d.get("max_length") or "").isdigit() and ev_type == "string":
            f["max_length"] = int(d["max_length"])
        if str(d.get("choice") or "0") not in ("0", "", "false"):
            f["choice"] = True
        fields_by_table.setdefault(d["name"], []).append(f)
    classes = []
    for o in sorted(raw["objects"], key=lambda o: o["name"]):
        classes.append({"name": o["name"], "label": o.get("label") or o["name"], "extends": o.get("super_class.name") or None,
                        "fields": sorted(fields_by_table.get(o["name"], []), key=lambda f: f["name"])})
    rels = [{"name": r["name"]} for r in sorted(raw.get("rel_types", []), key=lambda r: r["name"])]
    return {"version": 1, "origin": "servicenow", "classes": classes, "relationship_types": rels}, notes


def diff(seed_classes: list[dict[str, Any]], seed_rel_types: list[dict[str, Any]], imported: Mapping[str, Any]) -> list[str]:
    """Evren tohumu ↔ ServiceNow örneği farkları (Türkçe rapor satırları)."""
    report: list[str] = []
    sn = {c["name"]: c for c in imported["classes"]}
    for c in seed_classes:
        name = c["name"]
        if name not in sn:
            report.append(f"EKSİK SINIF: {name} ServiceNow örneğinde yok")
            continue
        if (c.get("extends") or None) != (sn[name].get("extends") or None):
            report.append(f"ÜST SINIF FARKI: {name}: Evren={c.get('extends')} ServiceNow={sn[name].get('extends')}")
        sn_fields = {f["name"]: f for f in sn[name]["fields"]}
        for f in c.get("fields") or []:
            if f["name"] not in sn_fields:
                report.append(f"EKSİK ALAN: {name}.{f['name']} bu sınıfta tanımlı değil (başka sınıfta olabilir)")
            elif sn_fields[f["name"]]["type"] != f["type"]:
                report.append(f"TİP FARKI: {name}.{f['name']}: Evren={f['type']} ServiceNow={sn_fields[f['name']]['type']}")
    sn_rels = {r["name"] for r in imported.get("relationship_types", [])}
    if sn_rels:
        for r in seed_rel_types:
            if r["name"] not in sn_rels:
                report.append(f"EKSİK İLİŞKİ TİPİ: {r['name']} ServiceNow örneğinde yok")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(REPO_ROOT / "metadata" / "imported"))
    parser.add_argument("--diff-only", metavar="DIR", help="Daha önce dışa aktarılmış klasörle yalnızca fark üret")
    args = parser.parse_args(argv)

    out = Path(args.diff_only or args.out)
    if not args.diff_only:
        instance = os.environ.get("EVREN_SN_INSTANCE", "")
        raw = export(instance)
        out.mkdir(parents=True, exist_ok=True)
        (out / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        converted, notes = to_evren(raw)
        (out / "classes.yaml").write_text(yaml.safe_dump(converted, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (out / "notes.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    imported = yaml.safe_load((out / "classes.yaml").read_text(encoding="utf-8"))
    md = load_metadata(REPO_ROOT / "metadata")
    report = diff(md.classes, md.relationship_types, imported)
    (out / "diff.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"{len(imported['classes'])} ServiceNow sınıfı okundu; {len(report)} fark: {out / 'diff.txt'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
