"""Uzlaştırıcı deposu — golden record'un TEK yazarı (F0-10, CLAUDE.md kural 1).

Her kaynak kaydı kendi veritabanı işleminde işlenir:

1. **Değişim süzgeci:** normalleştirilmiş içerik özeti aynıysa yalnızca ``last_seen`` güncellenir.
2. **Kimlik:** ``IdentityResolver`` + PostgreSQL aday dizini (``ingest.identity_key``).
3. **Karar:** bağ (``identity_link``), yeni CI (yalnızca ``may_create_ci``), eşleştirme kuyruğu
   (inceleme, yetersiz kimlik, eşleşmeyen gölge kaynak).
4. **Uzlaştırma:** CI'ın tüm bağlı kaynak kayıtları ``Reconciler``'dan geçer; değişen öznitelikler
   ``cmdb_ci``, ``ci_reconciliation_state``, ``audit.ci_change`` ve ``events.outbox``'a aynı işlemde yazılır.

Çalışma sonunda devre kesici değerlendirilir; yalnızca ``OK`` ise görülmeyen kayıtlar ``missing`` olur.
CI hiçbir zaman silinmez; eskime ayrı bir kural adımıdır (F1-06).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from evren_core.circuit_breaker import BreakerState, BreakerVerdict, evaluate_run
from evren_core.envelope import IngestRecord
from evren_core.identity import IdentityResolver, Outcome, Resolution, RuleSet, extract_keys
from evren_core.identity.adapters import source_record_from_ingest
from evren_core.identity.index import CandidateProfile
from evren_core.metadata import CORE_COLUMNS, Metadata
from evren_core.reconcile import Reconciler, SourceValues

from .pg_identity import PostgresCandidateIndex, PostgresLinkStore

__all__ = ["ApplyResult", "ReconcilerStore"]

_SYSID = re.compile(r"^[0-9a-f]{32}$")
REFERENCE_COLUMNS = frozenset({
    "manufacturer", "model_id", "location", "company", "department", "support_group", "assignment_group",
    "change_control", "managed_by", "owned_by", "assigned_to",
})
SMALLINT_COLUMNS = frozenset({"operational_status", "install_status"})
#: Kaynak tarafından yazılamaz; uzlaştırıcı türetir veya özel işlemle değişir.
SYSTEM_MANAGED = frozenset({"discovery_source", "first_discovered", "last_discovered", "duplicate_of"})
DISCOVERY_ROLES = frozenset({"Discovery", "Virtualization", "Network"})
_JSON_TYPES: dict[str, Callable[[Any], Any]] = {
    "integer": int, "decimal": float, "boolean": bool, "string": str, "reference": str,
    "glide_date_time": str, "glide_date": str,
}


@dataclass
class ApplyResult:
    """Bir kaynak kaydının işlenme sonucu (servis logu, metrik ve testler için)."""

    outcome: str  # unchanged | linked | rule_match | score_match | new | review | insufficient | unmatched_shadow …
    ci_sys_id: str | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


@dataclass
class _Run:
    data_source: str
    mode: str
    started_at: datetime
    records: int = 0
    changed: int = 0   # önceden var olan ve içeriği değişen kayıt (eşleme hatası belirtisi)
    new: int = 0       # ilk kez görülen kayıt (ilk yükleme veya yeni kaynakta doğal olarak yüksek)


def content_hash(record: IngestRecord) -> str:
    doc = record.model_dump(mode="json", include={"class_hint", "attributes", "lookup", "external_ids", "relations",
                                                  "deleted"})
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class ReconcilerStore:
    def __init__(self, conn: psycopg.Connection[Any], metadata: Metadata, *, actor: str = "evren_reconciler",
                 clock: Callable[[], datetime] | None = None) -> None:
        self.conn = conn
        self.md = metadata
        self.registry = metadata.sources
        self.hierarchy = metadata.hierarchy
        self.rules = RuleSet.from_metadata(metadata.identification, metadata.hierarchy)
        self.reconciler = Reconciler.from_metadata(metadata.hierarchy, metadata.reconciliation, self.registry)
        self.actor = actor
        self.clock = clock or (lambda: datetime.now(UTC))
        self.runs: dict[UUID, _Run] = {}

    # ------------------------------------------------------------------ çalışma yaşam döngüsü
    def start_run(self, run_id: UUID, data_source: str, *, mode: str = "snapshot",
                  started_at: datetime | None = None) -> UUID:
        self.registry.source(data_source)  # tanımsız kaynak açık hata verir
        started = started_at or self.clock()
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("INSERT INTO ingest.source_run (run_id, data_source, mode, started_at) VALUES (%s, %s, %s, %s)",
                        (run_id, data_source, mode, started))
        self.runs[run_id] = _Run(data_source, mode, started)
        return run_id

    def finish_run(self, run_id: UUID, *, completed: bool = True, error_count: int = 0,
                   finished_at: datetime | None = None) -> BreakerVerdict:
        run = self.runs.pop(run_id)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """SELECT record_count FROM ingest.source_run
                    WHERE data_source = %s AND run_id <> %s AND completed AND breaker_state IN ('ok', 'warn')
                    ORDER BY started_at DESC LIMIT 10""", (run.data_source, run_id))
            baseline = [r[0] for r in cur.fetchall()]
            if self.registry.roles[self.registry.role_of(run.data_source)].kind in ("manual", "platform"):
                # Elle giriş ve platform kuralları toplu toplama değildir; devre kesici uygulanmaz.
                verdict = BreakerVerdict(BreakerState.OK, ("Elle giriş/platform: devre kesici uygulanmaz",), None)
            else:
                verdict = evaluate_run(run.records, baseline, changed_count=run.changed, error_count=error_count,
                                       completed=completed)
            cur.execute(
                """UPDATE ingest.source_run SET finished_at = %s, completed = %s, record_count = %s, changed_count = %s,
                          error_count = %s, breaker_state = %s, breaker_reasons = %s, baseline = %s,
                          notes = %s
                    WHERE run_id = %s""",
                (finished_at or self.clock(), completed, run.records, run.changed, error_count, verdict.state.value,
                 list(verdict.reasons), verdict.baseline, f"yeni kayıt: {run.new}" if run.new else None, run_id))
            self._resolve_relations(cur, run_id, run.data_source)
            if verdict.allows_destructive and run.mode == "snapshot":
                # Görülmeyen kayıtlar yalnızca işaretlenir; değerleri eskime kuralı karar verene kadar korunur.
                cur.execute(
                    """UPDATE ingest.source_record SET status = 'missing'
                        WHERE data_source = %s AND status = 'active' AND last_run IS DISTINCT FROM %s""",
                    (run.data_source, run_id))
            elif not verdict.allows_destructive:
                cur.execute(
                    """INSERT INTO insight.anomaly (kind, severity, data_source, details, explanation)
                       VALUES ('source_volume', %s, %s, %s, %s)""",
                    ("high" if verdict.state.value == "open" else "medium", run.data_source,
                     Jsonb({"run_id": str(run_id), "state": verdict.state.value, "reasons": list(verdict.reasons),
                            "records": run.records, "new": run.new, "changed": run.changed,
                            "baseline": verdict.baseline}),
                     "Devre kesici: " + "; ".join(verdict.reasons)))
        return verdict

    # ------------------------------------------------------------------ kayıt işleme
    def apply(self, record: IngestRecord) -> ApplyResult:
        """Kaynak kaydını kimlik çözümlemesi ve uzlaştırmadan geçirir (tek işlem)."""
        return self._process(record, attach_to=None, method=None)

    def import_record(self, record: IngestRecord, ci_sys_id: str) -> ApplyResult:
        """İlk yükleme: CI verilen sys_id ile açılır (ServiceNow sys_id'si korunur), kimlik çözümlemesi yapılmaz."""
        return self._process(record, attach_to=ci_sys_id, method="import")

    def attach_manual(self, record: IngestRecord, ci_sys_id: str) -> ApplyResult:
        """Elle giriş: Manual kaynak kaydı belirtilen CI'a bağlanır ve uzlaştırmadan geçer (CLAUDE.md kural 1)."""
        if self.registry.role_of(record.data_source) != "Manual":
            raise ValueError("Yalnızca Manual rolündeki kaynak kayıtları elle bağlanabilir")
        return self._process(record, attach_to=ci_sys_id, method="manual")

    def _process(self, record: IngestRecord, *, attach_to: str | None, method: str | None) -> ApplyResult:
        src = self.registry.source(record.data_source)
        run = self.runs.get(record.run_id)
        digest = content_hash(record)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"{record.data_source}:{record.source_key}",))
            cur.execute("SELECT content_hash FROM ingest.source_record WHERE data_source = %s AND source_key = %s",
                        (record.data_source, record.source_key))
            row = cur.fetchone()
            links = PostgresLinkStore(cur)
            linked = links.get(record.data_source, record.source_key)
            if run:
                run.records += 1
            if row and row[0] == digest and (linked or attach_to is None):
                cur.execute("""UPDATE ingest.source_record SET last_seen = %s, last_run = %s, status = 'active'
                                WHERE data_source = %s AND source_key = %s""",
                            (record.observed_at, record.run_id, record.data_source, record.source_key))
                if linked:
                    self._touch_discovered(cur, linked, src.name, record.observed_at)
                return ApplyResult("unchanged", linked)
            if run:
                if row is None:
                    run.new += 1
                else:
                    run.changed += 1
            self._upsert_source_record(cur, record, digest)
            index = PostgresCandidateIndex(cur)
            resolver = IdentityResolver(self.rules, index, links)
            sr = source_record_from_ingest(record)
            keys = extract_keys(sr)

            if attach_to is not None:
                created = self._ensure_ci(cur, attach_to, record) if method == "import" else False
                self._attach(cur, index, resolver, record, keys, attach_to, method=method, rule="import" if created
                             else method, resolution=None)
                changes = self._reconcile(cur, attach_to, record.run_id)
                return ApplyResult("linked", attach_to, changes, "İlk yükleme" if created else "Elle bağlandı")

            res = resolver.resolve(sr, keys)
            if res.outcome in (Outcome.LINKED, Outcome.RULE_MATCH, Outcome.SCORE_MATCH):
                ci = res.ci_sys_id
                assert ci is not None
                if res.outcome is not Outcome.LINKED:
                    self._attach(cur, index, resolver, record, keys, ci,
                                 method="rule" if res.outcome is Outcome.RULE_MATCH else "score", rule=res.rule,
                                 resolution=res)
                else:
                    index.upsert(CandidateProfile(ci, self._profile_class(index, ci, record.class_hint),
                                                  resolver.family(record.class_hint), keys))
                self._close_open_candidates(cur, record, "Kimlik otomatik çözüldü")
                return ApplyResult(res.outcome.value, ci, self._reconcile(cur, ci, record.run_id), res.summary_tr)

            if res.outcome is Outcome.NEW:
                if not self.registry.may_create_ci(record.data_source):
                    kind = "unmatched_shadow" if src.lifecycle == "shadow" else "unmatched_retiring"
                    self._enqueue(cur, record, res, kind, priority=5,
                                  note=f"{record.data_source} ({src.lifecycle}) yeni CI açamaz; eşleşmeyen kayıt kapsam "
                                       "farkı olarak raporlanır")
                    return ApplyResult(kind, None, [], res.summary_tr)
                ci = self._create_ci(cur, record)
                self._attach(cur, index, resolver, record, keys, ci, method="rule", rule="yeni CI", resolution=res)
                self._close_open_candidates(cur, record, "Yeni CI açıldı")
                return ApplyResult("new", ci, self._reconcile(cur, ci, record.run_id), res.summary_tr)

            kind = "review" if res.outcome is Outcome.REVIEW else "insufficient"
            self._enqueue(cur, record, res, kind, priority=2 if kind == "review" else 4, note=None)
            return ApplyResult(kind, res.ci_sys_id, [], res.summary_tr)

    # ------------------------------------------------------------------ yardımcılar: kaynak kaydı, bağ, kuyruk
    def _upsert_source_record(self, cur: psycopg.Cursor[Any], record: IngestRecord, digest: str) -> None:
        dumped = record.model_dump(mode="json")
        cur.execute(
            """INSERT INTO ingest.source_record (data_source, source_key, class_hint, attributes, lookup, external_ids,
                                                 relations, content_hash, raw_ref, status, first_seen, last_seen,
                                                 last_changed, last_run)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
               ON CONFLICT (data_source, source_key) DO UPDATE SET class_hint = EXCLUDED.class_hint,
                 attributes = EXCLUDED.attributes, lookup = EXCLUDED.lookup, external_ids = EXCLUDED.external_ids,
                 relations = EXCLUDED.relations, content_hash = EXCLUDED.content_hash, raw_ref = EXCLUDED.raw_ref,
                 status = EXCLUDED.status, last_seen = EXCLUDED.last_seen, last_changed = now(),
                 last_run = EXCLUDED.last_run""",
            (record.data_source, record.source_key, record.class_hint, Jsonb(dumped["attributes"]),
             Jsonb(dumped["lookup"]), Jsonb(dumped["external_ids"]), Jsonb(dumped["relations"]), digest,
             record.raw_ref, "deleted" if record.deleted else "active", record.observed_at, record.observed_at,
             record.run_id))

    def _profile_class(self, index: PostgresCandidateIndex, ci: str, class_hint: str) -> str:
        try:
            current = index.profile(ci).class_name
        except KeyError:
            return class_hint
        h = self.hierarchy
        if class_hint in h and current in h and h.compatible(class_hint, current):
            return h.more_specific(class_hint, current)
        return current

    def _attach(self, cur: psycopg.Cursor[Any], index: PostgresCandidateIndex, resolver: IdentityResolver,
                record: IngestRecord, keys: Any, ci: str, *, method: str | None, rule: str | None,
                resolution: Resolution | None) -> None:
        cls = self._profile_class(index, ci, record.class_hint)
        index.upsert(CandidateProfile(ci, cls, resolver.family(cls), keys))
        cur.execute(
            """UPDATE ingest.identity_link SET active = false, ended_at = now(), end_reason = 'yeniden bağlandı'
                WHERE data_source = %s AND source_key = %s AND active AND ci_sys_id <> %s""",
            (record.data_source, record.source_key, ci))
        features = [{"code": f.code, "weight": f.weight, "detail": f.detail}
                    for f in (resolution.features if resolution else ())]
        cur.execute(
            """INSERT INTO ingest.identity_link (data_source, source_key, ci_sys_id, method, rule_ref, probability,
                                                features, decided_by)
               SELECT %s, %s, %s, %s, %s, %s, %s, %s
                WHERE NOT EXISTS (SELECT 1 FROM ingest.identity_link
                                   WHERE data_source = %s AND source_key = %s AND active)""",
            (record.data_source, record.source_key, ci, method, rule,
             resolution.probability if resolution else None, Jsonb(features), self.actor,
             record.data_source, record.source_key))

    def _enqueue(self, cur: psycopg.Cursor[Any], record: IngestRecord, res: Resolution, kind: str, *, priority: int,
                 note: str | None) -> None:
        reason = res.summary_tr if note is None else f"{note}. {res.summary_tr}"
        cur.execute(
            """INSERT INTO ingest.match_candidate (data_source, source_key, reason, candidate_ci, probability, features,
                                                  candidates, priority, kind)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (data_source, source_key) WHERE status = 'open' DO UPDATE SET reason = EXCLUDED.reason,
                 candidate_ci = EXCLUDED.candidate_ci, probability = EXCLUDED.probability, features = EXCLUDED.features,
                 candidates = EXCLUDED.candidates, priority = EXCLUDED.priority, kind = EXCLUDED.kind""",
            (record.data_source, record.source_key, reason[:2000], res.ci_sys_id, res.probability,
             Jsonb([{"code": f.code, "weight": f.weight, "detail": f.detail} for f in res.features]),
             Jsonb([{"sys_id": c, "probability": round(p, 5)} for c, p in res.candidates]), priority, kind))

    def _close_open_candidates(self, cur: psycopg.Cursor[Any], record: IngestRecord, note: str) -> None:
        cur.execute(
            """UPDATE ingest.match_candidate SET status = 'expired', decided_by = %s, decided_at = now(),
                      decision_note = %s
                WHERE data_source = %s AND source_key = %s AND status = 'open'""",
            (self.actor, note, record.data_source, record.source_key))

    # ------------------------------------------------------------------ golden record
    def _create_ci(self, cur: psycopg.Cursor[Any], record: IngestRecord) -> str:
        cur.execute(
            """INSERT INTO cmdb.cmdb_ci (sys_class_name, discovery_source, first_discovered, sys_created_by,
                                         sys_updated_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING sys_id""",
            (record.class_hint, record.data_source, record.observed_at, self.actor, self.actor))
        sys_id = cur.fetchone()[0]  # type: ignore[index]
        self._outbox(cur, sys_id, "cmdb.ci.created.v1", {"sys_id": sys_id, "class": record.class_hint,
                                                         "data_source": record.data_source})
        return sys_id

    def _ensure_ci(self, cur: psycopg.Cursor[Any], sys_id: str, record: IngestRecord) -> bool:
        if not _SYSID.match(sys_id):
            raise ValueError("sys_id 32 haneli küçük harf onaltılık olmalı")
        cur.execute(
            """INSERT INTO cmdb.cmdb_ci (sys_id, sys_class_name, discovery_source, first_discovered, sys_created_by,
                                         sys_updated_by)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (sys_id) DO NOTHING RETURNING sys_id""",
            (sys_id, record.class_hint, record.data_source, record.observed_at, self.actor, self.actor))
        created = cur.fetchone() is not None
        if created:
            self._outbox(cur, sys_id, "cmdb.ci.created.v1", {"sys_id": sys_id, "class": record.class_hint,
                                                             "data_source": record.data_source, "import": True})
        return created

    def _touch_discovered(self, cur: psycopg.Cursor[Any], ci: str, data_source: str, observed: datetime) -> None:
        if self.registry.role_of(data_source) in DISCOVERY_ROLES:
            cur.execute("""UPDATE cmdb.cmdb_ci SET last_discovered = %s
                            WHERE sys_id = %s AND (last_discovered IS NULL OR last_discovered < %s)""",
                        (observed, ci, observed))

    def _coerce(self, attribute: str, value: Any, fdef: dict[str, Any] | None) -> tuple[str, Any] | None:
        """Kararı fiziksel kolona veya sözlükteki JSONB alanına yerleştirir; yerleşemiyorsa ``None``."""
        if attribute in SYSTEM_MANAGED:
            return None
        if attribute in CORE_COLUMNS:
            if attribute in REFERENCE_COLUMNS:
                return ("column", value) if isinstance(value, str) and _SYSID.match(value) else None
            if attribute in SMALLINT_COLUMNS:
                try:
                    return "column", int(value)
                except (TypeError, ValueError):
                    return None
            return "column", value if isinstance(value, str) else str(value)
        if fdef is None or fdef.get("storage", "jsonb") != "jsonb":
            return None
        cast = _JSON_TYPES.get(fdef.get("type", "string"), str)
        try:
            return "jsonb", cast(value)
        except (TypeError, ValueError):
            return None

    def _reconcile(self, cur: psycopg.Cursor[Any], ci: str, run_id: UUID | None) -> list[dict[str, Any]]:
        cur.execute(
            """SELECT sr.data_source, sr.attributes, sr.last_seen, sr.content_hash, sr.first_seen
                 FROM ingest.identity_link l
                 JOIN ingest.source_record sr USING (data_source, source_key)
                WHERE l.ci_sys_id = %s AND l.active AND sr.status IN ('active', 'missing')
                ORDER BY sr.data_source, sr.source_key""", (ci,))
        rows = cur.fetchall()
        input_hash = hashlib.sha256(
            json.dumps([[r[0], r[3], r[2].isoformat()] for r in rows]).encode()).hexdigest()
        with self.conn.cursor(row_factory=dict_row) as dcur:
            dcur.execute("SELECT * FROM cmdb.cmdb_ci WHERE sys_id = %s FOR UPDATE", (ci,))
            current = dcur.fetchone()
            dcur.execute("SELECT input_hash FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s", (ci,))
            state = dcur.fetchone()
        if current is None:
            raise LookupError(f"CI yok: {ci}")
        if state and state["input_hash"] == input_hash:
            return []
        cur.execute("SELECT class_name FROM ingest.identity_profile WHERE ci_sys_id = %s", (ci,))
        prof = cur.fetchone()
        cls = current["sys_class_name"]
        changes: list[dict[str, Any]] = []
        if prof and prof[0] != cls and self.hierarchy.compatible(prof[0], cls):
            new_cls = self.hierarchy.more_specific(prof[0], cls)
            if new_cls != cls:
                changes.append({"attribute": "sys_class_name", "old": cls, "new": new_cls, "source": None,
                                "reason": "reclassification"})
                cls = new_cls

        inputs = [SourceValues(r[0], r[1], r[2]) for r in rows]
        report = self.reconciler.reconcile_report(cls, inputs, self.clock())
        fields = self.md.fields_of(cls)
        columns: dict[str, Any] = {}
        attrs = dict(current["class_attributes"] or {})
        winners: dict[str, Any] = {}
        dropped: dict[str, Any] = {}
        for name, d in report.decisions.items():
            placed = self._coerce(name, d.value, fields.get(name))
            if placed is None:
                dropped[name] = {"value": _jsonable(d.value), "source": d.data_source,
                                 "reason": "Sözlükte bu sınıf için alan yok veya tip/biçim uyumsuz"}
                continue
            where, value = placed
            winners[name] = {"source": d.data_source, "role": d.role, "reason": d.reason,
                             "observed_at": d.observed_at.isoformat(), "overridden": [list(o) for o in d.overridden],
                             "unauthorized": list(d.unauthorized), "shadow": [list(s) for s in d.shadow]}
            old = current[name] if where == "column" else attrs.get(name)
            if old != value:
                changes.append({"attribute": name, "old": _jsonable(old), "new": value, "source": d.data_source,
                                "reason": "reconciliation"})
                if where == "column":
                    columns[name] = value
                else:
                    attrs[name] = value
        if cls != current["sys_class_name"]:
            columns["sys_class_name"] = cls
        discovered = [r[2] for r in rows if self.registry.role_of(r[0]) in DISCOVERY_ROLES]
        if discovered and (current["last_discovered"] is None or max(discovered) > current["last_discovered"]):
            columns["last_discovered"] = max(discovered)
        if current["first_discovered"] is None and rows:
            columns["first_discovered"] = min(r[4] for r in rows)
        if attrs != (current["class_attributes"] or {}):
            columns["class_attributes"] = Jsonb(attrs)
        if columns:
            columns["sys_updated_by"] = self.actor
            # Kolon adları sabit kümelerden (CORE_COLUMNS ve sistem alanları) gelir; yine de tanımlayıcı olarak kaçışlanır.
            query = sql.SQL("UPDATE cmdb.cmdb_ci SET {} WHERE sys_id = %s").format(
                sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in columns))
            cur.execute(query, (*columns.values(), ci))
        for ch in changes:
            cur.execute(
                """INSERT INTO audit.ci_change (ci_sys_id, sys_class_name, attribute, old_value, new_value, data_source,
                                               actor, reason, correlation)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (ci, cls, ch["attribute"], Jsonb(ch["old"]), Jsonb(ch["new"]), ch["source"], self.actor, ch["reason"],
                 Jsonb({"run_id": str(run_id) if run_id else None})))
        conflicts = {name: [list(a) for a in attempts] for name, attempts in report.rejected.items()}
        if dropped:
            conflicts["_dropped"] = dropped
        cur.execute(
            """INSERT INTO cmdb.ci_reconciliation_state (ci_sys_id, winners, conflicts, input_hash, computed_at)
               VALUES (%s, %s, %s, %s, now())
               ON CONFLICT (ci_sys_id) DO UPDATE SET winners = EXCLUDED.winners, conflicts = EXCLUDED.conflicts,
                 input_hash = EXCLUDED.input_hash, computed_at = now()""",
            (ci, Jsonb(winners), Jsonb(conflicts), input_hash))
        if changes:
            self._outbox(cur, ci, "cmdb.ci.changed.v1", {
                "sys_id": ci, "class": cls, "run_id": str(run_id) if run_id else None,
                "changes": [{k: ch[k] for k in ("attribute", "old", "new", "source")} for ch in changes]})
        return changes

    def _outbox(self, cur: psycopg.Cursor[Any], ci: str, event_type: str, payload: dict[str, Any]) -> None:
        cur.execute("INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload) "
                    "VALUES ('cmdb_ci', %s, %s, %s)", (ci, event_type, Jsonb(payload)))

    # ------------------------------------------------------------------ ilişkiler
    def _resolve_relations(self, cur: psycopg.Cursor[Any], run_id: UUID, data_source: str) -> None:
        """Çalışmadaki kayıtların ilişkilerini, iki uç da CI'a bağlıysa ``cmdb_rel_ci``'a yazar."""
        cur.execute(
            """SELECT sr.source_key, sr.relations, l.ci_sys_id, sr.last_seen
                 FROM ingest.source_record sr
                 JOIN ingest.identity_link l ON l.data_source = sr.data_source AND l.source_key = sr.source_key AND l.active
                WHERE sr.data_source = %s AND sr.last_run = %s AND jsonb_array_length(sr.relations) > 0""",
            (data_source, run_id))
        for _key, relations, ci, seen in cur.fetchall():
            for rel in relations:
                target = PostgresLinkStore(cur).get(rel.get("target_data_source") or data_source,
                                                    rel["target_source_key"])
                if not target or target == ci:
                    continue
                parent, child = (ci, target) if rel["direction"] == "parent" else (target, ci)
                cur.execute(
                    """INSERT INTO cmdb.cmdb_rel_ci (parent, child, type, discovery_source, first_discovered,
                                                    last_discovered)
                       SELECT %s, %s, t.sys_id, %s, %s, %s FROM cmdb.cmdb_rel_type t WHERE t.name = %s
                       ON CONFLICT (parent, child, type) DO UPDATE SET last_discovered = EXCLUDED.last_discovered""",
                    (parent, child, data_source, seen, seen, rel["type"]))

    # ------------------------------------------------------------------ toplu yardımcı
    def apply_all(self, records: Iterable[IngestRecord]) -> list[ApplyResult]:
        return [self.apply(r) for r in records]
