#!/usr/bin/env python3
"""Ürün değişimi tatbikatı (F1-13, ADR-0009): keşif ürünü BMC → "Keşif-B" (sentetik), kurallar değişmeden.

Sentetik demo envanteri (tools/build_demo.py) çekirdeğin GERÇEK hattından geçirilir; ardından aynı rolde
(Discovery) ikinci bir ürün kendi eşleme dosyasıyla (tools/drill/kesif_b_host.mapping.yaml) devreye alınır:

  0. Başlangıç       BMC etkin
  1. Gölge mod       Keşif-B gölge: kimliği çözülür, değer yazmaz, CI açmaz → golden record DEĞİŞMEMELİ;
                     uyum yüzdesi ve kapsam farkı ölçülür
  2. Etkin + devir   Keşif-B etkin (rol sırası 1), BMC devrediliyor (2): değerler Keşif-B'ye geçer; gölgedeyken
                     eşleşmeyen kayıtlar artık CI açar; BMC yeni CI açamaz. Emeklilik öncesi kapsam riski listelenir
  3. Emeklilik       BMC emekli: değer yazmaz, eskime kararına katılmaz

Her aşamada uzlaştırma ve tanımlama kurallarının SHA-256 özeti aynı kalmalıdır (kural dosyası değişmez).
Çıktı: apps/web/content/drill.json (Veri Kaynakları sayfası). Deterministiktir; CI ``--check`` ile denetler.

Kullanım: python tools/build_drill.py [--check]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import timedelta
from typing import Any

import yaml

from _bootstrap import REPO_ROOT
from build_demo import NOW, OS, Host, Pipeline, feed, fmt
from evren_core.connectors import FieldMapping
from evren_core.identity import Outcome, extract_keys
from evren_core.identity.adapters import source_record_from_ingest
from evren_core.reconcile import Reconciler, SourceValues
from evren_core.sources import SourceRegistry

OUT = REPO_ROOT / "apps" / "web" / "content" / "drill.json"
MAPPING = REPO_ROOT / "tools" / "drill" / "kesif_b_host.mapping.yaml"
RULE_FILES = ("metadata/reconciliation_rules.yaml", "metadata/identification_rules.yaml")
OLD, NEW = "BMC", "KesifB"
ROLE = "Discovery"
DISCOVERY_ROLES = ("Discovery", "Virtualization", "Network")
#: Keşif-B'nin işletim sistemi adlandırması BMC'den farklıdır (ürünler arası gerçekçi fark).
KB_OS = {"linux": "RHEL 9.4", "win": "Windows Server 2022 Standard"}
EXTRA = (("ist-yeni-01", "10.20.40.11"), ("ist-yeni-02", "10.20.40.12"))
COMPARED = ("name", "fqdn", "serial_number", "os", "os_version", "cpu_count", "ram", "virtual", "ip_address")
PHASES = (
    ("baseline", "Başlangıç", {OLD: ("active", 1)}),
    ("shadow", "Gölge mod", {OLD: ("active", 1), NEW: ("shadow", 2)}),
    ("promote", "Etkin + devir", {OLD: ("retiring", 2), NEW: ("active", 1)}),
    ("retire", "Emeklilik", {OLD: ("retired", 2), NEW: ("active", 1)}),
)
LIFECYCLE_TR = {"active": "Etkin", "shadow": "Gölge", "retiring": "Devrediliyor", "retired": "Emekli"}


def sha256(path: str) -> str:
    return hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()


def kesif_b_raw(hosts: list[Host]) -> list[dict[str, Any]]:
    """Keşif-B'nin yerel yükü: BMC'nin gördüğü sunucuların çoğu (her 8. sunucuyu görmez) + 2 yeni sunucu."""
    seen = [h for h in hosts if h.in_bmc]
    raw = []
    for i, h in enumerate(seen):
        if i % 8 == 3:
            continue  # kapsam farkı: Keşif-B bu sunucuyu görmüyor
        raw.append({"id": f"kb-{i:04d}", "host": h.name.upper() if h.family == "win" else h.name, "dns_name": h.fqdn,
                    "hw_serial": h.serial, "smbios_uuid": h.bios_uuid, "platform": OS[h.family][4],
                    "os_name": KB_OS[h.family], "os_release": OS[h.family][2], "sockets": h.cpu,
                    "memory_mb": h.ram, "is_virtual": h.virtual, "addresses": [h.ip], "nics": [h.mac.lower()]})
    for j, (name, ip) in enumerate(EXTRA):
        raw.append({"id": f"kb-y{j}", "host": name, "dns_name": f"{name}.corp.ornek.local",
                    "hw_serial": f"CZKB000{j + 1}X", "smbios_uuid": f"4b450000-0000-4000-8000-00000000000{j + 1}",
                    "platform": "Linux", "os_name": KB_OS["linux"], "os_release": "9.4", "sockets": 4,
                    "memory_mb": 16384, "is_virtual": False, "addresses": [ip], "nics": [f"00:50:56:4b:00:0{j + 1}"]})
    return raw


def registry_for(md: Any, states: dict[str, tuple[str, int]]) -> tuple[SourceRegistry, Reconciler]:
    sources = copy.deepcopy(md.data_sources)
    if NEW in states and not any(s["name"] == NEW for s in sources):
        sources.append({"name": NEW, "label_tr": "Keşif-B (tatbikat ürünü)", "role": ROLE, "role_rank": 2,
                        "lifecycle": "shadow", "connector": "tatbikat", "mappings": []})
    for s in sources:
        if s["name"] in states:
            s["lifecycle"], s["role_rank"] = states[s["name"]]
    reg = SourceRegistry.from_metadata({"source_roles": md.source_roles, "data_sources": sources})
    problems = reg.validate()
    if problems:
        raise SystemExit("Tatbikat kaynak tanımı geçersiz: " + "; ".join(problems))
    return reg, Reconciler.from_metadata(md.hierarchy, md.reconciliation, reg)


def golden(p: Pipeline, by_ci: dict[str, list[Any]], reconciler: Reconciler) -> dict[str, dict[str, Any]]:
    index = p.engine.resolver.index
    out: dict[str, dict[str, Any]] = {}
    for ci, recs in by_ci.items():
        cls = index.profile(ci).class_name
        inputs = [SourceValues(r.data_source, dict(r.attributes), r.observed_at) for r in recs]
        if ci in p.manual_values:
            vals = dict(p.manual_values[ci])
            observed = vals.pop("_observed")
            inputs.append(SourceValues("Manual", vals, observed))
        report = reconciler.reconcile_report(cls, inputs, NOW)
        out[ci] = {"class": cls, "decisions": report.decisions}
    return out


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


def norm(v: Any) -> str:
    return str(v).strip().casefold() if v is not None else ""


def build() -> dict[str, Any]:
    p = Pipeline()
    md = p.md
    _refs, hosts = feed(p)
    names: dict[str, str] = {}
    by_ci: dict[str, list[Any]] = {}
    for r in p.records:
        res = r["resolution"]
        if res.outcome in (Outcome.LINKED, Outcome.RULE_MATCH, Outcome.SCORE_MATCH, Outcome.NEW):
            by_ci.setdefault(res.ci_sys_id, []).append(r["ingest"])
            names.setdefault(res.ci_sys_id, r["ingest"].attributes.get("name") or res.ci_sys_id[:8])

    mapping = FieldMapping.from_dict(yaml.safe_load(MAPPING.read_text(encoding="utf-8")))
    kb_records = [mapping.apply(raw, run_id=p.records[0]["ingest"].run_id, observed_at=NOW - timedelta(hours=2))
                  for raw in kesif_b_raw(hosts)]
    kb_records = [r for r in kb_records if r is not None]
    rules_sha = {f: sha256(f) for f in RULE_FILES}
    old_map = yaml.safe_load((REPO_ROOT / "connectors/bmc_discovery/host.mapping.yaml").read_text(encoding="utf-8"))
    mapping_gap = sorted(set(old_map["attributes"]) - set(yaml.safe_load(MAPPING.read_text(encoding="utf-8"))["attributes"]))

    phases: list[dict[str, Any]] = []
    previous: dict[str, dict[str, Any]] | None = None
    unmatched: list[Any] = []
    for pid, title, states in PHASES:
        reg, reconciler = registry_for(md, states)
        new_cis: list[str] = []
        if pid == "shadow":
            # Gölge kaynak: kimlik çözülür; eşleşen bağlanır, eşleşmeyen CI AÇMAZ (kapsam farkı olarak raporlanır).
            assert not reg.may_create_ci(NEW) and not reg.writes_attributes(NEW)
            for rec in kb_records:
                sr = source_record_from_ingest(rec)
                keys = extract_keys(sr)
                res = p.engine.resolver.resolve(sr, keys)
                if res.outcome in (Outcome.LINKED, Outcome.RULE_MATCH, Outcome.SCORE_MATCH):
                    p.engine._attach(sr, keys, res.ci_sys_id)
                    by_ci[res.ci_sys_id].append(rec)
                else:
                    unmatched.append(rec)
        elif pid == "promote":
            assert reg.may_create_ci(NEW) and not reg.may_create_ci(OLD) and reg.writes_attributes(OLD)
            for rec in unmatched:
                res = p.engine.process(source_record_from_ingest(rec))
                if res.outcome is Outcome.NEW:
                    by_ci[res.ci_sys_id] = [rec]
                    names[res.ci_sys_id] = rec.attributes.get("name")
                    new_cis.append(res.ci_sys_id)
        elif pid == "retire":
            assert not reg.writes_attributes(OLD) and not reg.counts_for_staleness(OLD)

        gold = golden(p, by_ci, reconciler)
        changes: list[dict[str, Any]] = []       # golden record değeri değişti
        provenance: list[dict[str, Any]] = []    # değer aynı, kazanan kaynak değişti
        orphaned: list[dict[str, Any]] = []      # öznitelik kaynağını yitirdi (değer kalır, sahipsiz)
        if previous is not None:
            for ci, g in gold.items():
                if ci not in previous:
                    continue
                before = previous[ci]["decisions"]
                for attr, d in g["decisions"].items():
                    b = before.get(attr)
                    row = {"ci": ci, "name": names.get(ci), "attribute": attr,
                           "before": None if b is None else b.value, "before_source": None if b is None else b.data_source,
                           "after": d.value, "after_source": d.data_source}
                    if b is None or b.value != d.value:
                        changes.append(row)
                    elif b.data_source != d.data_source:
                        provenance.append(row)
                for attr, b in before.items():
                    if attr not in g["decisions"]:
                        orphaned.append({"ci": ci, "name": names.get(ci), "attribute": attr, "before": b.value,
                                         "before_source": b.data_source})
        # Gölge uyumu: yetkili değer ile gölge kaynağın değeri (normalleştirilmiş) aynı mı?
        agreement: dict[str, list[int]] = {}
        for g in gold.values():
            for attr, d in g["decisions"].items():
                for src, val in d.shadow:
                    if src == NEW and attr in COMPARED:
                        slot = agreement.setdefault(attr, [0, 0])
                        slot[0] += 1
                        slot[1] += norm(val) == norm(d.value)
        roles_by_ci = {ci: {reg.role_of(r.data_source) for r in recs if r.data_source in reg.sources
                            and reg.counts_for_staleness(r.data_source)} for ci, recs in by_ci.items()}
        discovery_seen = sum(1 for roles in roles_by_ci.values() if roles & set(DISCOVERY_ROLES))
        risk = []
        if pid == "promote":
            # Emeklilik öncesi: BMC dışında hiçbir keşif rolündeki kaynağın görmediği CI'lar
            for ci, recs in sorted(by_ci.items(), key=lambda kv: str(names.get(kv[0]))):
                discovery_sources = {r.data_source for r in recs if reg.sources.get(r.data_source) is not None
                                     and reg.role_of(r.data_source) in DISCOVERY_ROLES}
                if discovery_sources == {OLD}:
                    risk.append({"ci": ci, "name": names.get(ci)})
        preview: dict[str, int] = {}
        if pid == "promote":
            # Emeklilik önizlemesi (kuru çalıştırma): eski ürün emekli olsaydı hangi öznitelikler hiçbir kaynaktan
            # değer alamazdı? Bunlar emeklilikte sahipsiz kalır; eşleme genişletilmeli veya bilinçli bırakılmalı.
            _r, retire_reconciler = registry_for(md, PHASES[3][2])
            dry = golden(p, by_ci, retire_reconciler)
            for ci, g in gold.items():
                for attr in g["decisions"]:
                    if attr not in dry[ci]["decisions"]:
                        preview[attr] = preview.get(attr, 0) + 1
        by_attr: dict[str, dict[str, Any]] = {}
        for ch in changes:
            slot = by_attr.setdefault(ch["attribute"], {"attribute": ch["attribute"], "count": 0, "sources": set()})
            slot["count"] += 1
            slot["sources"].add(ch["after_source"])
        phases.append({
            "id": pid, "title": title,
            "lifecycle": [{"source": s, "label": "Keşif-B (tatbikat)" if s == NEW else s, "lifecycle": lc,
                           "lifecycle_label": LIFECYCLE_TR[lc], "role_rank": rank} for s, (lc, rank) in states.items()],
            "ci_count": len(gold), "discovery_coverage": discovery_seen,
            "golden_changes": len(changes), "provenance_changes": len(provenance),
            "provenance_by_source": sorted({f"{c['before_source']} → {c['after_source']}" for c in provenance}),
            "orphaned": orphaned[:20], "orphaned_count": len(orphaned),
            "changes_by_attribute": [{**v, "sources": sorted(v["sources"])} for v in
                                     sorted(by_attr.values(), key=lambda x: (-x["count"], x["attribute"]))],
            "change_samples": sorted(changes, key=lambda c: (c["attribute"], str(c["name"])))[:12],
            "new_cis": [{"ci": c, "name": names.get(c)} for c in new_cis],
            "unmatched_shadow": [r.attributes.get("name") for r in unmatched] if pid == "shadow" else [],
            "agreement": [{"attribute": a, "compared": n, "agree": k, "pct": round(100 * k / n, 1)}
                          for a, (n, k) in sorted(agreement.items())],
            "retire_risk": risk,
            "retire_preview": [{"attribute": a, "count": n} for a, n in sorted(preview.items(), key=lambda kv: (-kv[1], kv[0]))],
            "orphaned_by_attribute": [{"attribute": a, "count": n} for a, n in sorted(
                _count(o["attribute"] for o in orphaned).items(), key=lambda kv: (-kv[1], kv[0]))],
            "rules_sha256": {f: sha256(f) for f in RULE_FILES},
            "may_create_ci": {s: reg.may_create_ci(s) for s in states},
            "writes_attributes": {s: reg.writes_attributes(s) for s in states},
        })
        previous = gold

    shadow, promote, retire = phases[1], phases[2], phases[3]
    retire_writers = {c["after_source"] for c in retire["change_samples"]} | {
        x.split(" → ")[1] for x in retire["provenance_by_source"]}
    checks = [
        {"text": "Gölge modda golden record'da hiçbir değer ve kaynak değişmedi",
         "met": shadow["golden_changes"] == 0 and shadow["provenance_changes"] == 0},
        {"text": "Gölge kaynak eşleşmeyen kayıt için CI açmadı (kapsam farkı olarak raporlandı)",
         "met": not shadow["new_cis"] and len(shadow["unmatched_shadow"]) == len(EXTRA)},
        {"text": "Etkin moda alınınca yeni ürün yetkili kaynak oldu (değer veya kaynak değişimleri yalnız Keşif-B lehine)",
         "met": promote["golden_changes"] + promote["provenance_changes"] > 0
         and {c["after_source"] for c in promote["change_samples"]} <= {NEW}
         and all(x.endswith(f"→ {NEW}") for x in promote["provenance_by_source"])},
        {"text": "Etkin moda alınınca gölgedeyken eşleşmeyen kayıtlar CI açtı", "met": len(promote["new_cis"]) == len(EXTRA)},
        {"text": "Devredilen ürün (BMC) yeni CI açamaz, değeri yedek olarak kalır",
         "met": promote["may_create_ci"][OLD] is False and promote["writes_attributes"][OLD] is True},
        {"text": "Emeklilik öncesi yalnız eski ürünün gördüğü CI'lar listelendi", "met": len(promote["retire_risk"]) > 0},
        {"text": "Emeklilik önizlemesi sahipsiz kalacak alanları önceden gösterdi ve gerçekleşenle birebir aynı",
         "met": bool(promote["retire_preview"]) and promote["retire_preview"] == retire["orphaned_by_attribute"]},
        {"text": "Emekli ürün değer yazmıyor", "met": OLD not in retire_writers and retire["writes_attributes"][OLD] is False},
        {"text": "Uzlaştırma ve tanımlama kuralları hiçbir aşamada değişmedi (SHA-256)",
         "met": all(ph["rules_sha256"] == rules_sha for ph in phases)},
    ]
    return {
        "title": "Ürün değişimi tatbikatı", "synthetic": True, "generated_at": fmt(NOW),
        "role": ROLE, "role_label": "Keşif",
        "old": {"name": OLD, "label": "BMC Helix Discovery"}, "new": {"name": NEW, "label": "Keşif-B (tatbikat ürünü)",
                                                                      "mapping": str(MAPPING.relative_to(REPO_ROOT))},
        "note": ("Sentetik tatbikat: Keşif-B gerçek bir ürün değildir. Aynı adımlar gerçek bir ürün değişiminde "
                 "data_sources.yaml'daki yaşam döngüsü değişiklikleriyle (taslak → onay → yayın) uygulanır."),
        "rules_sha256": rules_sha, "phases": phases, "checks": checks, "mapping_gap": mapping_gap,
        "labels": {k: v.get("label_tr", k) for k, v in sorted(md.fields_of("cmdb_ci_linux_server").items())},
        "passed": all(c["met"] for c in checks),
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="drill.json güncel ve tüm denetimler geçiyor mu? (CI)")
    args = parser.parse_args(argv)
    data = build()
    text = render(data)
    failed = [c["text"] for c in data["checks"] if not c["met"]]
    if failed:
        print("Tatbikat denetimi başarısız: " + "; ".join(failed), file=sys.stderr)
        return 1
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("apps/web/content/drill.json güncel değil: python tools/build_drill.py", file=sys.stderr)
            return 1
        print("drill.json güncel; tatbikatın tüm denetimleri geçti.")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"drill.json yazıldı: {len(data['checks'])} denetim geçti; " + ", ".join(
        f"{ph['title']}: {ph['golden_changes']} değer, {ph['provenance_changes']} kaynak değişimi"
        for ph in data["phases"][1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
