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

Yetkisiz değişiklik (ADR-0025): gözlemci rolündeki kaynağın kendi kaydında izlenen öznitelik değişip golden record'u
değiştirdiğinde ``insight.config_change``'e gözlem yazılır; ``evaluate_config_changes`` bekleme süresinden sonra onaylı
değişiklik penceresine göre karar verir ve gerekirse ``unauthorized_change`` anomalisi açar.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from evren_core.attestation import ATTESTED, NOT_REVIEWED, REASONS_TR, REJECTED
from evren_core.change_control import LOCAL_TZ, ChangeRecord, evaluate
from evren_core.circuit_breaker import BreakerState, BreakerVerdict, evaluate_run
from evren_core.envelope import IngestRecord
from evren_core.identity import IdentityResolver, Outcome, Resolution, RuleSet, extract_keys
from evren_core.identity.adapters import source_record_from_ingest
from evren_core.identity.index import CandidateProfile
from evren_core.metadata import CORE_COLUMNS, Metadata
from evren_core.reconcile import Reconciler, SourceValues
from evren_core.reference import ROLE_TABLES, ReferenceDataError, coerce_reference, is_reference

from .pg_identity import PostgresCandidateIndex, PostgresLinkStore, keys_from_json

log = logging.getLogger("evren.reconciler")

__all__ = ["ApplyResult", "ReconcilerStore"]

_SYSID = re.compile(r"^[0-9a-f]{32}$")
REFERENCE_COLUMNS = frozenset({
    "manufacturer", "model_id", "location", "company", "department", "support_group", "assignment_group",
    "change_control", "managed_by", "owned_by", "assigned_to", "cost_center", "attested_by",
})
SMALLINT_COLUMNS = frozenset({"operational_status", "install_status"})
BOOLEAN_COLUMNS = frozenset({"attested"})
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


@dataclass(frozen=True)
class Observation:
    """Kaynağın kendi kaydında gördüğü öznitelik değişimi (ADR-0025): eski değeri son ve yeni değeri ilk gördüğü an."""

    data_source: str
    observed_from: datetime | None
    observed_at: datetime
    attributes: frozenset[str]


@dataclass(frozen=True)
class RunClosure:
    """Alım geçidinden açılan bir çalışmanın kapanışı: ``completed`` | ``timed_out`` | ``abandoned``."""

    run_id: UUID
    data_source: str
    reason: str
    processed: int
    reported: int | None
    verdict: BreakerVerdict


def _record_from_row(row: dict[str, Any]) -> IngestRecord:
    """``ingest.source_record`` satırından (sözlük) kaynak kaydını yeniden kurar."""
    return IngestRecord(
        data_source=row["data_source"], run_id=row["last_run"] or UUID(int=0), source_key=row["source_key"],
        class_hint=row["class_hint"], attributes=row["attributes"], lookup=row["lookup"],
        external_ids=row["external_ids"], relations=row["relations"], observed_at=row["last_seen"])


def _manual_outcome(name: str, value: Any, winners: dict[str, Any], conflicts: dict[str, Any]) -> dict[str, Any]:
    """Elle girilen değerin uzlaştırmadaki sonucu (arayüzde alan başına gösterilir)."""
    if value is None:
        w = winners.get(name)
        return {"status": "cleared", "source": w.get("source") if w else None}
    if name in (conflicts.get("_dropped") or {}):
        return {"status": "invalid", "source": None}
    if any(src == "Manual" for src, _ in conflicts.get(name, [])):
        return {"status": "unauthorized", "source": None}
    w = winners.get(name) or {}
    if w.get("source") == "Manual":
        return {"status": "applied", "source": "Manual"}
    return {"status": "overridden", "source": w.get("source")}


def _manual_note(outcome: dict[str, dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for o in outcome.values():
        counts[o["status"]] = counts.get(o["status"], 0) + 1
    parts = [f"{n} alan {label}" for key, label in (
        ("applied", "uygulandı"), ("cleared", "için elle değer kaldırıldı"),
        ("overridden", "daha öncelikli kaynakta; elle değer yedek olarak saklandı"),
        ("unauthorized", "elle yazılamaz"), ("invalid", "geçersiz")) if (n := counts.get(key))]
    return "; ".join(parts) or "Değişiklik yok"


def content_hash(record: IngestRecord) -> str:
    doc = record.model_dump(mode="json", include={"class_hint", "attributes", "lookup", "external_ids", "relations",
                                                  "deleted"})
    if record.posture is not None:  # duruşu olmayan kayıtların özeti değişmez (ADR-0023)
        doc["posture"] = record.posture.model_dump(mode="json")
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
        self.policy = metadata.change_policy
        self.attestation = metadata.attestation_policy

    # ------------------------------------------------------------------ çalışma yaşam döngüsü
    def start_run(self, run_id: UUID, data_source: str, *, mode: str = "snapshot",
                  started_at: datetime | None = None) -> UUID:
        self.registry.source(data_source)  # tanımsız kaynak açık hata verir
        started = started_at or self.clock()
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("INSERT INTO ingest.source_run (run_id, data_source, mode, started_at) VALUES (%s, %s, %s, %s)",
                        (run_id, data_source, mode, started))
        return run_id

    def run_counts(self, cur: psycopg.Cursor[Any], run_id: UUID, data_source: str) -> tuple[int, int, int]:
        """(işlenen, yeni, değişen) — kaynak kayıtlarından; aynı kaydın yeniden teslimi iki kez sayılmaz (ADR-0017)."""
        cur.execute("""SELECT count(*), count(*) FILTER (WHERE first_run = %(r)s),
                              count(*) FILTER (WHERE changed_run = %(r)s AND first_run IS DISTINCT FROM %(r)s)
                         FROM ingest.source_record WHERE data_source = %(ds)s AND last_run = %(r)s""",
                    {"r": run_id, "ds": data_source})
        return cur.fetchone()  # type: ignore[return-value]

    def finish_run(self, run_id: UUID, *, completed: bool = True, error_count: int = 0,
                   finished_at: datetime | None = None, note: str | None = None) -> BreakerVerdict:
        """Çalışmayı kapatır: sayaçlar veritabanından, devre kesici kararı, ilişkiler, görülmeyenlerin işaretlenmesi."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT data_source, mode, finished_at FROM ingest.source_run WHERE run_id = %s FOR UPDATE",
                        (run_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"Çalışma bulunamadı: {run_id}")
            data_source, mode, already = row
            if already is not None:
                raise ValueError(f"Çalışma zaten kapatılmış: {run_id}")
            records, new, changed = self.run_counts(cur, run_id, data_source)
            cur.execute(
                """SELECT record_count FROM ingest.source_run
                    WHERE data_source = %s AND run_id <> %s AND completed AND breaker_state IN ('ok', 'warn')
                      AND mode = 'snapshot'
                    ORDER BY started_at DESC LIMIT 10""", (data_source, run_id))
            baseline = [r[0] for r in cur.fetchall()]
            if self.registry.roles[self.registry.role_of(data_source)].kind in ("manual", "platform"):
                # Elle giriş ve platform kuralları toplu toplama değildir; devre kesici uygulanmaz.
                verdict = BreakerVerdict(BreakerState.OK, ("Elle giriş/platform: devre kesici uygulanmaz",), None)
            else:
                verdict = evaluate_run(records, baseline, changed_count=changed, error_count=error_count,
                                       completed=completed, mode=mode)
            notes = "; ".join(n for n in (f"yeni kayıt: {new}" if new else None, note) if n) or None
            cur.execute(
                """UPDATE ingest.source_run SET finished_at = %s, completed = %s, record_count = %s, changed_count = %s,
                          new_count = %s, error_count = %s, breaker_state = %s, breaker_reasons = %s, baseline = %s,
                          notes = %s
                    WHERE run_id = %s""",
                (finished_at or self.clock(), completed, records, changed, new, error_count, verdict.state.value,
                 list(verdict.reasons), verdict.baseline, notes, run_id))
            self._resolve_relations(cur, run_id, data_source)
            if verdict.allows_destructive and mode == "snapshot":
                # Görülmeyen kayıtlar yalnızca işaretlenir; değerleri eskime kuralı karar verene kadar korunur.
                cur.execute(
                    """UPDATE ingest.source_record SET status = 'missing'
                        WHERE data_source = %s AND status = 'active' AND last_run IS DISTINCT FROM %s""",
                    (data_source, run_id))
            elif not verdict.allows_destructive:
                cur.execute(
                    """INSERT INTO insight.anomaly (kind, severity, data_source, details, explanation)
                       VALUES ('source_volume', %s, %s, %s, %s)""",
                    ("high" if verdict.state.value == "open" else "medium", data_source,
                     Jsonb({"run_id": str(run_id), "state": verdict.state.value, "reasons": list(verdict.reasons),
                            "records": records, "new": new, "changed": changed,
                            "baseline": verdict.baseline}),
                     "Devre kesici: " + "; ".join(verdict.reasons)))
        return verdict

    def finalize_ready_runs(self, *, grace: timedelta = timedelta(minutes=15),
                            abandon_after: timedelta = timedelta(hours=24), limit: int = 50) -> list[RunClosure]:
        """Alım geçidinden açılan çalışmaları kapatır (ADR-0017).

        * Kapanış istendi ve bildirilen sayıda kayıt işlendi → bildirilen tamamlanma durumuyla kapanır.
        * Kapanış istendi ama ``grace`` içinde kayıtlar gelmedi → eksik (``completed=false``): devre kesici açılır,
          yıkıcı işlem yapılmaz.
        * Kapanış hiç istenmedi ve çalışma ``abandon_after``'dan eski → terk edilmiş, eksik kapanır.
        Birden çok çalışan güvenle çalışır (``FOR UPDATE SKIP LOCKED``).
        """
        now = self.clock()
        closed: list[RunClosure] = []
        with self.conn.cursor() as cur:
            cur.execute("""SELECT run_id FROM ingest.source_run
                            WHERE finished_at IS NULL AND opened_by IS NOT NULL
                              AND (close_requested_at IS NOT NULL OR started_at < %s)
                            ORDER BY coalesce(close_requested_at, started_at) LIMIT %s""",
                        (now - abandon_after, limit))
            candidates = [r[0] for r in cur.fetchall()]
        for run_id in candidates:
            with self.conn.transaction(), self.conn.cursor() as cur:
                cur.execute("""SELECT data_source, started_at, close_requested_at, reported_count, reported_completed,
                                      reported_error_count
                                 FROM ingest.source_run WHERE run_id = %s AND finished_at IS NULL
                                  FOR UPDATE SKIP LOCKED""", (run_id,))
                row = cur.fetchone()
                if row is None:
                    continue
                ds, started, close_at, reported, reported_ok, errors = row
                processed = self.run_counts(cur, run_id, ds)[0]
                if close_at is not None and processed >= (reported or 0):
                    verdict = self.finish_run(run_id, completed=bool(reported_ok), error_count=errors or 0)
                    closed.append(RunClosure(run_id, ds, "completed", processed, reported, verdict))
                elif close_at is not None and now - close_at > grace:
                    note = f"Bildirilen {reported} kaydın {processed} tanesi işlendi; bekleme süresi doldu"
                    verdict = self.finish_run(run_id, completed=False, error_count=errors or 0, note=note)
                    closed.append(RunClosure(run_id, ds, "timed_out", processed, reported, verdict))
                elif close_at is None and started < now - abandon_after:
                    note = "Bağlayıcı çalışmayı kapatmadı (terk edildi)"
                    verdict = self.finish_run(run_id, completed=False, note=note)
                    closed.append(RunClosure(run_id, ds, "abandoned", processed, None, verdict))
        for c in closed:
            log.info("Çalışma kapatıldı: %s %s %s (%s/%s) devre kesici=%s", c.data_source, c.run_id, c.reason,
                     c.processed, c.reported, c.verdict.state.value)
        return closed

    # ------------------------------------------------------------------ kayıt işleme
    def apply(self, record: IngestRecord) -> ApplyResult:
        """Kaynak kaydını kimlik çözümlemesi ve uzlaştırmadan geçirir (tek işlem).

        Kaynak anahtarı ServiceNow sys_id'si olan kaynakta (``sys_id_key``) bağı olmayan kayıt kimlik çözümlemesine
        girmez; hedef CI sys_id ile belirlenir (ADR-0004, docs/05):

        * ``sync.sn_ci_map``'te bu SN sys_id'si varsa kayıt, Evren'de doğup replikaya IRE ile giden CI'dır
          (ServiceNow yeni sys_id verdi); eşlemedeki Evren CI'ına bağlanır.
        * Yoksa CI aynı sys_id ile açılır (ilk yükleme) ya da o sys_id'li CI zaten varsa ona bağlanır.

        Böylece ServiceNow task'larındaki CI referansları dönüşümsüz çözülür ve replikadan geri gelen kayıt mükerrer
        CI açmaz. Gölge veya devredilen kaynak CI açamayacağı için bu yola girmez.

        Referans tablosu kaydı (``class_hint`` = ``sys_user_group`` gibi; ADR-0022) kimlik çözümlemesine girmez:
        ``apply_reference``."""
        if is_reference(record.class_hint):
            return self.apply_reference(record)
        src = self.registry.source(record.data_source)
        if src.sys_id_key and _SYSID.match(record.source_key) and self.registry.may_create_ci(src.name):
            with self.conn.cursor() as cur:
                linked = PostgresLinkStore(cur).get(record.data_source, record.source_key)
                target = record.source_key
                if not linked:
                    cur.execute("SELECT evren_sys_id FROM sync.sn_ci_map WHERE sn_sys_id = %s", (record.source_key,))
                    row = cur.fetchone()
                    target = row[0] if row else record.source_key
            if not linked:
                return self.import_record(record, target)
        return self._process(record, attach_to=None, method=None)

    def apply_reference(self, record: IngestRecord) -> ApplyResult:
        """ServiceNow referans veya değişiklik kaydı → ``ref.<tablo>`` (ADR-0022, ADR-0025). Kaynak kaydı ve çalışma
        sayaçları CI kaydıyla aynı.

        * Referans tablolarını yalnızca referans kaynağı rolü, değişiklik tablolarını (``change_request``,
          ``task_ci``) yalnızca değişiklik kaynağı rolü yazar; kaynak etkin veya devrediliyor olmalı. Gölge kaynağın
          ve yetkisiz rolün kaydı sayılır, yazılmaz.
        * Sütunlar ``ROLE_TABLES``'tan; değer türü uymayan kayıt yazılmaz (``reference_invalid``).
        * Entra/Evren kaynaklı satır ezilmez (``reference_protected``); kullanıcı adı çakışması ``reference_conflict``.
        * Silinen kayıt ``active`` sütunu olan tabloda pasifleşir; satır silinmez."""
        spec = ROLE_TABLES[record.class_hint]
        digest = content_hash(record)
        table = sql.Identifier("ref", spec.db_table)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"{record.data_source}:{record.source_key}",))
            cur.execute("SELECT content_hash FROM ingest.source_record WHERE data_source = %s AND source_key = %s",
                        (record.data_source, record.source_key))
            row = cur.fetchone()
            if row and row[0] == digest:
                cur.execute("""UPDATE ingest.source_record SET last_seen = %s, last_run = %s, status = 'active'
                                WHERE data_source = %s AND source_key = %s""",
                            (record.observed_at, record.run_id, record.data_source, record.source_key))
                return ApplyResult("unchanged")
            self._upsert_source_record(cur, record, digest)
            if not self.registry.writes_role_table(record.data_source, spec.writer):
                what = "değişiklik kaydı" if spec.writer == "change" else "referans verisi"
                return ApplyResult("reference_skipped", summary=f"{record.data_source} {what} yazmaz "
                                                               "(rol veya yaşam döngüsü)")
            try:
                values = coerce_reference(spec.name, record.source_key, record.attributes)
            except ReferenceDataError as exc:
                log.warning("Referans kaydı yazılmadı: %s %s (%s)", spec.name, record.source_key, exc)
                return ApplyResult("reference_invalid", summary=f"Referans kaydı yazılmadı: {exc}")
            guard = sql.SQL(" AND source = 'servicenow'") if spec.has_source else sql.SQL("")
            if record.deleted:
                if not spec.has_active:
                    return ApplyResult("reference_kept", summary="Tabloda etkinlik sütunu yok; satır korunur")
                cur.execute(sql.SQL("UPDATE {} SET active = false, sys_updated_on = now() WHERE sys_id = %s{}").format(
                    table, guard), (record.source_key,))
                return ApplyResult("reference_deactivated" if cur.rowcount else "reference_protected")
            cols = list(values)
            update = sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in cols)
            where = sql.SQL(" WHERE {t}.source = 'servicenow'").format(t=sql.Identifier(spec.db_table)) \
                if spec.has_source else sql.SQL("")
            query = sql.SQL("INSERT INTO {t} (sys_id, {cols}, sys_updated_on) VALUES (%s, {vals}, now()) "
                            "ON CONFLICT (sys_id) DO UPDATE SET {upd}, sys_updated_on = now(){where}").format(
                t=table, cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
                vals=sql.SQL(", ").join([sql.Placeholder()] * len(cols)), upd=update, where=where)
            try:
                with self.conn.transaction():
                    cur.execute(query, (record.source_key, *values.values()))
            except psycopg.errors.UniqueViolation:
                log.warning("Referans kaydı yazılmadı (benzersizlik çakışması): %s %s", spec.name, record.source_key)
                return ApplyResult("reference_conflict", summary="Aynı kullanıcı adıyla başka bir satır var")
            return ApplyResult("reference" if cur.rowcount else "reference_protected")

    def import_record(self, record: IngestRecord, ci_sys_id: str) -> ApplyResult:
        """İlk yükleme: CI verilen sys_id ile açılır (ServiceNow sys_id'si korunur), kimlik çözümlemesi yapılmaz."""
        return self._process(record, attach_to=ci_sys_id, method="import")

    def attach_manual(self, record: IngestRecord, ci_sys_id: str) -> ApplyResult:
        """Elle giriş: Manual kaynak kaydı belirtilen CI'a bağlanır ve uzlaştırmadan geçer (CLAUDE.md kural 1)."""
        if self.registry.role_of(record.data_source) != "Manual":
            raise ValueError("Yalnızca Manual rolündeki kaynak kayıtları elle bağlanabilir")
        return self._process(record, attach_to=ci_sys_id, method="manual")

    def _process(self, record: IngestRecord, *, attach_to: str | None, method: str | None,
                 change_reason: str | None = None) -> ApplyResult:
        src = self.registry.source(record.data_source)
        digest = content_hash(record)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"{record.data_source}:{record.source_key}",))
            cur.execute("""SELECT content_hash, attributes, last_seen, status FROM ingest.source_record
                            WHERE data_source = %s AND source_key = %s""", (record.data_source, record.source_key))
            row = cur.fetchone()
            links = PostgresLinkStore(cur)
            linked = links.get(record.data_source, record.source_key)
            if row and row[0] == digest and (linked or attach_to is None):
                cur.execute("""UPDATE ingest.source_record SET last_seen = %s, last_run = %s, status = 'active'
                                WHERE data_source = %s AND source_key = %s""",
                            (record.observed_at, record.run_id, record.data_source, record.source_key))
                if linked:
                    self._touch_discovered(cur, linked, src.name, record.observed_at)
                    self._write_posture(cur, record, linked)  # kuyruk kararından sonra ilk çalışmada da yazılır
                return ApplyResult("unchanged", linked)
            self._upsert_source_record(cur, record, digest)
            index = PostgresCandidateIndex(cur)
            resolver = IdentityResolver(self.rules, index, links)
            sr = source_record_from_ingest(record)
            # Elle giriş sabit bir CI'a bağlıdır; yazım hatası kimlik dizinini kirletmesin diye anahtar eklemez.
            keys = keys_from_json({}) if method == "manual" else extract_keys(sr)

            if attach_to is not None:
                created = self._ensure_ci(cur, attach_to, record) if method == "import" else False
                self._attach(cur, index, resolver, record, keys, attach_to, method=method, rule="import" if created
                             else method, resolution=None)
                manual = method == "manual"
                self._write_posture(cur, record, attach_to)
                changes = self._reconcile(cur, attach_to, record.run_id, released="Manual" if manual else None,
                                          reason=change_reason or ("manual" if manual else "reconciliation"))
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
                self._write_posture(cur, record, ci)
                observed = self._observation(record, row) if res.outcome is Outcome.LINKED else None
                return ApplyResult(res.outcome.value, ci, self._reconcile(cur, ci, record.run_id, observed=observed),
                                   res.summary_tr)

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
                self._write_posture(cur, record, ci)
                return ApplyResult("new", ci, self._reconcile(cur, ci, record.run_id), res.summary_tr)

            kind = "review" if res.outcome is Outcome.REVIEW else "insufficient"
            self._enqueue(cur, record, res, kind, priority=2 if kind == "review" else 4, note=None)
            return ApplyResult(kind, res.ci_sys_id, [], res.summary_tr)

    # ------------------------------------------------------------------ yetkisiz değişiklik (ADR-0025)
    def _observation(self, record: IngestRecord, row: tuple[Any, ...] | None) -> Observation | None:
        """Kaynağın kendi kaydında boş olmayan bir değerden başka bir değere değişen öznitelikler. Yalnızca gözlemci
        rolündeki kaynak; önceki kayıt silinmiş veya kayıt eski tarihliyse (sıra dışı) gözlem yok."""
        if row is None or not self.policy.enabled or row[3] == "deleted":
            return None
        if self.registry.role_of(record.data_source) not in self.policy.observer_roles:
            return None
        prior_seen: datetime | None = row[2]
        if prior_seen is not None and prior_seen > record.observed_at:
            return None
        old = row[1] or {}
        new = record.model_dump(mode="json")["attributes"]
        changed = frozenset(k for k, v in new.items()
                            if v not in (None, "") and old.get(k) not in (None, "") and old.get(k) != v)
        return Observation(record.data_source, prior_seen, record.observed_at, changed) if changed else None

    def _record_observations(self, cur: psycopg.Cursor[Any], ci: str, cls: str, run_id: UUID | None,
                             observed: Observation, changes: list[dict[str, Any]]) -> int:
        """Gözlemi yapan kaynağın kazandığı, izlenen ve önceden dolu özniteliklerdeki değişimleri yazar."""
        watched = self.policy.watched_for(cls, self.hierarchy)
        written = 0
        for ch in changes:
            if (ch["reason"] != "reconciliation" or ch["source"] != observed.data_source or ch["old"] is None
                    or ch["attribute"] not in observed.attributes or ch["attribute"] not in watched):
                continue
            cur.execute(
                """INSERT INTO insight.config_change (ci_sys_id, sys_class_name, attribute, old_value, new_value,
                                                      data_source, run_id, observed_from, observed_at, recorded_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (ci, cls, ch["attribute"], Jsonb(ch["old"]), Jsonb(_jsonable(ch["new"])), observed.data_source,
                 run_id, observed.observed_from, observed.observed_at, self.clock()))
            written += 1
        return written

    def _change_candidates(self, cur: psycopg.Cursor[Any], ci: str, lo: datetime, hi: datetime,
                           now: datetime) -> list[ChangeRecord]:
        """CI'a (``cmdb_ci`` veya ``task_ci``) bağlı, penceresi aralığa değen değişiklik kayıtları. CI Evren'de doğup
        replikaya gittiyse ServiceNow'daki sys_id'si de aranır (``sync.sn_ci_map``)."""
        cur = self.conn.cursor()  # çağıranın imleci sözlük satırı döndürebilir
        cur.execute("SELECT sn_sys_id FROM sync.sn_ci_map WHERE evren_sys_id = %s AND sn_sys_id IS NOT NULL", (ci,))
        ids = [ci, *(r[0] for r in cur.fetchall())]
        tol = self.policy.tolerance
        cur.execute(
            """SELECT cr.sys_id, cr.number, cr.type, cr.state, cr.approval, cr.start_date, cr.end_date,
                      cr.work_start, cr.work_end
                 FROM ref.sn_change_request cr
                WHERE (cr.cmdb_ci = ANY(%(ids)s::bpchar[])
                       OR EXISTS (SELECT 1 FROM ref.sn_task_ci t
                                   WHERE t.task = cr.sys_id AND t.ci_item = ANY(%(ids)s::bpchar[])))
                  AND ((cr.start_date <= %(hi)s AND cr.end_date >= %(lo)s)
                       OR (cr.work_start <= %(hi)s AND COALESCE(cr.work_end, %(now)s) >= %(lo)s))
                ORDER BY cr.number LIMIT 100""",
            {"ids": ids, "lo": lo - tol, "hi": hi + tol, "now": now})
        cols = ("sys_id", "number", "type", "state", "approval", "start_date", "end_date", "work_start", "work_end")
        rows = cur.fetchall()
        cur.close()
        return [ChangeRecord.from_row(dict(zip(cols, r, strict=True))) for r in rows]

    def _describe(self, cur: psycopg.Cursor[Any], ci: str, cls: str, rows: list[dict[str, Any]]
                  ) -> tuple[str, dict[str, Any]]:
        """Anomali için CI adı ve öznitelik özeti (Türkçe) ile önem kuralının okuduğu CI alanları."""
        with self.conn.cursor(row_factory=dict_row) as dcur:
            dcur.execute("SELECT * FROM cmdb.cmdb_ci WHERE sys_id = %s", (ci,))
            current = dcur.fetchone() or {}
        values = {**(current.get("class_attributes") or {}), **{k: v for k, v in current.items()
                                                                 if k != "class_attributes"}}
        fields = self.md.fields_of(cls) if cls in self.hierarchy else {}
        parts = [f"{fields.get(r['attribute'], {}).get('label_tr', r['attribute'])} "
                 f"{r['old_value']} → {r['new_value']}" for r in rows]
        return f"{current.get('name') or ci}: " + "; ".join(parts), values

    def evaluate_config_changes(self, *, limit: int = 200) -> dict[str, int]:
        """Bekleme süresi dolan gözlemleri onaylı değişiklik penceresine göre değerlendirir; açık yetkisiz değişiklik
        anomalilerini sonradan gelen kayda karşı yeniden dener (ADR-0025 §5–6).

        * Çalışma kapanmadıysa gözlem bekler; devre kesici çalışmayı açtıysa ``suppressed`` (anomali yok).
        * CI, kaynak ve gözlem anı başına tek anomali; önem kuralı CI alanlarından.
        * İnsanın ``false_positive``/``resolved`` dediği anomaliye dokunulmaz."""
        counts = {"authorized": 0, "unauthorized": 0, "suppressed": 0, "late_authorized": 0}
        if not self.policy.enabled:
            return counts
        now = self.clock()
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT c.sys_id, c.ci_sys_id, c.sys_class_name, c.attribute, c.old_value, c.new_value,
                          c.data_source, c.run_id, c.observed_from, c.observed_at,
                          r.run_id IS NOT NULL AS has_run, r.finished_at, r.breaker_state
                     FROM insight.config_change c LEFT JOIN ingest.source_run r ON r.run_id = c.run_id
                    WHERE c.verdict = 'pending' AND GREATEST(c.recorded_at, c.observed_at) <= %s
                    ORDER BY c.recorded_at, c.sys_id LIMIT %s
                      FOR UPDATE OF c SKIP LOCKED""", (now - self.policy.grace, limit))
            groups: dict[tuple[str, str, datetime], list[dict[str, Any]]] = {}
            for r in cur.fetchall():
                if r["has_run"] and r["finished_at"] is None:
                    continue  # çalışma kapanmadan karar verilmez
                groups.setdefault((r["ci_sys_id"], r["data_source"], r["observed_at"]), []).append(r)
            for (ci, ds, observed_at), rows in groups.items():
                ids = [r["sys_id"] for r in rows]
                if rows[0]["breaker_state"] == BreakerState.OPEN.value:
                    cur.execute(
                        """UPDATE insight.config_change SET verdict = 'suppressed', evaluated_at = %s, detail = %s
                            WHERE sys_id = ANY(%s::bpchar[])""",
                        (now, Jsonb({"explanation": "Devre kesici çalışmayı açtı; çalışma güvenilir değil, yetkisiz "
                                                    "değişiklik kararı verilmedi."}), ids))
                    counts["suppressed"] += len(rows)
                    continue
                observed_from = rows[0]["observed_from"]
                candidates = self._change_candidates(cur, ci, observed_from or observed_at, observed_at, now)
                verdict = evaluate(observed_from, observed_at, candidates, self.policy, now)
                text = verdict.explanation(observed_from, observed_at)
                detail = {"explanation": text, "considered": list(verdict.considered), "window": verdict.window}
                if verdict.authorized and verdict.change:
                    cur.execute(
                        """UPDATE insight.config_change SET verdict = 'authorized', evaluated_at = %s,
                                  change_sys_id = %s, change_number = %s, detail = %s
                            WHERE sys_id = ANY(%s::bpchar[])""",
                        (now, verdict.change.sys_id, verdict.change.number, Jsonb(detail), ids))
                    counts["authorized"] += len(rows)
                    continue
                summary, values = self._describe(cur, ci, rows[0]["sys_class_name"], rows)
                cur.execute(
                    """INSERT INTO insight.anomaly (kind, severity, ci_sys_id, data_source, detected_at, window_start,
                                                    window_end, details, explanation)
                       VALUES ('unauthorized_change', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                    (self.policy.severity_for(values), ci, ds, now, observed_from, observed_at,
                     Jsonb({"observations": ids, "attributes": [
                         {"attribute": r["attribute"], "old": r["old_value"], "new": r["new_value"]} for r in rows],
                         "run_id": str(rows[0]["run_id"]) if rows[0]["run_id"] else None,
                         "considered": list(verdict.considered)}),
                     f"{summary}. Kaynak: {ds}. {text}"))
                anomaly = cur.fetchone()["sys_id"]  # type: ignore[index]
                cur.execute(
                    """UPDATE insight.config_change SET verdict = 'unauthorized', evaluated_at = %s, anomaly_sys_id = %s,
                              detail = %s WHERE sys_id = ANY(%s::bpchar[])""", (now, anomaly, Jsonb(detail), ids))
                counts["unauthorized"] += len(rows)
            counts["late_authorized"] = self._recheck_unauthorized(cur, now, limit)
        return counts

    def _recheck_unauthorized(self, cur: psycopg.Cursor[Any], now: datetime, limit: int) -> int:
        """Açık yetkisiz değişiklik anomalisini sonradan kaydedilen onaylı değişikliğe karşı yeniden dener (10 dakikada
        bir, ``recheck_days`` boyunca). Kapsanırsa gözlem ``authorized`` olur, anomali ``resolved``; kanıt kalır."""
        cur.execute(
            """SELECT a.sys_id AS anomaly, c.ci_sys_id, c.observed_from, c.observed_at,
                      array_agg(c.sys_id::text ORDER BY c.sys_id) AS ids
                 FROM insight.config_change c JOIN insight.anomaly a ON a.sys_id = c.anomaly_sys_id
                WHERE c.verdict = 'unauthorized' AND a.status IN ('open', 'acknowledged')
                  AND c.observed_at >= %s AND c.evaluated_at <= %s
                GROUP BY 1, 2, 3, 4 ORDER BY 4 LIMIT %s""",
            (now - self.policy.recheck, now - timedelta(minutes=10), limit))
        late = 0
        for g in cur.fetchall():
            ids = list(g["ids"])
            candidates = self._change_candidates(cur, g["ci_sys_id"], g["observed_from"] or g["observed_at"],
                                                 g["observed_at"], now)
            verdict = evaluate(g["observed_from"], g["observed_at"], candidates, self.policy, now)
            if not (verdict.authorized and verdict.change):
                cur.execute("UPDATE insight.config_change SET evaluated_at = %s WHERE sys_id = ANY(%s::bpchar[])",
                            (now, ids))
                continue
            note = (f"Sonradan kaydedilen değişiklik kaydı kapsıyor: {verdict.change.number} "
                    f"({verdict.window} pencere).")
            cur.execute(
                """UPDATE insight.config_change SET verdict = 'authorized', evaluated_at = %s, change_sys_id = %s,
                          change_number = %s, detail = detail || %s WHERE sys_id = ANY(%s::bpchar[])""",
                (now, verdict.change.sys_id, verdict.change.number,
                 Jsonb({"late": True, "explanation": note, "window": verdict.window}), ids))
            cur.execute(
                """UPDATE insight.anomaly SET status = 'resolved', related_change = %s,
                          explanation = explanation || ' ' || %s,
                          details = details || %s
                    WHERE sys_id = %s AND status IN ('open', 'acknowledged')""",
                (verdict.change.number, note, Jsonb({"resolution": "late_change_record",
                                                     "resolved_at": now.isoformat()}), g["anomaly"]))
            late += len(ids)
        return late

    # ------------------------------------------------------------------ kısıtlı güvenlik duruşu (ADR-0023)
    def _write_posture(self, cur: psycopg.Cursor[Any], record: IngestRecord, ci: str) -> bool:
        """Bağlı CI'ın duruşunu ``cmdb.ci_security_posture``'a yazar. Yalnızca duruş kaynağı rolündeki etkin/devredilen
        kaynak yazar; aynı CI'ın daha öncelikli (rol içi sırası küçük) kaynağının satırı ezilmez."""
        p = record.posture
        if p is None:
            return False
        ds = record.data_source
        if not self.registry.writes_posture(ds):
            if not self.registry.is_shadow(ds):
                log.warning("Duruş verisi yok sayıldı: %s duruş kaynağı rolünde değil", ds)
            return False
        cur.execute("SELECT data_source FROM cmdb.ci_security_posture WHERE ci_sys_id = %s FOR UPDATE", (ci,))
        row = cur.fetchone()
        if row and row[0] != ds and row[0] in self.registry.sources and self.registry.writes_posture(row[0]) \
                and self.registry.rank_of(row[0]) < self.registry.rank_of(ds):
            return False
        cur.execute(
            """INSERT INTO cmdb.ci_security_posture (ci_sys_id, data_source, protection_status, health_status,
                                                    exposure_level, risk_score, vulnerabilities_critical,
                                                    vulnerabilities_high, last_seen, observed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (ci_sys_id) DO UPDATE SET data_source = EXCLUDED.data_source,
                 protection_status = EXCLUDED.protection_status, health_status = EXCLUDED.health_status,
                 exposure_level = EXCLUDED.exposure_level, risk_score = EXCLUDED.risk_score,
                 vulnerabilities_critical = EXCLUDED.vulnerabilities_critical,
                 vulnerabilities_high = EXCLUDED.vulnerabilities_high, last_seen = EXCLUDED.last_seen,
                 observed_at = EXCLUDED.observed_at""",
            (ci, ds, p.protection_status, p.health_status, p.exposure_level, p.risk_score, p.vulnerabilities_critical,
             p.vulnerabilities_high, p.last_seen, record.observed_at))
        return True

    # ------------------------------------------------------------------ yardımcılar: kaynak kaydı, bağ, kuyruk
    def _upsert_source_record(self, cur: psycopg.Cursor[Any], record: IngestRecord, digest: str) -> None:
        dumped = record.model_dump(mode="json")
        cur.execute(
            """INSERT INTO ingest.source_record (data_source, source_key, class_hint, attributes, lookup, external_ids,
                                                 relations, content_hash, raw_ref, status, first_seen, last_seen,
                                                 last_changed, last_run, first_run, changed_run)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s)
               ON CONFLICT (data_source, source_key) DO UPDATE SET class_hint = EXCLUDED.class_hint,
                 attributes = EXCLUDED.attributes, lookup = EXCLUDED.lookup, external_ids = EXCLUDED.external_ids,
                 relations = EXCLUDED.relations, content_hash = EXCLUDED.content_hash, raw_ref = EXCLUDED.raw_ref,
                 status = EXCLUDED.status, last_seen = EXCLUDED.last_seen, last_changed = now(),
                 last_run = EXCLUDED.last_run, changed_run = EXCLUDED.changed_run""",
            (record.data_source, record.source_key, record.class_hint, Jsonb(dumped["attributes"]),
             Jsonb(dumped["lookup"]), Jsonb(dumped["external_ids"]), Jsonb(dumped["relations"]), digest,
             record.raw_ref, "deleted" if record.deleted else "active", record.observed_at, record.observed_at,
             record.run_id, record.run_id, record.run_id))

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
            if attribute in BOOLEAN_COLUMNS:
                if isinstance(value, bool):
                    return "column", value
                text = str(value).strip().lower()
                return ("column", text in ("true", "1", "yes", "evet")) if text in (
                    "true", "false", "1", "0", "yes", "no", "evet", "hayır") else None
            return "column", value if isinstance(value, str) else str(value)
        if fdef is None or fdef.get("storage", "jsonb") != "jsonb":
            return None
        cast = _JSON_TYPES.get(fdef.get("type", "string"), str)
        try:
            return "jsonb", cast(value)
        except (TypeError, ValueError):
            return None

    def _reconcile(self, cur: psycopg.Cursor[Any], ci: str, run_id: UUID | None, *,
                   released: str | None = None, reason: str = "reconciliation",
                   force: bool = False, observed: Observation | None = None) -> list[dict[str, Any]]:
        """CI'ın etkin bağlı kaynak kayıtlarını uzlaştırır.

        ``released``: CI'dan değer çekilen veri kaynağı — ayırmada ayrılan kaynak, elle düzenlemede değeri kaldırılan
        Manual. Önceki kazananı bu kaynak olan ve artık hiçbir kaynağın bildirmediği öznitelikler temizlenir;
        denetimde neden ayırmada ``split``, elle düzenlemede ``manual``.

        ``observed``: kaydı yeni işlenen kaynağın kendi kaydında değişen öznitelikler (ADR-0025). Bu kaynağın kazandığı
        ve izlenen özniteliklerdeki golden record değişimi ``insight.config_change``'e gözlem olarak yazılır.
        """
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
            dcur.execute("SELECT input_hash, winners FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s", (ci,))
            state = dcur.fetchone()
        if current is None:
            raise LookupError(f"CI yok: {ci}")
        if state and state["input_hash"] == input_hash and not force:
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
                                "reason": reason})
                if where == "column":
                    columns[name] = value
                else:
                    attrs[name] = value
        if released and state:
            for name, prev in (state["winners"] or {}).items():
                if prev.get("source") != released or name in report.decisions:
                    continue
                in_column = name in CORE_COLUMNS
                old = current.get(name) if in_column else attrs.get(name)
                if old is None:
                    continue
                if in_column:
                    columns[name] = None
                else:
                    attrs.pop(name, None)
                changes.append({"attribute": name, "old": _jsonable(old), "new": None, "source": released,
                                "reason": "split" if reason == "reconciliation" else reason})
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
        if observed is not None:
            self._record_observations(cur, ci, cls, run_id, observed, changes)
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
        self._write_relations(cur, data_source, sql.SQL("sr.last_run = %s"), run_id)

    def _resolve_relations_of(self, cur: psycopg.Cursor[Any], data_source: str, source_key: str) -> None:
        """Tek kaynak kaydının ilişkileri (ayırmadan sonra yeni CI'a). Asıl CI'da kalan eski ilişki satırları
        silinmez; yıkıcı işlem olduğu için eskime kuralına bırakılır (CLAUDE.md kural 4)."""
        self._write_relations(cur, data_source, sql.SQL("sr.source_key = %s"), source_key)

    def _write_relations(self, cur: psycopg.Cursor[Any], data_source: str, where: sql.Composable, value: Any) -> None:
        cur.execute(sql.SQL(
            """SELECT sr.source_key, sr.relations, l.ci_sys_id, sr.last_seen
                 FROM ingest.source_record sr
                 JOIN ingest.identity_link l ON l.data_source = sr.data_source AND l.source_key = sr.source_key AND l.active
                WHERE sr.data_source = %s AND {} AND jsonb_array_length(sr.relations) > 0""").format(where),
            (data_source, value))
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

    # ------------------------------------------------------------------ insan istekleri: ortak döngü
    def _drain(self, kind: str, fetch_sql: str, handle: Callable[[dict[str, Any]], ApplyResult],
               fail: Callable[[psycopg.Cursor[Any], dict[str, Any], str], None], limit: int) -> list[ApplyResult]:
        """Bekleyen istekleri birer birer, her biri kendi işleminde uygular.

        Birden fazla uzlaştırıcı aynı anda çalışabilir (``FOR UPDATE SKIP LOCKED``). Bir isteğin uygulanması
        beklenmeyen bir hatayla düşerse yalnızca o isteğin değişiklikleri geri alınır (kayıt noktası); istek hata
        kimliğiyle reddedilir ve kuyruk kilitlenmez. Bağlantı/kilitlenme gibi geçici hatalar (``OperationalError``)
        yukarı iletilir: istek beklemede kalır, çalışan yeniden dener.
        """
        results: list[ApplyResult] = []
        for _ in range(limit):
            with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as dcur:
                dcur.execute(fetch_sql)
                req = dcur.fetchone()
                if req is None:
                    break
                try:
                    with self.conn.transaction():
                        results.append(handle(req))
                except psycopg.OperationalError:
                    raise
                except Exception:
                    error_id = uuid4().hex[:12]
                    log.exception("istek uygulanamadı: tür=%s istek=%s hata_kimliği=%s", kind, req["sys_id"], error_id)
                    note = f"Uygulanamadı: beklenmeyen hata (hata kimliği {error_id}); yöneticiye bildirin"
                    fail(dcur, req, note)
                    results.append(ApplyResult(f"{kind}_failed", req.get("ci_sys_id"), [], note))
        return results

    # ------------------------------------------------------------------ insan kararları (eşleştirme kuyruğu)
    def apply_decisions(self, limit: int = 100) -> list[ApplyResult]:
        """API'nin kaydettiği kuyruk kararlarını uygular (kimlik bağı veya yeni CI) ve uzlaştırır.

        Karar veren kişi bağın ``decided_by`` alanına yazılır; yöntem ``manual`` olur.
        """
        def handle(row: dict[str, Any]) -> ApplyResult:
            with self.conn.cursor() as cur:
                record = _record_from_row(row)
                result = ApplyResult("decision_rejected", None, [], "Kaynakta düzeltilecek")
                if row["status"] in ("accepted", "created_new"):
                    ci = row["decision_ci"] if row["status"] == "accepted" else self._create_ci(cur, record)
                    index = PostgresCandidateIndex(cur)
                    resolver = IdentityResolver(self.rules, index, PostgresLinkStore(cur))
                    keys = extract_keys(source_record_from_ingest(record))
                    actor = self.actor
                    self.actor = row["decided_by"] or actor  # bağın karar vereni insan
                    try:
                        self._attach(cur, index, resolver, record, keys, ci, method="manual",
                                     rule=f"karar:{row['decision']}", resolution=None)
                    finally:
                        self.actor = actor
                    result = ApplyResult("decision_" + ("merged" if row["status"] == "accepted" else "new_ci"),
                                         ci, self._reconcile(cur, ci, None), "İnsan kararı uygulandı")
                cur.execute("UPDATE ingest.match_candidate SET applied_at = now() WHERE sys_id = %s",
                            (row["sys_id"],))
            return result

        def fail(cur: psycopg.Cursor[Any], row: dict[str, Any], note: str) -> None:
            cur.execute("UPDATE ingest.match_candidate SET applied_at = now(), apply_error = %s WHERE sys_id = %s",
                        (note, row["sys_id"]))

        return self._drain("decision", """
            SELECT mc.sys_id, mc.data_source, mc.source_key, mc.status, mc.decision, mc.decision_ci,
                   mc.decided_by, sr.class_hint, sr.attributes, sr.lookup, sr.external_ids, sr.relations,
                   sr.last_seen, sr.last_run
              FROM ingest.match_candidate mc
              JOIN ingest.source_record sr USING (data_source, source_key)
             WHERE mc.applied_at IS NULL AND mc.status IN ('accepted', 'created_new', 'rejected')
             ORDER BY mc.decided_at, mc.sys_id
             LIMIT 1 FOR UPDATE OF mc SKIP LOCKED""", handle, fail, limit)

    # ------------------------------------------------------------------ elle düzenleme (Manual kaynak kaydı)
    def apply_manual_edits(self, limit: int = 100) -> list[ApplyResult]:
        """API'nin kaydettiği elle düzenleme isteklerini uygular (CLAUDE.md kural 1).

        CI başına tek bir ``Manual`` kaynak kaydı vardır (``source_key`` = CI sys_id). İstekteki değerler bu kayda
        eklenir (``null``: o alandaki elle değer kaldırılır) ve kayıt uzlaştırmadan geçer. Rol önceliği geçerlidir:
        keşif sahipli alanda Manual yalnızca yedektir. Sonuç alan başına isteğe yazılır.
        """
        def handle(req: dict[str, Any]) -> ApplyResult:
            ci = req["ci_sys_id"]
            with self.conn.cursor(row_factory=dict_row) as dcur:
                dcur.execute("SELECT attributes, relations FROM ingest.source_record WHERE data_source = 'Manual' "
                             "AND source_key = %s", (ci,))
                prev = dcur.fetchone()
                merged = dict(prev["attributes"]) if prev else {}
                relations = list(prev["relations"] or []) if prev else []  # elle ilişkiler korunur (ADR-0026)
                for name, value in req["attributes"].items():
                    if value is None:
                        merged.pop(name, None)
                    else:
                        merged[name] = value
                record = IngestRecord(data_source="Manual", run_id=uuid4(), source_key=ci,
                                      class_hint=req["sys_class_name"], attributes=merged, relations=relations,
                                      observed_at=self.clock())
                actor = self.actor
                self.actor = req["requested_by"]  # bağın ve değişimin sahibi insan
                try:
                    applied = self._process(record, attach_to=ci, method="manual")
                finally:
                    self.actor = actor
                dcur.execute("SELECT winners, conflicts FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s",
                             (ci,))
                st = dcur.fetchone() or {"winners": {}, "conflicts": {}}
                outcome = {name: _manual_outcome(name, value, st["winners"] or {}, st["conflicts"] or {})
                           for name, value in req["attributes"].items()}
                note = _manual_note(outcome)
                dcur.execute(
                    """UPDATE ingest.manual_edit SET status = 'applied', result = %s, result_note = %s,
                              applied_at = now()
                        WHERE sys_id = %s""", (Jsonb(outcome), note, req["sys_id"]))
            return ApplyResult("manual", ci, applied.changes, note)

        def fail(cur: psycopg.Cursor[Any], req: dict[str, Any], note: str) -> None:
            cur.execute("""UPDATE ingest.manual_edit SET status = 'rejected', result_note = %s, applied_at = now()
                            WHERE sys_id = %s""", (note, req["sys_id"]))

        return self._drain("manual", """
            SELECT e.sys_id, e.ci_sys_id, e.attributes, e.requested_by, ci.sys_class_name
              FROM ingest.manual_edit e JOIN cmdb.cmdb_ci ci ON ci.sys_id = e.ci_sys_id
             WHERE e.status = 'pending' ORDER BY e.requested_at, e.sys_id
             LIMIT 1 FOR UPDATE OF e SKIP LOCKED""", handle, fail, limit)

    # ------------------------------------------------------------------ elle CI oluşturma (ADR-0026)
    def apply_manual_creates(self, limit: int = 100) -> list[ApplyResult]:
        """API'nin kaydettiği elle CI oluşturma isteklerini kimlik çözümlemesinden geçirip uygular (ADR-0026).

        * Eşleşme yok veya kimlik anahtarı yok: CI açılır; CI başına tek ``Manual`` kaynak kaydı (``source_key`` = CI
          sys_id) kimlik anahtarlarıyla dizine girer (keşif aynı CI'ı sonradan tanır) ve uzlaşır (neden ``manual``).
        * Kesin eşleşme (tanımlama kuralı): açılmaz, ``duplicate``; var olan CI sonuçta. Zorlama yok (kural 7).
        * Belirsiz eşleşme (skor, inceleme) veya kimlik anahtarı olmadan aynı ailede aynı ad: açılmaz,
          ``needs_decision``; adaylar sonuçta. Gerekçeli ``force_new`` ile yeniden istenirse açılır.
        Bağın, değişimin ve CI'ı açanın sahibi isteği yapan kişidir."""
        def handle(req: dict[str, Any]) -> ApplyResult:
            cls = req["sys_class_name"]
            with self.conn.cursor() as cur:
                if cls not in self.hierarchy or cls == "cmdb_ci" or not self.registry.may_create_ci("Manual"):
                    note = "Bu sınıfta elle CI açılamaz (kök sınıf, tanımsız sınıf veya elle giriş kaynağı CI açamıyor)"
                    self._finish_create(cur, req, "rejected", None, {}, note)
                    return ApplyResult("create_rejected", None, [], note)
                probe = IngestRecord(data_source="Manual", run_id=uuid4(), source_key=req["sys_id"], class_hint=cls,
                                     attributes=req["attributes"], observed_at=self.clock())
                index = PostgresCandidateIndex(cur)
                resolver = IdentityResolver(self.rules, index, PostgresLinkStore(cur))
                sr = source_record_from_ingest(probe)
                keys = extract_keys(sr)
                res = resolver.resolve(sr, keys)
                if res.outcome in (Outcome.RULE_MATCH, Outcome.LINKED) and res.ci_sys_id:
                    name = self._ci_name(cur, res.ci_sys_id)
                    note = f"Bu kimlik zaten '{name}' CI'ında var ({res.summary_tr}); yeni CI açılmadı. Var olan CI'ı düzenleyin."
                    self._finish_create(cur, req, "duplicate", res.ci_sys_id,
                                        {"outcome": res.outcome.value, "rule": res.rule,
                                         "reasons": list(res.reasons)}, note)
                    return ApplyResult("create_duplicate", res.ci_sys_id, [], note)
                candidates = self._create_candidates(cur, res, cls, str(req["attributes"].get("name") or ""))
                if candidates and not req["force_new"]:
                    note = ("Benzer CI'lar var; yeni CI açılmadı. Adayları inceleyin; yine de açılacaksa 'Yeni CI…' "
                            "penceresinden gerekçeyle açın." if req.get("batch_id") else
                            "Benzer CI'lar var; yeni CI açılmadı. Adayları inceleyin; yine de açmak için gerekçe yazıp "
                            "'yeni CI aç' deyin.")
                    self._finish_create(cur, req, "needs_decision", None,
                                        {"outcome": res.outcome.value, "candidates": candidates,
                                         "reasons": list(res.reasons)}, note)
                    return ApplyResult("create_needs_decision", None, [], note)
                actor = self.actor
                self.actor = req["requested_by"]  # CI'ı açan, bağın ve değişimin sahibi insan
                try:
                    ci = self._create_ci(cur, probe)
                    record = probe.model_copy(update={"source_key": ci})
                    self._upsert_source_record(cur, record, content_hash(record))
                    self._attach(cur, index, resolver, record, keys, ci, method="manual",
                                 rule="elle oluşturma" + (" (gerekçeli)" if req["force_new"] else ""), resolution=res)
                    changes = self._reconcile(cur, ci, record.run_id, reason="manual")
                finally:
                    self.actor = actor
                warn = "" if keys.has_strong() else (" Kimlik anahtarı (seri, FQDN, IP, MAC) yok: keşif bu CI'ı "
                                                     "tanıyamayabilir; bilinen bir anahtar eklemeniz önerilir.")
                note = f"CI açıldı ({len(changes)} öznitelik).{warn}"
                self._finish_create(cur, req, "created", ci,
                                    {"outcome": res.outcome.value, "forced": bool(req["force_new"] and candidates),
                                     "candidates": candidates, "identity_keys": keys.has_strong()}, note)
                return ApplyResult("created", ci, changes, note)

        def fail(cur: psycopg.Cursor[Any], req: dict[str, Any], note: str) -> None:
            self._finish_create(cur, req, "rejected", None, {}, note)

        return self._drain("create", """
            SELECT sys_id, sys_class_name, attributes, force_new, requested_by, batch_id FROM ingest.manual_create
             WHERE status = 'pending' ORDER BY requested_at, batch_id NULLS FIRST, row_no, sys_id
             LIMIT 1 FOR UPDATE SKIP LOCKED""", handle, fail, limit)

    @staticmethod
    def _finish_create(cur: psycopg.Cursor[Any], req: dict[str, Any], status: str, ci: str | None,
                       result: dict[str, Any], note: str) -> None:
        cur.execute("""UPDATE ingest.manual_create SET status = %s, result_ci = %s, result = %s, result_note = %s,
                              applied_at = now() WHERE sys_id = %s""", (status, ci, Jsonb(result), note, req["sys_id"]))

    @staticmethod
    def _ci_name(cur: psycopg.Cursor[Any], ci: str) -> str:
        cur.execute("SELECT name FROM cmdb.cmdb_ci WHERE sys_id = %s", (ci,))
        row = cur.fetchone()
        return (row[0] if row else None) or ci

    def _create_candidates(self, cur: psycopg.Cursor[Any], res: Resolution, cls: str,
                           name: str) -> list[dict[str, Any]]:
        """Belirsiz eşleşmenin adayları; kimlik anahtarı yoksa aynı sınıf ailesinde aynı adı taşıyan CI'lar (ad tek
        başına kimlik değildir; yalnızca insana gösterilir)."""
        found: dict[str, float | None] = {}
        if res.outcome in (Outcome.SCORE_MATCH, Outcome.REVIEW):
            if res.ci_sys_id:
                found[res.ci_sys_id] = res.probability
            for ci, prob in res.candidates:
                found.setdefault(ci, prob)
        if name.strip():
            cur.execute("""SELECT sys_id, sys_class_name FROM cmdb.cmdb_ci
                            WHERE lower(name) = lower(%s) ORDER BY sys_id LIMIT 20""", (name.strip(),))
            for ci, other in cur.fetchall():
                if other in self.hierarchy and self.hierarchy.compatible(cls, other):
                    found.setdefault(ci, None)
        if not found:
            return []
        cur.execute("SELECT sys_id, name, sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])",
                    (list(found),))
        rows = {r[0]: r for r in cur.fetchall()}
        out = [{"sys_id": ci, "name": rows[ci][1], "class": rows[ci][2],
                "probability": round(float(prob), 3) if prob is not None else None}
               for ci, prob in found.items() if ci in rows]
        return sorted(out, key=lambda c: (-(c["probability"] or 0), c["name"] or ""))[:10]

    # ------------------------------------------------------------------ elle ilişki (ADR-0026 §5)
    def apply_manual_relations(self, limit: int = 100) -> list[ApplyResult]:
        """API'nin kaydettiği elle ilişki ekleme/kaldırma isteklerini uygular (ADR-0026 §5).

        * İlişki CI'ın ``Manual`` kaynak kaydının ilişki listesinde durur (hedef CI sys_id'siyle); ``cmdb_rel_ci``'a
          ``Manual`` kaynağıyla yazılır. İzinli ilişki kuralı (ADR-0019) elle girişte zorunludur: ters yön veya kuralsız
          ilişki reddedilir.
        * Kaldırma yalnızca elle girilmiş ilişkiye uygulanır; satır yalnızca ``Manual`` kaynaklıysa silinir (keşfin
          bildirdiği ilişki kaynağından kalkar).
        * İki uçta da değişim geçmişine ``relation`` kaydı (neden ``manual``, kişi isteyen) ve CI olayı yazılır."""
        def handle(req: dict[str, Any]) -> ApplyResult:
            ci, target, rel_type, direction = req["ci_sys_id"], req["target_sys_id"], req["type"], req["direction"]
            parent, child = (ci, target) if direction == "parent" else (target, ci)
            with self.conn.cursor() as cur:
                cur.execute("SELECT sys_id, sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])",
                            ([parent, child],))
                classes = dict(cur.fetchall())
                cur.execute("SELECT sys_id FROM cmdb.cmdb_rel_type WHERE name = %s", (rel_type,))
                type_row = cur.fetchone()
                if len(classes) != 2 or type_row is None:
                    note = "CI veya ilişki tipi bulunamadı"
                    self._finish_relation(cur, req, "rejected", note)
                    return ApplyResult("relation_rejected", ci, [], note)
                ref = {"type": rel_type, "direction": direction, "target_source_key": target,
                       "target_data_source": "Manual", "target_class_hint": classes[target]}
                # Elle ilişki hangi uçtan eklendiyse o CI'ın Manual kaydında durur; iki uçtan da bulunur.
                holder, attrs, relations, same = ci, {}, [], []
                for end, end_dir, other in ((ci, direction, target),
                                            (target, "child" if direction == "parent" else "parent", ci)):
                    end_attrs, end_rels = self._manual_record(cur, end)
                    found = [r for r in end_rels if (r.get("type"), r.get("direction"), r.get("target_source_key"))
                             == (rel_type, end_dir, other)]
                    if end == ci:
                        attrs, relations = end_attrs, end_rels
                    if found:
                        holder, attrs, relations, same = end, end_attrs, end_rels, found
                        break
                if req["action"] == "add":
                    verdict = self.md.relations.check(classes[parent], rel_type, classes[child])
                    if not verdict.allowed:
                        note = verdict.message(classes[parent], rel_type, classes[child])
                        self._finish_relation(cur, req, "rejected", note)
                        return ApplyResult("relation_rejected", ci, [], note)
                    if not same:
                        relations.append(ref)
                    cur.execute(
                        """INSERT INTO cmdb.cmdb_rel_ci (parent, child, type, discovery_source, first_discovered,
                                                        last_discovered)
                           VALUES (%s, %s, %s, 'Manual', %s, %s) ON CONFLICT (parent, child, type) DO NOTHING""",
                        (parent, child, type_row[0], self.clock(), self.clock()))
                    note = ("İlişki eklendi." if cur.rowcount else
                            "İlişki zaten vardı (başka bir kaynaktan); elle kayıt da eklendi.")
                    old, new = None, {"type": rel_type, "parent": parent, "child": child}
                else:
                    if not same:
                        note = "Bu ilişki elle girilmemiş; keşfin bildirdiği ilişki elle kaldırılamaz"
                        self._finish_relation(cur, req, "rejected", note)
                        return ApplyResult("relation_rejected", ci, [], note)
                    relations = [r for r in relations if r not in same]
                    cur.execute("""DELETE FROM cmdb.cmdb_rel_ci WHERE parent = %s AND child = %s AND type = %s
                                     AND discovery_source = 'Manual'""", (parent, child, type_row[0]))
                    note = ("İlişki kaldırıldı." if cur.rowcount else
                            "Elle kayıt kaldırıldı; ilişkiyi başka bir kaynak da bildirdiği için satır kalır.")
                    old, new = {"type": rel_type, "parent": parent, "child": child}, None
                actor = self.actor
                self.actor = req["requested_by"]
                try:
                    record = IngestRecord(data_source="Manual", run_id=uuid4(), source_key=holder,
                                          class_hint=classes[holder], attributes=attrs, relations=relations,
                                          observed_at=self.clock())
                    self._process(record, attach_to=holder, method="manual")
                    for end in (ci, target):
                        cur.execute(
                            """INSERT INTO audit.ci_change (ci_sys_id, sys_class_name, attribute, old_value, new_value,
                                                           data_source, actor, reason, correlation)
                               VALUES (%s, %s, 'relation', %s, %s, 'Manual', %s, 'manual', %s)""",
                            (end, classes[end], Jsonb(old), Jsonb(new), self.actor,
                             Jsonb({"request": req["sys_id"]})))
                        self._outbox(cur, end, "cmdb.ci.changed.v1", {
                            "sys_id": end, "class": classes[end], "run_id": None,
                            "changes": [{"attribute": "relation", "old": old, "new": new, "source": "Manual"}]})
                finally:
                    self.actor = actor
                self._finish_relation(cur, req, "applied", note)
                return ApplyResult("relation", ci, [], note)

        def fail(cur: psycopg.Cursor[Any], req: dict[str, Any], note: str) -> None:
            self._finish_relation(cur, req, "rejected", note)

        return self._drain("relation", """
            SELECT sys_id, ci_sys_id, target_sys_id, type, direction, action, requested_by FROM ingest.manual_relation
             WHERE status = 'pending' ORDER BY requested_at, sys_id
             LIMIT 1 FOR UPDATE SKIP LOCKED""", handle, fail, limit)

    @staticmethod
    def _manual_record(cur: psycopg.Cursor[Any], ci: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """CI'ın Manual kaynak kaydının öznitelikleri ve ilişki listesi (yoksa boş)."""
        cur.execute("""SELECT attributes, relations FROM ingest.source_record
                        WHERE data_source = 'Manual' AND source_key = %s""", (ci,))
        row = cur.fetchone()
        return (dict(row[0] or {}), list(row[1] or [])) if row else ({}, [])

    @staticmethod
    def _finish_relation(cur: psycopg.Cursor[Any], req: dict[str, Any], status: str, note: str) -> None:
        cur.execute("""UPDATE ingest.manual_relation SET status = %s, result_note = %s, applied_at = now()
                        WHERE sys_id = %s""", (status, note, req["sys_id"]))

    # ------------------------------------------------------------------ birleştirmeyi ayırma (split)
    def apply_splits(self, limit: int = 100) -> list[ApplyResult]:
        """API'nin kaydettiği ayırma isteklerini uygular (CLAUDE.md kural 3: birleştirme geri alınabilir).

        1. Bağ sonlandırılır (``active = false``, ``end_reason``); geçmiş kalır.
        2. Kaynak kaydı yeni CI'a (veya istenen CI'a) ``split`` yöntemiyle, isteyen kişinin adıyla bağlanır.
           Bağ kalıcı olduğu için sonraki çalışmalarda kayıt asıl CI'a otomatik geri birleşmez.
        3. Asıl CI'ın kimlik profili kalan kaynak kayıtlarından yeniden kurulur (ayrılan kaydın seri, UUID, IP…
           anahtarları artık bu CI'a aday üretmez).
        4. İki CI yeniden uzlaştırılır; asıl CI'da yalnızca ayrılan kaynaktan gelen değerler temizlenir.

        İstek güvenle reddedilir: bağ istekten sonra değiştiyse, kayıt CI'ın ilk yükleme kaydıysa (sys_id o
        kayıttan gelir), elle giriş kaydıysa, CI'ın tek kaynak kaydıysa veya kaynak yeni CI açamıyorsa (gölge/devir)
        ve hedef yoksa.
        """
        def handle(req: dict[str, Any]) -> ApplyResult:
            ds, key, ci = req["data_source"], req["source_key"], req["ci_sys_id"]
            with self.conn.cursor() as cur, self.conn.cursor(row_factory=dict_row) as dcur:
                # _process ile aynı kilit: aynı kaynak kaydı aynı anda alımdan da geçmesin.
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{ds}:{key}",))
                reason = self._split_blocker(cur, req)
                if reason:
                    cur.execute(
                        """UPDATE ingest.split_request SET status = 'rejected', result_note = %s, applied_at = now()
                            WHERE sys_id = %s""", (reason, req["sys_id"]))
                    return ApplyResult("split_rejected", ci, [], reason)
                dcur.execute(
                    """SELECT data_source, source_key, class_hint, attributes, lookup, external_ids, relations,
                              last_seen, last_run
                         FROM ingest.source_record WHERE data_source = %s AND source_key = %s""", (ds, key))
                record = _record_from_row(dcur.fetchone())
                by = req["requested_by"]
                cur.execute(
                    """UPDATE ingest.identity_link SET active = false, ended_at = now(), end_reason = %s
                        WHERE sys_id = %s""", (f"ayrıldı: {by}"[:500], req["link_sys_id"]))
                target = req["target_ci"] or self._create_ci(cur, record)
                index = PostgresCandidateIndex(cur)
                resolver = IdentityResolver(self.rules, index, PostgresLinkStore(cur))
                keys = extract_keys(source_record_from_ingest(record))
                actor = self.actor
                self.actor = by  # bağın ve CI değişiminin karar vereni insan
                try:
                    self._attach(cur, index, resolver, record, keys, target, method="split",
                                 rule=f"ayırma:{req['sys_id']}", resolution=None)
                finally:
                    self.actor = actor
                self._rebuild_profile(cur, index, resolver, ci)
                changes = self._reconcile(cur, ci, None, released=ds) + self._reconcile(cur, target, None)
                self._resolve_relations_of(cur, ds, key)
                self._outbox(cur, ci, "evren.ci.split.v1", {
                    "sys_id": ci, "new_ci": target, "created": req["target_ci"] is None, "data_source": ds,
                    "source_key": key, "by": by, "request": req["sys_id"]})
                cur.execute(
                    """UPDATE ingest.split_request SET status = 'applied', result_ci = %s, applied_at = now(),
                              result_note = %s
                        WHERE sys_id = %s""",
                    (target, "Yeni CI açıldı" if req["target_ci"] is None else "Seçilen CI'a bağlandı",
                     req["sys_id"]))
            return ApplyResult("split", target, changes, "Ayırma uygulandı")

        def fail(cur: psycopg.Cursor[Any], req: dict[str, Any], note: str) -> None:
            cur.execute("""UPDATE ingest.split_request SET status = 'rejected', result_note = %s, applied_at = now()
                            WHERE sys_id = %s""", (note, req["sys_id"]))

        return self._drain("split", """
            SELECT sys_id, link_sys_id, ci_sys_id, data_source, source_key, target_ci, requested_by, note
              FROM ingest.split_request WHERE status = 'pending'
             ORDER BY requested_at, sys_id LIMIT 1 FOR UPDATE SKIP LOCKED""", handle, fail, limit)

    def rereconcile_source(self, data_source: str, *, batch: int = 500) -> dict[str, int]:
        """Veri kaynağının yaşam döngüsü veya rol sırası değişince (ADR-0009) bağlı CI'ları yeniden uzlaştırır.

        Depo, değişikliği içeren metadata ile kurulmuş olmalıdır. Girdi kayıtları değişmediği için özet denetimi
        atlanır (``force``). Emekli kaynağın tek başına verdiği öznitelikler silinmez (yıkıcı işlem yok, kural 4);
        değer kalır ama kazanan kaynağı olmaz — sayısı ``orphaned`` olarak döner, emeklilik önizlemesi bunu
        önceden gösterir (tools/build_drill.py).
        """
        self.registry.source(data_source)  # tanımsız kaynak açık hata
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ci_sys_id FROM ingest.identity_link WHERE data_source = %s AND active "
                        "ORDER BY 1", (data_source,))
            cis = [r[0] for r in cur.fetchall()]
        totals = {"cis": len(cis), "changes": 0, "orphaned": 0}
        for start in range(0, len(cis), batch):
            with self.conn.transaction(), self.conn.cursor() as cur:
                for ci in cis[start:start + batch]:
                    before = self._winner_keys(cur, ci)
                    totals["changes"] += len(self._reconcile(cur, ci, None, force=True))
                    totals["orphaned"] += len(before - self._winner_keys(cur, ci))
        return totals

    @staticmethod
    def _winner_keys(cur: psycopg.Cursor[Any], ci: str) -> set[str]:
        cur.execute("SELECT winners FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s", (ci,))
        row = cur.fetchone()
        return set(row[0]) if row and row[0] else set()

    def apply_requests(self, limit: int = 100) -> dict[str, list[ApplyResult]]:
        """Tüm insan isteklerini bir tur uygular: elle oluşturma, elle düzenleme, elle ilişki, sahiplik onayı, kuyruk
        kararı, ayırma (bu sırayla)."""
        return {"create": self.apply_manual_creates(limit), "manual": self.apply_manual_edits(limit),
                "relation": self.apply_manual_relations(limit), "attest": self.apply_attestations(limit),
                "decision": self.apply_decisions(limit), "split": self.apply_splits(limit)}

    # ------------------------------------------------------------------ sahiplik onayı (ADR-0027)
    def _write_attestation(self, ci: str, cls: str, values: dict[str, Any], actor: str) -> list[Any]:
        """Onay alanlarını CI'ın Manual kaynak kaydına yazar ve uzlaştırır (neden ``attestation``). Diğer elle
        değerler ve elle ilişkiler korunur."""
        with self.conn.cursor() as tcur:  # çağıranın imleci dict_row olabilir
            tcur.execute("SELECT attributes, relations FROM ingest.source_record WHERE data_source = 'Manual' "
                         "AND source_key = %s", (ci,))
            prev = tcur.fetchone()
        merged = {**(dict(prev[0]) if prev else {}), **values}
        record = IngestRecord(data_source="Manual", run_id=uuid4(), source_key=ci, class_hint=cls, attributes=merged,
                              relations=list(prev[1] or []) if prev else [], observed_at=self.clock())
        saved = self.actor
        self.actor = actor
        try:
            return self._process(record, attach_to=ci, method="manual", change_reason="attestation").changes
        finally:
            self.actor = saved

    def apply_attestations(self, limit: int = 100) -> list[ApplyResult]:
        """API'nin kaydettiği sahiplik onayı isteklerini uygular (ADR-0027).

        Uygulama anında yeniden denetlenir: CI kapsamda mı (politika), onay veren hâlâ sorumlu mu (``owner_fields``).
        Geçerse ``attestation_status``, ``attested``, ``attested_by``, ``attested_date`` Manual kaydına yazılır.
        Ret ``attestation_rejected`` anomalisi açar (CI başına tek açık); sonraki onay onu kapatır, kanıt kalır."""
        policy = self.attestation

        def handle(req: dict[str, Any]) -> ApplyResult:
            ci = req["ci_sys_id"]
            now = self.clock()
            with self.conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT to_jsonb(ci) AS doc FROM cmdb.cmdb_ci ci WHERE sys_id = %s FOR UPDATE", (ci,))
                doc = cur.fetchone()["doc"]  # type: ignore[index]
                cls = doc["sys_class_name"]
                pol = policy.policy_for(cls, doc, self.hierarchy)
                note = None
                if pol is None:
                    note = "CI sahiplik onayı kapsamında değil; onay uygulanmadı"
                elif req["attester_user"] not in policy.owners(doc):
                    note = "Onay veren artık bu CI'ın sorumlusu değil; onay uygulanmadı"
                if note:
                    cur.execute("""UPDATE ingest.attestation_request SET status = 'rejected', result_note = %s,
                                          applied_at = now() WHERE sys_id = %s""", (note, req["sys_id"]))
                    return ApplyResult("attest_rejected", ci, [], note)
                attested = req["decision"] == "attested"
                values = {"attestation_status": ATTESTED if attested else REJECTED, "attested": attested,
                          "attested_by": req["attester_user"], "attested_date": now.isoformat()}
                changes = self._write_attestation(ci, cls, values, req["requested_by"])
                assert pol is not None
                if attested:
                    until = (now + pol.period).astimezone(LOCAL_TZ).strftime("%d.%m.%Y")
                    note = f"Onaylandı; {pol.label_tr} politikasıyla {until} tarihine kadar geçerli"
                    cur.execute(
                        """UPDATE insight.anomaly SET status = 'resolved',
                                  explanation = explanation || ' Sorumlu kaydı sonradan onayladı.',
                                  details = details || %s
                            WHERE ci_sys_id = %s AND kind = 'attestation_rejected' AND status IN ('open', 'acknowledged')""",
                        (Jsonb({"resolution": "attested", "resolved_at": now.isoformat(),
                                "request": req["sys_id"]}), ci))
                else:
                    reason = REASONS_TR[req["reason"]]
                    note = f"Reddedildi ({reason}); Veri sorumlusuna anomali açıldı"
                    cur.execute("""SELECT 1 FROM insight.anomaly WHERE ci_sys_id = %s AND kind = 'attestation_rejected'
                                      AND status IN ('open', 'acknowledged')""", (ci,))
                    if cur.fetchone() is None:
                        cur.execute(
                            """INSERT INTO insight.anomaly (kind, severity, ci_sys_id, data_source, detected_at, details,
                                                            explanation)
                               VALUES ('attestation_rejected', 'medium', %s, 'Manual', %s, %s, %s)""",
                            (ci, now, Jsonb({"reason": req["reason"], "note": req["note"], "by": req["attester_user"],
                                             "request": req["sys_id"], "policy": pol.name}),
                             f"Sorumlu sahiplik onayını reddetti: {reason}. Gerekçe: {req['note']}"))
                    else:
                        note = f"Reddedildi ({reason}); açık anomali zaten var"
                cur.execute("""UPDATE ingest.attestation_request SET status = 'applied', result_note = %s,
                                      applied_at = now() WHERE sys_id = %s""", (note, req["sys_id"]))
            return ApplyResult("attest_applied", ci, changes, note)

        def fail(cur: psycopg.Cursor[Any], req: dict[str, Any], note: str) -> None:
            cur.execute("""UPDATE ingest.attestation_request SET status = 'rejected', result_note = %s,
                                  applied_at = now() WHERE sys_id = %s""", (note, req["sys_id"]))

        return self._drain("attest", """
            SELECT sys_id, ci_sys_id, decision, reason, note, attester_user, requested_by
              FROM ingest.attestation_request WHERE status = 'pending' ORDER BY requested_at, sys_id
             LIMIT 1 FOR UPDATE SKIP LOCKED""", handle, fail, limit)

    def expire_attestations(self, limit: int = 500) -> dict[str, int]:
        """Süresi dolan veya onaylayanı artık sorumlu olmayan onayı geçersiz kılar (ADR-0027 §5): kapsamdaki CI
        ``Not Yet Reviewed`` olur, ``attested`` false; ``attested_by``/``attested_date`` son onayın kanıtı olarak kalır.
        Sahip değişikliği reddi de sıfırlar (yeni sorumlu yeniden inceler). Yıkıcı değildir."""
        policy = self.attestation
        if not policy.enabled:
            return {"expired": 0, "owner_changed": 0}
        period, params = policy.period_sql(self.hierarchy)
        owners = policy.owners_sql()
        now = self.clock()
        counts = {"expired": 0, "owner_changed": 0}
        with self.conn.cursor() as cur:
            # Politika ifadesi yalnızca doğrulanmış tanımlayıcılardan kurulur; değerler parametredir.
            cur.execute(sql.SQL(
                """SELECT sys_id, sys_class_name, owner_changed FROM (
                        SELECT ci.sys_id, ci.sys_class_name, ci.attestation_status, ci.attested_date,
                               {period} AS period_days,
                               COALESCE(NOT (ci.attested_by::text = ANY({owners})), true) AS owner_changed
                          FROM cmdb.cmdb_ci ci
                         WHERE ci.attestation_status IN ('Attested', 'Rejected')) x
                     WHERE period_days IS NOT NULL
                       AND (owner_changed
                            OR attestation_status = 'Attested'
                               AND (attested_date IS NULL OR attested_date + make_interval(days => period_days) <= %s))
                     ORDER BY sys_id LIMIT %s""").format(period=sql.SQL(period), owners=sql.SQL(owners)),
                [*params, now, limit])
            due = cur.fetchall()
        for ci, cls, owner_changed in due:
            with self.conn.transaction():
                self._write_attestation(ci, cls, {"attestation_status": NOT_REVIEWED, "attested": False}, self.actor)
            counts["owner_changed" if owner_changed else "expired"] += 1
        return counts

    def _split_blocker(self, cur: psycopg.Cursor[Any], req: dict[str, Any]) -> str | None:
        cur.execute("SELECT active, ci_sys_id, method FROM ingest.identity_link WHERE sys_id = %s FOR UPDATE",
                    (req["link_sys_id"],))
        link = cur.fetchone()
        if link is None or not link[0] or link[1] != req["ci_sys_id"]:
            return "Bağ istekten sonra değişti; ayırma uygulanmadı"
        if req["data_source"] == "Manual":
            return "Elle giriş kaydı ayrılmaz; elle girilen değerler düzenleme ile kaldırılır"
        if link[2] == "import":
            return "Bu CI'ın sys_id'si bu kaynak kaydından geliyor (ilk yükleme); diğer kayıtları ayırın"
        cur.execute("SELECT count(*) FROM ingest.identity_link WHERE ci_sys_id = %s AND active AND sys_id <> %s "
                    "AND data_source <> 'Manual'", (req["ci_sys_id"], req["link_sys_id"]))
        if cur.fetchone()[0] == 0:  # type: ignore[index]
            return "CI'ın tek kaynak kaydı ayrılamaz"
        if req["target_ci"] is None and not self.registry.may_create_ci(req["data_source"]):
            return "Bu kaynak yeni CI açamaz (gölge/devir); hedef CI seçin"
        return None

    def _rebuild_profile(self, cur: psycopg.Cursor[Any], index: PostgresCandidateIndex, resolver: IdentityResolver,
                         ci: str) -> None:
        """Kimlik profilini CI'ın kalan etkin kaynak kayıtlarından sıfırdan kurar."""
        with self.conn.cursor(row_factory=dict_row) as dcur:
            dcur.execute(
                """SELECT sr.data_source, sr.source_key, sr.class_hint, sr.attributes, sr.lookup, sr.external_ids,
                          sr.relations, sr.last_seen, sr.last_run
                     FROM ingest.identity_link l JOIN ingest.source_record sr USING (data_source, source_key)
                    WHERE l.ci_sys_id = %s AND l.active ORDER BY sr.data_source, sr.source_key""", (ci,))
            rows = dcur.fetchall()
        cur.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s", (ci,))
        cls = cur.fetchone()[0]  # type: ignore[index]
        keys = None
        for row in rows:
            k = extract_keys(source_record_from_ingest(_record_from_row(row)))
            keys = k if keys is None else keys.merged(k)
        if keys is None:  # _split_blocker en az bir kalan bağ olmasını sağlar; yine de boş profil güvenlidir
            keys = keys_from_json({})
        index.replace(CandidateProfile(ci, cls, resolver.family(cls), keys))

    # ------------------------------------------------------------------ toplu yardımcı
    def apply_all(self, records: Iterable[IngestRecord]) -> list[ApplyResult]:
        return [self.apply(r) for r in records]
