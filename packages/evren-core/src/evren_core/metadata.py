"""Metadata sözlüğü: yükleme, doğrulama ve veritabanı tohum SQL'i üretimi.

``metadata/*.yaml`` dosyaları tek doğruluk kaynağıdır. Bu modül:

* dosyaları yükler ve tutarlılığını doğrular (``validate``),
* ``meta.*``, ``cmdb.cmdb_rel_type``, ``ingest.data_source`` için idempotent tohum SQL'i,
* ServiceNow tablo adlarıyla sorgulanabilen ``sn.<sınıf>`` görünümlerini,
* sık sorgulanan JSONB alanları için ifade indekslerini üretir (``generate_seed_sql``).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .classes import ClassHierarchy
from .identity.rules import ATTRIBUTE_KEY_TYPES
from .sources import SourceRegistry, SourceRegistryError

__all__ = [
    "CORE_COLUMNS",
    "Metadata",
    "ValidationReport",
    "generate_seed_sql",
    "jsonb_expression",
    "load_metadata",
    "metadata_field_resolver",
    "sys_id_for",
    "validate",
]

#: cmdb.cmdb_ci fiziksel kolonları (DDL ile birebir; testler doğrular).
CORE_COLUMNS: tuple[str, ...] = (
    "name", "short_description", "serial_number", "asset_tag", "ip_address", "mac_address", "fqdn", "dns_domain",
    "operational_status", "install_status", "used_for", "discovery_source", "first_discovered", "last_discovered",
    "manufacturer", "model_id", "location", "company", "department", "support_group", "assignment_group",
    "change_control", "managed_by", "owned_by", "assigned_to", "duplicate_of", "correlation_id", "object_id",
    "category", "subcategory", "install_date", "warranty_expiration", "life_cycle_stage",
    "life_cycle_stage_status", "comments",
)
SYSTEM_COLUMNS = ("sys_created_on", "sys_created_by", "sys_updated_on", "sys_updated_by", "sys_mod_count")
VALID_TYPES = {"string", "integer", "decimal", "boolean", "glide_date_time", "glide_date", "reference"}
VALID_SENSITIVITY = {"public", "internal", "confidential", "restricted"}
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_ELEMENT = re.compile(r"^[a-z][a-z0-9_]{0,79}$")

_CASTS = {
    "integer": "::bigint",
    "decimal": "::numeric",
    "boolean": "::boolean",
    "glide_date_time": "::timestamptz",
    "glide_date": "::date",
}


def sys_id_for(*parts: str) -> str:
    """Tohum kayıtları için deterministik sys_id (yeniden yüklemede aynı kalır)."""
    return hashlib.sha256(("evren:" + ":".join(parts)).encode("utf-8")).hexdigest()[:32]


def jsonb_expression(element: str, internal_type: str, alias: str = "c") -> str:
    """Görünüm ve ifade indeksinde aynı olmalı ki planlayıcı indeksi kullansın."""
    base = f"({alias}.class_attributes->>'{element}')" if alias else f"(class_attributes->>'{element}')"
    cast = _CASTS.get(internal_type)
    return f"({base}{cast})" if cast else base


@dataclass
class Metadata:
    classes: list[dict[str, Any]]
    reference_tables: list[dict[str, Any]]
    relationship_types: list[dict[str, Any]]
    choices: list[dict[str, Any]]
    data_sources: list[dict[str, Any]]
    identification: list[dict[str, Any]]
    reconciliation: dict[str, Any]
    source_roles: list[dict[str, Any]] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)
    hierarchy: ClassHierarchy = field(init=False)

    def __post_init__(self) -> None:
        self.hierarchy = ClassHierarchy.from_metadata(self.classes)

    @property
    def sources(self) -> SourceRegistry:
        """Kaynak rolleri ve veri kaynakları (ADR-0009)."""
        return SourceRegistry.from_metadata({"source_roles": self.source_roles, "data_sources": self.data_sources})

    @property
    def class_map(self) -> dict[str, dict[str, Any]]:
        return {c["name"]: c for c in self.classes}

    def fields_of(self, class_name: str, *, inherited: bool = True) -> dict[str, dict[str, Any]]:
        chain = self.hierarchy.ancestors(class_name) if inherited else [class_name]
        out: dict[str, dict[str, Any]] = {}
        cmap = self.class_map
        for name in reversed(chain):
            for f in cmap[name].get("fields") or []:
                out[f["name"]] = {**f, "_table": name}
        return out


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_metadata(directory: str | Path, *, apply_change_files: bool = True) -> Metadata:
    """Temel YAML dosyalarını yükler; ardından ``changes/`` altındaki yayınlanmış değişiklik setlerini ad sırasıyla
    uygular (ADR-0015). ``apply_change_files=False``: yalnız temel dosyalar (değişiklik setinin etki analizi için)."""
    d = Path(directory)
    md = _load_base(d)
    if apply_change_files:
        from .changeset import ChangeError, apply_changes, load_change_files

        for name, changes in load_change_files(d / "changes"):
            try:
                md = apply_changes(md, changes)
            except ChangeError as exc:
                raise ChangeError([f"{name}: {e}" for e in exc.errors]) from None
    return md


def _load_base(d: Path) -> Metadata:
    classes = _read(d / "classes.yaml")
    sources = _read(d / "data_sources.yaml")
    return Metadata(
        classes=classes.get("classes", []),
        reference_tables=classes.get("reference_tables", []),
        relationship_types=_read(d / "relationship_types.yaml").get("relationship_types", []),
        choices=_read(d / "choices.yaml").get("choices", []),
        data_sources=sources.get("data_sources", []),
        source_roles=sources.get("source_roles", []),
        identification=_read(d / "identification_rules.yaml").get("rules", []),
        reconciliation=_read(d / "reconciliation_rules.yaml"),
        health=_read(d / "health_rules.yaml") if (d / "health_rules.yaml").exists() else {},
    )


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(md: Metadata) -> ValidationReport:
    r = ValidationReport()
    names = [c["name"] for c in md.classes]
    ref_names = {t["name"] for t in md.reference_tables}
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        r.errors.append(f"Tekrarlanan sınıf adları: {sorted(dupes)}")
    known = set(names)
    roots = [c["name"] for c in md.classes if not c.get("extends")]
    if roots != ["cmdb_ci"]:
        r.errors.append(f"Tek kök sınıf cmdb_ci olmalı; bulunan: {roots}")
    for c in md.classes:
        if not _NAME.match(c["name"]):
            r.errors.append(f"Geçersiz sınıf adı: {c['name']}")
        if c.get("extends") and c["extends"] not in known:
            r.errors.append(f"{c['name']}: üst sınıf tanımsız: {c['extends']}")
        if not c.get("label_tr"):
            r.errors.append(f"{c['name']}: label_tr zorunlu (Türkçe arayüz)")
    try:
        for n in names:
            md.hierarchy.ancestors(n)
    except ValueError as exc:
        r.errors.append(str(exc))
        return r

    for c in md.classes:
        ancestor_fields: set[str] = set()
        for a in md.hierarchy.ancestors(c["name"], include_self=False):
            ancestor_fields |= {f["name"] for f in md.class_map[a].get("fields") or []}
        seen: set[str] = set()
        for f in c.get("fields") or []:
            where = f"{c['name']}.{f.get('name')}"
            if not _ELEMENT.match(str(f.get("name", ""))):
                r.errors.append(f"{where}: geçersiz alan adı")
            if f["name"] in seen:
                r.errors.append(f"{where}: aynı sınıfta tekrar")
            seen.add(f["name"])
            if f["name"] in ancestor_fields:
                r.errors.append(f"{where}: üst sınıftaki alanı yeniden tanımlıyor")
            if f["name"] in SYSTEM_COLUMNS or f["name"] in ("sys_id", "sys_class_name", "class_attributes"):
                r.errors.append(f"{where}: sistem alanı adı kullanılamaz")
            if f.get("sensitivity", "internal") not in VALID_SENSITIVITY:
                r.errors.append(f"{where}: geçersiz hassasiyet {f.get('sensitivity')}")
            if f.get("type") not in VALID_TYPES:
                r.errors.append(f"{where}: geçersiz tip {f.get('type')}")
            if f.get("type") == "reference" and f.get("reference") not in known | ref_names:
                r.errors.append(f"{where}: referans hedefi tanımsız: {f.get('reference')}")
            if not f.get("label_tr"):
                r.errors.append(f"{where}: label_tr zorunlu")
            storage = f.get("storage", "jsonb")
            if storage == "column" and c["name"] != "cmdb_ci":
                r.errors.append(f"{where}: fiziksel kolon yalnızca cmdb_ci alanları için")
            if c["name"] == "cmdb_ci" and storage != "column":
                r.errors.append(f"{where}: cmdb_ci alanları fiziksel kolon olmalı (storage: column)")
    core = [f["name"] for f in md.class_map.get("cmdb_ci", {}).get("fields") or []]
    if tuple(core) != CORE_COLUMNS:
        missing = sorted(set(CORE_COLUMNS) - set(core))
        extra = sorted(set(core) - set(CORE_COLUMNS))
        r.errors.append(f"cmdb_ci alanları DDL ile uyumsuz (eksik: {missing}, fazla: {extra}, sıra farkı olabilir)")

    # Seçim listeleri
    choice_keys = set()
    for ch in md.choices:
        key = (ch["table"], ch["element"])
        choice_keys.add(key)
        fdef = md.fields_of(ch["table"], inherited=False).get(ch["element"]) if ch["table"] in known else None
        if fdef is None:
            r.errors.append(f"Seçim listesi tanımsız alana ait: {key}")
            continue
        if not fdef.get("choice"):
            r.errors.append(f"{key}: alan choice: true değil")
        values = [str(v["value"]) for v in ch["values"]]
        if len(values) != len(set(values)):
            r.errors.append(f"{key}: tekrarlanan seçim değeri")
        if fdef.get("type") == "integer" and not all(v.lstrip("-").isdigit() for v in values):
            r.errors.append(f"{key}: integer alan için sayısal olmayan seçim değeri")
    for c in md.classes:
        for f in c.get("fields") or []:
            if f.get("choice") and (c["name"], f["name"]) not in choice_keys:
                r.warnings.append(f"{c['name']}.{f['name']}: choice alanı için seçim listesi yok")

    # İlişki tipleri
    rel_names = [t["name"] for t in md.relationship_types]
    if len(rel_names) != len(set(rel_names)):
        r.errors.append("Tekrarlanan ilişki tipi")
    for t in rel_names:
        if not re.fullmatch(r"[^:]+::[^:]+", t):
            r.errors.append(f"İlişki tipi 'ebeveyn::çocuk' biçiminde olmalı: {t}")

    # Kaynak rolleri ve veri kaynakları (ADR-0009)
    sources = [s["name"] for s in md.data_sources]
    role_names = [r["name"] for r in md.source_roles]
    if len(sources) != len(set(sources)):
        r.errors.append("Tekrarlanan veri kaynağı")
    if len(role_names) != len(set(role_names)):
        r.errors.append("Tekrarlanan kaynak rolü")
    if set(sources) & set(role_names) - {"Manual"}:
        r.errors.append(f"Veri kaynağı ve rol adı çakışıyor: {sorted(set(sources) & set(role_names) - {'Manual'})}")
    for s in md.data_sources:
        if s.get("role") not in role_names:
            r.errors.append(f"{s['name']}: tanımsız kaynak rolü {s.get('role')}")
        if "kind" in s:
            r.errors.append(f"{s['name']}: kind veri kaynağında değil rolde tanımlanır")
    try:
        r.errors.extend(md.sources.validate())
    except SourceRegistryError as exc:
        r.errors.append(str(exc))
    role_set = set(role_names)

    # Sağlık kuralları (F1-09)
    health = md.health or {}
    weights = health.get("weights") or {}
    if weights and (set(weights) != {"completeness", "correctness", "compliance", "coverage"}
                    or sum(weights.values()) != 100):
        r.errors.append("sağlık: ağırlıklar dört boyut için tanımlı olmalı ve toplamı 100 olmalı")
    for rule in health.get("completeness", []):
        cls = rule.get("applies_to")
        if cls not in known:
            r.errors.append(f"sağlık[{cls}]: sınıf tanımsız")
            continue
        fields_ = md.fields_of(cls)
        for name in [*rule.get("required", []), *rule.get("recommended", [])]:
            if name not in fields_:
                r.errors.append(f"sağlık[{cls}]: alan tanımsız: {name}")
            elif fields_[name].get("deprecated"):
                r.warnings.append(f"sağlık[{cls}]: kullanımdan kaldırılmış alan: {name}")
    for name in (health.get("compliance") or {}).get("governance_fields", []):
        if name not in md.fields_of("cmdb_ci"):
            r.errors.append(f"sağlık: yönetişim alanı tanımsız: {name}")
    cov = (health.get("coverage") or {}).get("applies_to")
    if cov and cov not in known:
        r.errors.append(f"sağlık: kapsam sınıfı tanımsız: {cov}")

    # Tanımlama kuralları
    applies = [i["applies_to"] for i in md.identification]
    if len(applies) != len(set(applies)):
        r.errors.append("Bir sınıf için birden fazla tanımlama kuralı")
    for rule in md.identification:
        where = f"tanımlama[{rule['applies_to']}]"
        if rule["applies_to"] not in known:
            r.errors.append(f"{where}: sınıf tanımsız")
        prios = [e["priority"] for e in rule["entries"]]
        if len(prios) != len(set(prios)):
            r.errors.append(f"{where}: tekrarlanan öncelik")
        for e in rule["entries"]:
            for a in e["attributes"]:
                if a not in ATTRIBUTE_KEY_TYPES:
                    r.errors.append(f"{where}: desteklenmeyen öznitelik {a}")
        rel = (rule.get("parent_relation") or {}).get("type")
        if rel and rel not in rel_names:
            r.errors.append(f"{where}: ilişki tipi tanımsız {rel}")

    # Uzlaştırma
    rec = md.reconciliation
    for idx, rule in enumerate(rec.get("rules", [])):
        cls = rule.get("applies_to")
        where = f"uzlaştırma[{idx}:{cls}]"
        if cls not in known:
            r.errors.append(f"{where}: sınıf tanımsız")
            continue
        attrs = rule.get("attributes", "*")
        if attrs == "*":
            if cls != "cmdb_ci":
                r.errors.append(f"{where}: '*' kök dışı sınıfta yönetişim kurallarını gölgeler; alanları listeleyin")
        else:
            available = set(md.fields_of(cls))
            for a in attrs:
                if a not in available:
                    r.errors.append(f"{where}: '{a}' bu sınıfta veya atalarında yok")
        for role, prio in (rule.get("priorities") or {}).items():
            if role not in role_set:
                hint = " (ürün/veri kaynağı adı değil, kaynak rolü yazılır)" if role in sources else ""
                r.errors.append(f"{where}: kaynak rolü tanımsız {role}{hint}")
            if not isinstance(prio, int) or prio <= 0:
                r.errors.append(f"{where}: öncelik pozitif tam sayı olmalı ({role})")
    for d in rec.get("data_refresh", []):
        if d["applies_to"] not in known or d.get("role") not in role_set:
            r.errors.append(f"Veri tazeleme kuralı geçersiz: {d}")
        if float(d["effective_days"]) <= 0:
            r.errors.append(f"Veri tazeleme süresi pozitif olmalı: {d}")
    for s in rec.get("staleness", []):
        if s["applies_to"] not in known:
            r.errors.append(f"Eskime kuralı sınıfı tanımsız: {s['applies_to']}")
        if s["absent_candidate_after_days"] <= s["stale_after_days"]:
            r.errors.append(f"{s['applies_to']}: absent_candidate_after_days > stale_after_days olmalı")
    return r


# ---------------------------------------------------------------------------
# SQL üretimi
# ---------------------------------------------------------------------------


def _q(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _arr(values: Iterable[str] | None) -> str:
    if values is None:
        return "NULL"
    return "ARRAY[" + ", ".join(_q(v) for v in values) + "]::text[]"


def _partition_name(source: str) -> str:
    return "source_record_" + re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")


def generate_seed_sql(md: Metadata) -> str:
    report = validate(md)
    if not report.ok:
        raise ValueError("Metadata doğrulaması başarısız:\n- " + "\n- ".join(report.errors))
    out: list[str] = ["-- Bu dosya tools/load_metadata.py tarafından üretilir. Elle düzenlemeyin.", "BEGIN;",
                      "SET LOCAL client_min_messages = warning;", "SET CONSTRAINTS ALL DEFERRED;"]

    out.append("\n-- Kaynak rolleri (ADR-0009)")
    roles = {r["name"]: r for r in md.source_roles}
    for role in md.source_roles:
        out.append(
            "INSERT INTO meta.source_role (name, label_tr, kind, ci_attribute_source, description) VALUES "
            f"({_q(role['name'])}, {_q(role['label_tr'])}, {_q(role['kind'])}, "
            f"{_q(bool(role.get('ci_attribute_source', True)))}, {_q(role.get('description_tr'))}) "
            "ON CONFLICT (name) DO UPDATE SET label_tr = EXCLUDED.label_tr, kind = EXCLUDED.kind, "
            "ci_attribute_source = EXCLUDED.ci_attribute_source, description = EXCLUDED.description;"
        )

    out.append("\n-- Veri kaynakları ve kaynak kaydı bölümleri")
    for s in md.data_sources:
        role = roles[s["role"]]
        product = json.dumps(s.get("product") or {}, ensure_ascii=False, sort_keys=True)
        out.append(
            "INSERT INTO ingest.data_source (name, label_tr, kind, role, role_rank, lifecycle, product, connector, "
            "schedule, ci_attribute_source, notes) VALUES "
            f"({_q(s['name'])}, {_q(s['label_tr'])}, {_q(role['kind'])}, {_q(s['role'])}, {int(s.get('role_rank', 1))}, "
            f"{_q(s.get('lifecycle', 'active'))}, {_q(product)}::jsonb, {_q(s.get('connector'))}, {_q(s.get('schedule'))}, "
            f"{_q(bool(role.get('ci_attribute_source', True)))}, {_q(s.get('open_question'))}) "
            "ON CONFLICT (name) DO UPDATE SET label_tr = EXCLUDED.label_tr, kind = EXCLUDED.kind, role = EXCLUDED.role, "
            "role_rank = EXCLUDED.role_rank, lifecycle = EXCLUDED.lifecycle, product = EXCLUDED.product, "
            "connector = EXCLUDED.connector, schedule = EXCLUDED.schedule, "
            "ci_attribute_source = EXCLUDED.ci_attribute_source, notes = EXCLUDED.notes;"
        )
        out.append(f"CREATE TABLE IF NOT EXISTS ingest.{_partition_name(s['name'])} PARTITION OF ingest.source_record "
                   f"FOR VALUES IN ({_q(s['name'])});")

    out.append("\n-- Sınıflar ve referans tabloları (sys_db_object)")
    for c in md.classes:
        out.append(
            "INSERT INTO meta.sys_db_object (sys_id, name, label, label_tr, super_class, is_cmdb_class, verify_against_sn) "
            f"VALUES ({_q(sys_id_for('sys_db_object', c['name']))}, {_q(c['name'])}, {_q(c['label'])}, {_q(c['label_tr'])}, "
            f"{_q(c.get('extends'))}, true, {_q(bool(c.get('verify')))}) "
            "ON CONFLICT (name) DO UPDATE SET label = EXCLUDED.label, label_tr = EXCLUDED.label_tr, "
            "super_class = EXCLUDED.super_class, verify_against_sn = EXCLUDED.verify_against_sn;"
        )
    for t in md.reference_tables:
        out.append(
            "INSERT INTO meta.sys_db_object (sys_id, name, label, label_tr, is_cmdb_class) "
            f"VALUES ({_q(sys_id_for('sys_db_object', t['name']))}, {_q(t['name'])}, {_q(t['name'])}, {_q(t['label_tr'])}, false) "
            "ON CONFLICT (name) DO UPDATE SET label_tr = EXCLUDED.label_tr;"
        )

    out.append("\n-- Kalıtım kapanışı")
    out.append("DELETE FROM meta.class_closure;")
    pairs = []
    for c in md.classes:
        for depth, ancestor in enumerate(md.hierarchy.ancestors(c["name"])):
            pairs.append(f"({_q(ancestor)}, {_q(c['name'])}, {depth})")
    out.append("INSERT INTO meta.class_closure (ancestor, descendant, depth) VALUES\n  " + ",\n  ".join(pairs) + ";")

    out.append("\n-- Alan sözlüğü (sys_dictionary)")
    for c in md.classes:
        for f in c.get("fields") or []:
            out.append(
                "INSERT INTO meta.sys_dictionary (sys_id, name, element, column_label, label_tr, internal_type, max_length, "
                "reference, is_choice, storage, indexed, sensitivity, verify_against_sn) VALUES ("
                f"{_q(sys_id_for('sys_dictionary', c['name'], f['name']))}, {_q(c['name'])}, {_q(f['name'])}, "
                f"{_q(f.get('label', f['label_tr']))}, {_q(f['label_tr'])}, {_q(f['type'])}, {_q(f.get('max_length'))}, "
                f"{_q(f.get('reference'))}, {_q(bool(f.get('choice')))}, {_q(f.get('storage', 'jsonb'))}, "
                f"{_q(bool(f.get('indexed')))}, {_q(f.get('sensitivity', 'internal'))}, {_q(bool(f.get('verify')))}) "
                "ON CONFLICT (name, element) DO UPDATE SET label_tr = EXCLUDED.label_tr, internal_type = EXCLUDED.internal_type, "
                "max_length = EXCLUDED.max_length, reference = EXCLUDED.reference, is_choice = EXCLUDED.is_choice, "
                "storage = EXCLUDED.storage, indexed = EXCLUDED.indexed, sensitivity = EXCLUDED.sensitivity, "
                "verify_against_sn = EXCLUDED.verify_against_sn;"
            )

    out.append("\n-- Seçim listeleri (sys_choice)")
    for ch in md.choices:
        for seq, v in enumerate(ch["values"]):
            out.append(
                "INSERT INTO meta.sys_choice (sys_id, name, element, value, label, label_tr, sequence) VALUES ("
                f"{_q(sys_id_for('sys_choice', ch['table'], ch['element'], str(v['value'])))}, {_q(ch['table'])}, "
                f"{_q(ch['element'])}, {_q(str(v['value']))}, {_q(v['label'])}, {_q(v['label_tr'])}, {seq * 10}) "
                "ON CONFLICT (name, element, value) DO UPDATE SET label = EXCLUDED.label, label_tr = EXCLUDED.label_tr, "
                "sequence = EXCLUDED.sequence;"
            )

    out.append("\n-- İlişki tipleri")
    for t in md.relationship_types:
        parent, child = t["name"].split("::", 1)
        out.append(
            "INSERT INTO cmdb.cmdb_rel_type (sys_id, name, parent_descriptor, child_descriptor, label_tr) VALUES ("
            f"{_q(sys_id_for('cmdb_rel_type', t['name']))}, {_q(t['name'])}, {_q(parent)}, {_q(child)}, {_q(t.get('label_tr'))}) "
            "ON CONFLICT (name) DO UPDATE SET label_tr = EXCLUDED.label_tr;"
        )

    out.append("\n-- Tanımlama kuralları")
    for rule in md.identification:
        ident = sys_id_for("cmdb_identifier", rule["applies_to"])
        dependency = rule.get("parent_relation") or ({"parent_reference": rule["parent_reference"]}
                                                     if rule.get("parent_reference") else None)
        dep_sql = "NULL" if dependency is None else f"{_q(_json(dependency))}::jsonb"
        out.append(
            "INSERT INTO meta.cmdb_identifier (sys_id, applies_to, independent, description, dependency) VALUES ("
            f"{_q(ident)}, {_q(rule['applies_to'])}, {_q(bool(rule.get('independent', True)))}, "
            f"{_q(rule.get('description'))}, {dep_sql}) "
            "ON CONFLICT (applies_to) DO UPDATE SET independent = EXCLUDED.independent, "
            "description = EXCLUDED.description, dependency = EXCLUDED.dependency;"
        )
        out.append(f"DELETE FROM meta.cmdb_identifier_entry WHERE identifier = {_q(ident)};")
        for e in rule["entries"]:
            out.append(
                "INSERT INTO meta.cmdb_identifier_entry (sys_id, identifier, priority, search_table, attributes, evren_extension) "
                f"VALUES ({_q(sys_id_for('cmdb_identifier_entry', rule['applies_to'], str(e['priority'])))}, {_q(ident)}, "
                f"{e['priority']}, {_q(e.get('search_table'))}, {_arr(e['attributes'])}, {_q(bool(e.get('evren_extension')))});"
            )

    out.append("\n-- Uzlaştırma, veri tazeleme ve eskime kuralları")
    out.append("DELETE FROM meta.cmdb_reconciliation_definition;")
    rec = md.reconciliation
    for idx, rule in enumerate(rec.get("rules", [])):
        attrs = None if rule.get("attributes", "*") == "*" else list(rule["attributes"])
        for role, prio in rule["priorities"].items():
            out.append(
                "INSERT INTO meta.cmdb_reconciliation_definition (sys_id, applies_to, role, priority, attributes, description) "
                f"VALUES ({_q(sys_id_for('recon', str(idx), rule['applies_to'], role))}, {_q(rule['applies_to'])}, {_q(role)}, "
                f"{prio}, {_arr(attrs)}, {_q(rule.get('description'))});"
            )
    out.append("DELETE FROM meta.cmdb_datasource_refresh;")
    for d in rec.get("data_refresh", []):
        out.append(
            "INSERT INTO meta.cmdb_datasource_refresh (applies_to, role, effective_duration) VALUES ("
            f"{_q(d['applies_to'])}, {_q(d['role'])}, interval {_q(str(d['effective_days']) + ' days')});"
        )
    out.append("DELETE FROM meta.cmdb_staleness_rule;")
    for s in rec.get("staleness", []):
        out.append(
            "INSERT INTO meta.cmdb_staleness_rule (applies_to, stale_after, absent_candidate_after, require_all_sources_absent) "
            f"VALUES ({_q(s['applies_to'])}, interval {_q(str(s['stale_after_days']) + ' days')}, "
            f"interval {_q(str(s['absent_candidate_after_days']) + ' days')}, {_q(bool(s.get('require_all_sources_absent', True)))});"
        )

    out.append("\n-- JSONB ifade indeksleri (indexed: true)")
    for c in md.classes:
        for f in c.get("fields") or []:
            if f.get("indexed") and f.get("storage", "jsonb") == "jsonb":
                expr = jsonb_expression(f["name"], f["type"], alias="")
                out.append(f"CREATE INDEX IF NOT EXISTS cmdb_ci_attr_{f['name']} ON cmdb.cmdb_ci ({expr}) "
                           f"WHERE class_attributes ? {_q(f['name'])};")

    out.append("\n-- ServiceNow tablo adlarıyla sınıf görünümleri (sn şeması)")
    for c in md.classes:
        name = c["name"]
        classes = sorted(md.hierarchy.descendants(name))
        columns = ["c.sys_id", "c.sys_class_name"] + [f'c."{col}"' for col in CORE_COLUMNS]
        for element, fdef in md.fields_of(name).items():
            if fdef.get("storage", "jsonb") == "jsonb":
                columns.append(f'{jsonb_expression(element, fdef["type"])} AS "{element}"')
        columns += [f"c.{col}" for col in SYSTEM_COLUMNS]
        out.append(f'DROP VIEW IF EXISTS sn."{name}" CASCADE;')
        out.append(
            f'CREATE VIEW sn."{name}" AS SELECT {", ".join(columns)} FROM cmdb.cmdb_ci c '
            f"WHERE c.sys_class_name = ANY ({_arr(classes)});"
        )
        out.append(f'COMMENT ON VIEW sn."{name}" IS {_q(c["label_tr"] + " — " + c["label"])};')

    out.append("\n-- Yeni görünümlere okuma yetkisi (0003_security.sql rolleri varsa)")
    out.append("DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evren_readonly') THEN "
               "GRANT SELECT ON ALL TABLES IN SCHEMA sn TO evren_readonly, evren_auditor_ro; END IF; END $$;")
    out.append("COMMIT;")
    return "\n".join(out) + "\n"


_CORE_TYPES = {
    "operational_status": "integer", "install_status": "integer", "first_discovered": "glide_date_time",
    "last_discovered": "glide_date_time", "install_date": "glide_date_time", "warranty_expiration": "glide_date",
}


def metadata_field_resolver(md: Metadata, class_name: str, alias: str = "c"):
    """Encoded query derleyicisi için sözlük tabanlı alan çözümleyici.

    Yalnızca sınıfın (ve atalarının) sözlükte tanımlı alanları ile sistem alanları
    sorgulanabilir; bilinmeyen alan ``QueryError`` üretir (alan adı SQL'e asla ham girmez).
    """
    from .encoded_query import FieldRef, QueryError

    fields = md.fields_of(class_name)

    def resolve(name: str) -> FieldRef:
        if name == "sys_class_name":
            return FieldRef(f"{alias}.sys_class_name", "sys_class_name")
        if name == "sys_id":
            return FieldRef(f"{alias}.sys_id", "string")
        if name in ("sys_created_on", "sys_updated_on"):
            return FieldRef(f"{alias}.{name}", "glide_date_time")
        if name in ("sys_created_by", "sys_updated_by"):
            return FieldRef(f"{alias}.{name}", "string")
        if name == "sys_mod_count":
            return FieldRef(f"{alias}.sys_mod_count", "integer")
        fdef = fields.get(name)
        if fdef is None:
            raise QueryError(f"{class_name} sınıfında '{name}' alanı yok")
        if fdef.get("storage", "jsonb") == "column":
            return FieldRef(f'{alias}."{name}"', _CORE_TYPES.get(name, fdef["type"]))
        return FieldRef(jsonb_expression(name, fdef["type"], alias), fdef["type"])

    return resolve


def _json(value: Mapping[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
