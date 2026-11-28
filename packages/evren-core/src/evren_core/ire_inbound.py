"""IRE uyumlu alım (U9.3b, F2-05, ADR-0045): ServiceNow IRE yükünü Evren alım kayıtlarına çevirir.

ServiceNow CMDB'ye bugün IRE API ile (``/api/now/identifyreconcile``) yazan araçlar, Evren ana kaynak olunca aynı yükü
Evren'in alım geçidine gönderir. Yük **golden record'a yazılmaz**: her öğe aracın veri kaynağı adına bir kaynak kaydı
olur ve her zamanki yoldan (Kafka → uzlaştırıcı: kimlik çözümleme, uzlaştırma) geçer (kural 1, 2).

Bu modül saf mantıktır (G/Ç yok): doğrulama, dönüştürme ve IRE biçiminde yanıt. Geçit kimliği, kaynak yetkisini,
çalışmayı ve Kafka teslimini yönetir (``services/ingest-gateway``).

ServiceNow'dan bilinçli farklar:

* **Kaynak yerel anahtarı zorunlu** (``sys_object_source_info.source_native_key``): eskime, silinme ve geri alınabilir
  birleştirme bu anahtarla izlenir. Anahtarsız öğe reddedilir.
* **Ya hep ya hiç:** bir öğe hatalıysa hiçbiri alınmaz (geçidin toplu gönderim sözleşmesi).
* **Asenkron:** yanıt anında CI'ın sys_id'si bilinmez; öğe ``QUEUED`` döner, ``sysId`` ``Unknown``'dır. Sonuç
  çalışma durumundan izlenir.
* ``related`` (bağımlı öğeler) ve ``referenceItems`` bu sürümde işlenmez; yanıtta uyarı olarak belirtilir.

Bağımlı sınıflar (ADR-0029; ör. uygulama): ServiceNow'daki gibi ana CI ilişkisiyle gelir (uygulama ``Runs on::Runs``
sunucu). Kuraldaki ilişki tipi ve ana sınıfla eşleşen ilişki, kaydın ana kaydı (``parent``) olur; ilişki yoksa öğe
uyarıyla alınır ve kimliği çözülemediği için eşleştirme kuyruğuna düşer.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from .envelope import IngestRecord, LookupData, ParentRef, RelationRef
from .identity.rules import RuleSet

__all__ = ["IreInputError", "IreItem", "IreParse", "parse_payload", "response"]

_SYSID = re.compile(r"^[0-9a-f]{32}$")
_SN_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?Z?$")
MAX_KEY = 512
MAX_VALUE = 4000
TOP_LEVEL = {"items", "relations", "referenceItems"}
ITEM_KEYS = {"className", "values", "lookup", "related", "internal_id", "sys_object_source_info", "settings"}
LOOKUP = {"cmdb_serial_number": (("serial_number", "serial_numbers"),),
          "cmdb_ci_network_adapter": (("mac_address", "mac_addresses"), ("ip_address", "ip_addresses")),
          "cmdb_ci_ip_address": (("ip_address", "ip_addresses"),)}


class IreInputError(ValueError):
    """Yük reddedildi. ``problems``: (konum, açıklama) — girdi değerini içermez."""

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        super().__init__(f"{len(problems)} sorun")
        self.problems = problems

    @property
    def detail(self) -> str:
        shown = "; ".join(f"{loc}: {msg}" for loc, msg in self.problems[:10])
        more = f" (+{len(self.problems) - 10} sorun daha)" if len(self.problems) > 10 else ""
        return shown + more


@dataclass
class IreItem:
    index: int
    class_name: str
    source_key: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class IreParse:
    records: list[IngestRecord]
    items: list[IreItem]
    relations: list[dict[str, Any]]
    warnings: list[str]


def _observed(raw: Any, now: datetime, loc: str, problems: list[tuple[str, str]]) -> datetime:
    if raw in (None, ""):
        return now
    if not isinstance(raw, str) or not _SN_TIME.match(raw.strip()):
        problems.append((loc, "zaman 'YYYY-AA-GG ss:dd:sn' (UTC) biçiminde olmalı"))
        return now
    try:
        t = datetime.fromisoformat(raw.strip().replace(" ", "T").rstrip("Z")).replace(tzinfo=UTC)
    except ValueError:
        problems.append((loc, "zaman çözümlenemedi"))
        return now
    if t > now + timedelta(minutes=10):
        problems.append((loc, "zaman gelecekte"))
    return t


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def parse_payload(doc: Any, *, md: Any, data_source: str, run_id: UUID, now: datetime,
                  max_items: int) -> IreParse:
    """IRE yükünü doğrular ve alım kayıtlarına çevirir; sorun varsa ``IreInputError`` (hiçbir kayıt dönmez)."""
    problems: list[tuple[str, str]] = []
    warnings: list[str] = []
    if not isinstance(doc, Mapping):
        raise IreInputError([("gövde", "JSON nesnesi olmalı")])
    for k in set(doc) - TOP_LEVEL:
        problems.append((str(k)[:40], "bilinmeyen alan"))
    if doc.get("referenceItems"):
        warnings.append("referenceItems bu sürümde işlenmez")
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        raise IreInputError([*problems, ("items", "en az bir öğe içeren dizi olmalı")])
    if len(items) > max_items:
        raise IreInputError([("items", f"istek başına en çok {max_items} öğe")])
    rel_types = {str(t["name"]) for t in md.relationship_types}
    rules = RuleSet.from_metadata(md.identification, md.hierarchy)

    parsed: list[dict[str, Any]] = []
    seen_keys: dict[str, int] = {}
    for i, it in enumerate(items):
        loc = f"items[{i}]"
        if not isinstance(it, Mapping):
            problems.append((loc, "nesne olmalı"))
            parsed.append({})
            continue
        for k in set(it) - ITEM_KEYS:
            problems.append((f"{loc}.{str(k)[:40]}", "bilinmeyen alan"))
        cls = it.get("className")
        if not isinstance(cls, str) or cls not in md.hierarchy or "cmdb_ci" not in md.hierarchy.ancestors(cls):
            problems.append((f"{loc}.className", "CMDB sınıfı sözlükte yok"))
            parsed.append({})
            continue
        info = it.get("sys_object_source_info")
        info = info if isinstance(info, Mapping) else {}
        key = info.get("source_native_key")
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY:
            problems.append((f"{loc}.sys_object_source_info.source_native_key",
                             f"zorunlu (kaynaktaki kalıcı nesne kimliği, en çok {MAX_KEY} karakter)"))
            parsed.append({})
            continue
        key = key.strip()
        if key in seen_keys:
            problems.append((f"{loc}.sys_object_source_info.source_native_key",
                             f"items[{seen_keys[key]}] ile aynı; bir istekte her nesne bir kez gönderilir"))
        seen_keys.setdefault(key, i)
        name = info.get("source_name")
        if name not in (None, "", data_source):
            problems.append((f"{loc}.sys_object_source_info.source_name", "sysparm_data_source ile aynı olmalı"))
        observed = _observed(info.get("source_recency_timestamp"), now,
                             f"{loc}.sys_object_source_info.source_recency_timestamp", problems)
        item = IreItem(i, cls, key)
        fields = md.fields_of(cls)
        values = it.get("values", {})
        attrs: dict[str, Any] = {}
        if not isinstance(values, Mapping):
            problems.append((f"{loc}.values", "nesne olmalı"))
            values = {}
        for fname, v in values.items():
            if not isinstance(fname, str):
                problems.append((f"{loc}.values", "alan adı metin olmalı"))
                continue
            if fname.startswith("sys_"):
                item.warnings.append(f"{fname}: sistem alanı, yok sayıldı")
                continue
            fdef = fields.get(fname)
            if fdef is None:
                item.warnings.append(f"{fname}: {cls} sınıfında bu alan yok, yok sayıldı")
                continue
            if not _scalar(v) or (isinstance(v, str) and len(v) > MAX_VALUE):
                problems.append((f"{loc}.values.{fname[:40]}", f"tek değer olmalı (en çok {MAX_VALUE} karakter)"))
                continue
            if fdef.get("type") == "reference" and v not in (None, "") and not (isinstance(v, str) and _SYSID.match(v)):
                item.warnings.append(f"{fname}: referans alanı sys_id bekler; görünen değer işlenmeyebilir")
            attrs[fname] = v
        lookup: dict[str, list[str]] = {"serial_numbers": [], "mac_addresses": [], "ip_addresses": []}
        raw_lookup = it.get("lookup") or []
        if not isinstance(raw_lookup, list):
            problems.append((f"{loc}.lookup", "dizi olmalı"))
            raw_lookup = []
        for j, lk in enumerate(raw_lookup):
            lcls = lk.get("className") if isinstance(lk, Mapping) else None
            lvals = lk.get("values") if isinstance(lk, Mapping) else None
            if lcls not in LOOKUP or not isinstance(lvals, Mapping):
                item.warnings.append(f"lookup[{j}]: {str(lcls)[:60]} işlenmez (seri, ağ adaptörü ve IP adresi işlenir)")
                continue
            for src_field, target in LOOKUP[lcls]:
                val = lvals.get(src_field)
                if isinstance(val, str) and val.strip():
                    lookup[target].append(val.strip()[:256])
        if it.get("related"):
            item.warnings.append("related (bağımlı öğeler) bu sürümde işlenmez")
        parsed.append({"item": item, "attrs": attrs, "lookup": lookup, "observed": observed, "relations": []})

    relations_out: list[dict[str, Any]] = []
    raw_rel = doc.get("relations") or []
    if not isinstance(raw_rel, list):
        problems.append(("relations", "dizi olmalı"))
        raw_rel = []
    for r_i, rel in enumerate(raw_rel):
        loc = f"relations[{r_i}]"
        if not isinstance(rel, Mapping):
            problems.append((loc, "nesne olmalı"))
            continue
        p, c, t = rel.get("parent"), rel.get("child"), rel.get("type")
        if not (isinstance(p, int) and isinstance(c, int) and 0 <= p < len(items) and 0 <= c < len(items) and p != c):
            problems.append((loc, "parent ve child, items dizisinde farklı iki sıra olmalı"))
            continue
        if not isinstance(t, str) or t not in rel_types:
            problems.append((f"{loc}.type", "ilişki tipi sözlükte yok (ör. 'Runs on::Runs')"))
            continue
        if not parsed[p] or not parsed[c]:
            continue  # uçlardan biri zaten hatalı
        target = parsed[c]["item"]
        rule = rules.for_class(parsed[p]["item"].class_name)
        if (rule is not None and not rule.independent and rule.parent_relation and rule.parent_relation[0] == t
                and md.hierarchy.is_a(target.class_name, rule.parent_relation[1]) and parsed[p].get("parent") is None):
            parsed[p]["parent"] = ParentRef(source_key=target.source_key, data_source=data_source)
        parsed[p]["relations"].append(RelationRef(type=t, direction="parent", target_source_key=target.source_key,
                                                  target_data_source=data_source, target_class_hint=target.class_name))
        relations_out.append({"index": r_i, "type": t, "parent": p, "child": c})

    if problems:
        raise IreInputError(problems)
    records = []
    for entry in parsed:
        item = entry["item"]
        rule = rules.for_class(item.class_name)
        if rule is not None and not rule.independent and rule.parent_relation and entry.get("parent") is None:
            item.warnings.append(f"bağımlı sınıf: ana CI ilişkisi ({rule.parent_relation[0]} → "
                                 f"{rule.parent_relation[1]}) yok; kimlik çözülemez, eşleştirme kuyruğuna düşer")
        records.append(IngestRecord(
            data_source=data_source, run_id=run_id, source_key=item.source_key, class_hint=item.class_name,
            attributes=entry["attrs"], lookup=LookupData(**entry["lookup"]), relations=entry["relations"][:512],
            parent=entry.get("parent"), observed_at=entry["observed"]))
    return IreParse(records, [e["item"] for e in parsed], relations_out, warnings)


def response(parse: IreParse, *, operation: str, run_id: UUID | None, status_path: str | None) -> dict[str, Any]:
    """IRE biçiminde yanıt. ``operation``: ``QUEUED`` (alındı, uzlaştırıcıda işlenecek) veya ``VALIDATED`` (yalnızca
    doğrulandı, ``/query``). ``sysId`` asenkron işlendiği için ``Unknown``; ``evren`` bölümü çalışmayı gösterir."""
    items = [{"className": it.class_name, "operation": operation, "sysId": "Unknown",
              "identifierEntrySysId": "Unknown", "inputIndices": [it.index], "errors": [],
              "warnings": it.warnings} for it in parse.items]
    rels = [{"className": r["type"], "operation": operation, "parent": r["parent"], "child": r["child"],
             "inputIndices": [r["index"]], "errors": []} for r in parse.relations]
    note = ("Kayıtlar alındı; kimlik çözümleme ve uzlaştırma uzlaştırıcıda yapılır. CI sys_id'si bu yüzden yanıtta "
            "yoktur; çalışmanın durumu status_path adresinden izlenir." if operation == "QUEUED"
            else "Yük doğrulandı; hiçbir şey yazılmadı.")
    return {"result": {"items": items, "relations": rels,
                       "evren": {"run_id": str(run_id) if run_id else None, "status_path": status_path,
                                 "accepted": len(parse.records) if operation == "QUEUED" else 0,
                                 "warnings": parse.warnings, "note": note}}}
