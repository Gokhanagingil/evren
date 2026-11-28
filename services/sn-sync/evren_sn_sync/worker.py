"""sn-sync çalışanı: ServiceNow replikasına itme (U9.1, F2-01, ADR-0042).

Tur (``poll_seconds``'ta bir veya ``LISTEN evren_replica`` bildirimiyle hemen):

1. **Mod:** ``sync.replica_setting``. Kapalıysa yalnızca nabız yazılır. ServiceNow bağlantısı yoksa hiçbir şey
   gönderilmez.
2. **Adaylar:** kapsamdaki (``sync.sn_sync_scope``, alt sınıflarıyla) ve son itmeden/denemeden sonra değişen CI'lar.
   Yük özetiyle karşılaştırılır; aynı içerik tekrar gönderilmez.
3. **Gölge:** yük ServiceNow'un işlemeden deneme uç noktasına gider (``/identifyreconcile/query``). Sonuç
   (``ok`` | ``conflict`` | ``error``) eşlemeye yazılır; ServiceNow'a hiçbir şey yazılmaz.
4. **Etkin:** toplu değişiklik freni — aday sayısı sınırı aşarsa tur **bekletilir**, yetkili kişi onaylayınca
   gönderilir. Eşlemesi doğrulanmamış CI önce denenir; yalnızca beklenen kayıtla eşleşenler yazılır (önce doğrula,
   sonra yaz). Yazılan CI'ın ServiceNow sys_id'si eşlemeye yazılır.
5. **İlişkiler** (etkin): iki ucu da eşit olan ilişki ``relations`` ile gönderilir. Evren'de kalkan ilişki
   "kaldırıldı" işaretlenir (IRE ilişki silmez; ServiceNow'da kaldırılması işletim prosedürü).

Çalışan CMDB'ye yazamaz (``evren_app_sync``). Hata iletileri sır ve sunucu gövdesi içermez.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from evren_core.connectors.http import HttpClient, HttpError, Transport, UrllibTransport
from evren_core.metadata import load_metadata
from evren_core.replica import (
    ALWAYS_EXCLUDED,
    BuiltItem,
    CiSnapshot,
    IreClient,
    IreSimulator,
    ReplicaField,
    Verdict,
    build_item,
    judge,
)

from .config import ReplicaConfig

__all__ = ["CHANNEL", "ReplicaWorker"]

log = logging.getLogger("evren.sn_sync")
CHANNEL = "evren_replica"
REPO_ROOT = Path(__file__).resolve().parents[3]
#: Referans tabloları (ServiceNow'dan aynı sys_id ile gelir). ``source`` sütunu olanlarda yalnızca ServiceNow kaynaklı
#: satır gönderilir; Evren'de veya Entra'da doğan kişinin/grubun ServiceNow'da karşılığı yoktur.
REF_TABLES = {"sys_user": True, "sys_user_group": True, "cmn_cost_center": True, "cmn_location": False,
              "core_company": False, "cmn_department": False, "cmdb_model": False}
#: Yeniden deneme aralığı (dakika): 1, 2, 4, … en çok 6 saat
MAX_BACKOFF_MIN = 360


def _chunks(seq: Sequence[Any], n: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _endpoint(index: dict[str, int], items: list[dict[str, Any]], ci: str, cls: str, name: str, source: str) -> int:
    """İlişki ucunun yükteki sırası; uç kaynak anahtarıyla tanınır, yalnızca sınıf ve ad gönderilir."""
    if ci not in index:
        index[ci] = len(items)
        items.append({"className": cls, "internal_id": ci,
                      "values": {"sys_class_name": cls, **({"name": name} if name else {})},
                      "sys_object_source_info": {"source_name": source, "source_native_key": ci}})
    return index[ci]


SAME_IDENTITY = ("Aynı seri numarası veya sınıf + ad ile başka bir Evren CI'ı da ServiceNow'a gidiyor; ServiceNow "
                 "ikisini tek kayıtta birleştirirdi")


def _identity_keys(item: dict[str, Any]) -> set[str]:
    values = item.get("values") or {}
    keys = set()
    if str(values.get("serial_number") or "").strip():
        keys.add("serial:" + str(values["serial_number"]).strip().lower())
    if str(values.get("name") or "").strip():
        keys.add(f"name:{item.get('className')}:" + str(values["name"]).strip().lower())
    return keys


def _split_same_identity(items: list[tuple[BuiltItem, dict[str, Any]]]) -> tuple[list[Any], list[Any]]:
    seen: set[str] = set()
    first, later = [], []
    for b, r in items:
        keys = _identity_keys(b.item)
        if keys & seen:
            later.append((b, r))
        else:
            seen |= keys
            first.append((b, r))
    return first, later


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=min(2 ** max(attempts - 1, 0), MAX_BACKOFF_MIN))


class ReplicaWorker:
    def __init__(self, config: ReplicaConfig, *, transport: Transport | None = None,
                 connect: Callable[[], psycopg.Connection[Any]] | None = None) -> None:
        self.config = config
        self.stop = threading.Event()
        self.started_at = time.time()
        self.processed = 0
        self.failed = 0
        self._connect = connect or (lambda: psycopg.connect(config.conninfo))
        self._last_cycle = 0.0
        sn = load_metadata(REPO_ROOT / "metadata").sn_sync or {}
        self.source_name = config.source_name or str(sn.get("source_name") or "Evren")
        self.excluded = set(ALWAYS_EXCLUDED) | set(sn.get("exclude_fields") or [])
        self.simulator: IreSimulator | None = None
        self._seeded = False
        if transport is None and config.simulate:
            self.simulator = IreSimulator(allowed_sources=(self.source_name,))
            transport = self.simulator
        self.transport = transport
        self.client: IreClient | None = None
        if transport is not None or config.sn_url:
            if config.sn_token:
                auth = HttpClient.bearer(config.sn_token)
            elif config.sn_user and config.sn_password:
                auth = HttpClient.basic(config.sn_user, config.sn_password)
            else:  # simülatör veya test
                auth = HttpClient.basic("evren-replica", "simulator")
            http = HttpClient(config.sn_url or "https://servicenow.simulator", auth_header=auth, retries=2, backoff=0.2,
                              transport=transport or UrllibTransport(ca_file=config.sn_ca_file,
                                                                     allow_http=config.sn_allow_http))
            self.client = IreClient(http, self.source_name)
        self._fields: dict[str, list[ReplicaField]] = {}
        self._refs: dict[tuple[str, str], str | None] = {}

    # ------------------------------------------------------------------ bağlantı
    def open(self) -> psycopg.Connection[Any]:
        conn = self._connect()
        conn.autocommit = True
        if self.config.role:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.config.role)))
        conn.execute("SET application_name = 'evren-sn-sync'")
        return conn

    @property
    def connection(self) -> str:
        if self.simulator is not None:
            return "simülatör"
        return "servicenow" if self.client else "yok"

    # ------------------------------------------------------------------ kapsam ve yük
    def fields_for(self, conn: psycopg.Connection[Any], class_name: str) -> list[ReplicaField]:
        """Sınıfın ServiceNow'a giden alanları: sözlükte ``sn_sync``, ServiceNow'da da var olan (Evren'e özgü
        olmayan) ve kısıtlı olmayan alanlar; dışlananlar hariç."""
        if class_name not in self._fields:
            rows = conn.execute(
                """SELECT d.element, d.internal_type, d.reference
                     FROM meta.class_closure cc JOIN meta.sys_dictionary d ON d.name = cc.ancestor
                    WHERE cc.descendant = %s AND d.active AND d.sn_sync AND d.origin <> 'evren'
                      AND d.sensitivity <> 'restricted'
                    ORDER BY d.element""", (class_name,)).fetchall()
            self._fields[class_name] = [ReplicaField(r[0], r[1], r[2]) for r in rows if r[0] not in self.excluded]
        return self._fields[class_name]

    def ref_value(self, conn: psycopg.Connection[Any], table: str, sys_id: str) -> str | None:
        key = (table, sys_id)
        if key not in self._refs:
            value: str | None = None
            if table in REF_TABLES:
                q = sql.SQL("SELECT 1 FROM ref.{} WHERE sys_id = %s").format(sql.Identifier(table))
                if REF_TABLES[table]:
                    q = sql.SQL("{} AND source = 'servicenow'").format(q)
                value = sys_id if conn.execute(q, (sys_id,)).fetchone() else None
            elif table.startswith("cmdb_ci"):
                row = conn.execute("SELECT sn_sys_id FROM sync.sn_ci_map WHERE evren_sys_id = %s AND state = 'in_sync'",
                                   (sys_id,)).fetchone()
                value = row[0] if row and row[0] else None
            self._refs[key] = value
        return self._refs[key]

    _SCOPE = """WITH scope AS (SELECT DISTINCT cc.descendant AS cls FROM sync.sn_sync_scope s
                                 JOIN meta.class_closure cc ON cc.ancestor = s.sys_class_name
                                WHERE s.enabled AND (s.include_descendants OR cc.depth = 0))"""
    _BASE = """ FROM cmdb.cmdb_ci c JOIN scope ON scope.cls = c.sys_class_name
                LEFT JOIN sync.sn_ci_map m ON m.evren_sys_id = c.sys_id
               WHERE c.duplicate_of IS NULL
                 AND coalesce(m.state, 'pending_push') NOT IN ('excluded', 'conflict')
                 AND (m.next_try_at IS NULL OR m.next_try_at <= now())"""
    # Değişiklik CI'ın sürümüyle (golden_version) izlenir: zaman damgası karşılaştırmasındaki yarış (itme sırasında
    # başlayıp sonra işlenen güncelleme) değişikliği kaçırmaz.
    _ACTIVE = " AND (m.evren_sys_id IS NULL OR m.state <> 'in_sync' OR m.pushed_version IS DISTINCT FROM c.golden_version)"
    _SHADOW = " AND (m.evren_sys_id IS NULL OR m.shadow_version IS DISTINCT FROM c.golden_version)"

    def candidates(self, conn: psycopg.Connection[Any], mode: str,
                   after: tuple[Any, str] | None = None) -> list[dict[str, Any]]:
        """Değişmiş olabilecek CI'lar, (güncelleme zamanı, sys_id) sırasıyla sayfa sayfa."""
        pred = self._ACTIVE if mode == "active" else self._SHADOW
        page = " AND (c.sys_updated_on, c.sys_id) > (%s, %s)" if after else ""
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._SCOPE + """ SELECT c.*, m.evren_sys_id AS m_ci, m.sn_sys_id AS m_sn, m.state AS m_state,
                                                 m.accepted_sn_sys_id AS m_accepted,
                                                 m.last_pushed_hash AS m_hash, m.shadow_hash AS m_shadow_hash,
                                                 m.attempts AS m_attempts""" + self._BASE + pred + page +
                        " ORDER BY c.sys_updated_on, c.sys_id LIMIT %s", (*(after or ()), self.config.limit))
            return cur.fetchall()

    def collect(self, conn: psycopg.Connection[Any], mode: str, stop_at: int,
                t: dict[str, int]) -> tuple[list[tuple[BuiltItem, dict[str, Any]]], bool]:
        """Gerçekten değişen CI'lar (yük özeti farklı). İçeriği aynı olanın yalnızca görülen sürümü ilerler; böylece
        kapsam dışı bir alanın toplu değişmesi toplu değişiklik frenini tetiklemez. ``stop_at`` değişiklik bulununca
        durur; ikinci değer daha fazlası olabileceğini söyler."""
        changed: list[tuple[BuiltItem, dict[str, Any]]] = []
        after: tuple[Any, str] | None = None
        while True:
            rows = self.candidates(conn, mode, after)
            for r in rows:
                b = self.build(conn, r)
                same = b.hash == (r["m_hash"] if mode == "active" else r["m_shadow_hash"])
                if same and (mode == "shadow" or r["m_state"] == "in_sync"):
                    col = "pushed_version" if mode == "active" else "shadow_version"
                    conn.execute(sql.SQL("UPDATE sync.sn_ci_map SET {} = %s WHERE evren_sys_id = %s").format(
                        sql.Identifier(col)), (r["golden_version"], b.evren_sys_id))
                    t["skipped_same"] += 1
                    continue
                changed.append((b, r))
                if len(changed) >= stop_at:
                    return changed, True
            if len(rows) < self.config.limit:
                return changed, False
            after = (rows[-1]["sys_updated_on"], rows[-1]["sys_id"])

    def build(self, conn: psycopg.Connection[Any], row: dict[str, Any]) -> BuiltItem:
        attrs = row.get("class_attributes") or {}
        values = {**{k: v for k, v in row.items() if not k.startswith("m_")}, **attrs}
        ci = CiSnapshot(row["sys_id"], row["sys_class_name"], values, row["sys_updated_on"])
        return build_item(ci, self.fields_for(conn, ci.sys_class_name), source_name=self.source_name,
                          ref_value=lambda t, v: self.ref_value(conn, t, v))

    # ------------------------------------------------------------------ eşleme
    @staticmethod
    def sn_origin(conn: psycopg.Connection[Any], ids: Sequence[str]) -> set[str]:
        """ServiceNow'dan aynı sys_id ile gelmiş CI'lar (anahtarı CI sys_id'sine eşit etkin kaynak bağı)."""
        rows = conn.execute("SELECT DISTINCT ci_sys_id FROM ingest.identity_link WHERE active AND ci_sys_id = ANY(%s) "
                            "AND source_key = ci_sys_id", (list(ids),)).fetchall()
        return {r[0] for r in rows}

    @staticmethod
    def taken_by_other(conn: psycopg.Connection[Any], sn_sys_id: str | None, me: str) -> bool:
        if not sn_sys_id:
            return False
        return conn.execute(
            """SELECT EXISTS (SELECT 1 FROM sync.sn_ci_map WHERE sn_sys_id = %(sn)s AND evren_sys_id <> %(me)s)
                   OR EXISTS (SELECT 1 FROM cmdb.cmdb_ci WHERE sys_id = %(sn)s AND sys_id <> %(me)s)""",
            {"sn": sn_sys_id, "me": me}).fetchone()[0]  # type: ignore[index]

    def record(self, conn: psycopg.Connection[Any], b: BuiltItem, v: Verdict, *, commit: bool, run: int,
               attempts: int, version: int | None = None) -> None:
        common = {"ci": b.evren_sys_id, "cls": b.class_name, "op": v.operation or None, "found": v.sn_sys_id,
                  "msg": v.message[:300] or None, "skipped": list(b.skipped) or None, "hash": b.hash, "run": run,
                  "ver": version}
        if not commit:
            conn.execute(
                """INSERT INTO sync.sn_ci_map (evren_sys_id, sn_class, state, shadow_verdict, shadow_operation,
                                              shadow_hash, shadow_at, shadow_version, identified_sn_sys_id, message,
                                              skipped_fields, last_run, updated_at)
                   VALUES (%(ci)s, %(cls)s, 'pending_push', %(verdict)s, %(op)s, %(hash)s, now(), %(ver)s, %(found)s,
                           %(msg)s, %(skipped)s, %(run)s, now())
                   ON CONFLICT (evren_sys_id) DO UPDATE SET sn_class = EXCLUDED.sn_class,
                     shadow_version = EXCLUDED.shadow_version,
                     shadow_verdict = EXCLUDED.shadow_verdict, shadow_operation = EXCLUDED.shadow_operation,
                     shadow_hash = EXCLUDED.shadow_hash, shadow_at = now(),
                     identified_sn_sys_id = EXCLUDED.identified_sn_sys_id, message = EXCLUDED.message,
                     skipped_fields = EXCLUDED.skipped_fields, last_run = EXCLUDED.last_run, updated_at = now()""",
                {**common, "verdict": v.state})
            return
        if v.state == "ok":
            conn.execute(
                """INSERT INTO sync.sn_ci_map (evren_sys_id, sn_sys_id, sn_class, state, last_pushed_hash,
                                              last_pushed_at, pushed_version, attempts, last_error, last_operation,
                                              identified_sn_sys_id, message, skipped_fields, next_try_at, last_run,
                                              updated_at)
                   VALUES (%(ci)s, %(found)s, %(cls)s, 'in_sync', %(hash)s, now(), %(ver)s, 0, NULL, %(op)s, NULL, NULL,
                           %(skipped)s, NULL, %(run)s, now())
                   ON CONFLICT (evren_sys_id) DO UPDATE SET sn_sys_id = EXCLUDED.sn_sys_id,
                     pushed_version = EXCLUDED.pushed_version,
                     sn_class = EXCLUDED.sn_class, state = 'in_sync', last_pushed_hash = EXCLUDED.last_pushed_hash,
                     last_pushed_at = now(), attempts = 0, last_error = NULL, last_operation = EXCLUDED.last_operation,
                     identified_sn_sys_id = NULL, accepted_sn_sys_id = NULL, message = NULL, skipped_fields = EXCLUDED.skipped_fields,
                     next_try_at = NULL, last_run = EXCLUDED.last_run, updated_at = now()""", common)
            return
        state = "conflict" if v.state == "conflict" else "error"
        nxt = datetime.now(UTC) + _backoff(attempts + 1) if state == "error" else None
        conn.execute(
            """INSERT INTO sync.sn_ci_map (evren_sys_id, sn_class, state, attempts, last_error, last_operation,
                                          identified_sn_sys_id, message, skipped_fields, next_try_at, last_run,
                                          updated_at)
               VALUES (%(ci)s, %(cls)s, %(state)s, %(att)s, %(msg)s, %(op)s, %(found)s, %(msg)s, %(skipped)s,
                       %(next)s, %(run)s, now())
               ON CONFLICT (evren_sys_id) DO UPDATE SET state = EXCLUDED.state, attempts = EXCLUDED.attempts,
                 last_error = EXCLUDED.last_error, last_operation = EXCLUDED.last_operation,
                 identified_sn_sys_id = EXCLUDED.identified_sn_sys_id, message = EXCLUDED.message,
                 skipped_fields = EXCLUDED.skipped_fields, next_try_at = EXCLUDED.next_try_at,
                 last_run = EXCLUDED.last_run, updated_at = now()""",
            {**common, "state": state, "att": attempts + 1, "next": nxt})

    # ------------------------------------------------------------------ çalışmalar
    @staticmethod
    def setting(conn: psycopg.Connection[Any]) -> tuple[str, int]:
        row = conn.execute("SELECT mode, max_changes FROM sync.replica_setting WHERE id = 1").fetchone()
        return (row[0], int(row[1])) if row else ("off", 200)

    def open_run(self, conn: psycopg.Connection[Any], mode: str, count: int, max_changes: int,
                 more: bool = False) -> int | None:
        """Bu turun çalışma kaydı. Etkin modda sınırı aşan değişiklik onay bekler (``held``); onaylı tur sürer."""
        if mode == "active":
            row = conn.execute("SELECT id, status FROM sync.replica_run WHERE status IN ('held', 'approved')").fetchone()
            if row and row[1] == "approved":
                return int(row[0])
            if row and count <= max_changes:
                # Bekleyen değişiklik sınırın altına indi (ör. kapsam daraltıldı): bekletme kalkar, olağan tur sürer
                conn.execute("""UPDATE sync.replica_run SET status = 'done', finished_at = now(),
                                       held_reason = 'Bekleyen değişiklik sınırın altına indi; onaysız sürdü'
                                 WHERE id = %s""", (row[0],))
                row = None
            if count > max_changes:
                reason = (f"{'En az ' if more else ''}{count} CI değişikliği tur sınırını ({max_changes}) aşıyor; "
                          "onay bekliyor")
                if row:
                    conn.execute("UPDATE sync.replica_run SET candidates = %s, held_reason = %s WHERE id = %s",
                                 (count, reason, row[0]))
                else:
                    conn.execute("""INSERT INTO sync.replica_run (mode, status, candidates, held_reason, worker)
                                    VALUES ('active', 'held', %s, %s, %s)""", (count, reason, self.config.worker_id))
                    log.warning("toplu itme bekletildi: %s", reason)
                return None
        row = conn.execute("""INSERT INTO sync.replica_run (mode, status, candidates, worker)
                              VALUES (%s, 'running', %s, %s) RETURNING id""", (mode, count, self.config.worker_id))
        return int(row.fetchone()[0])  # type: ignore[index]

    # ------------------------------------------------------------------ tur
    def seed_simulator(self, conn: psycopg.Connection[Any]) -> None:
        """Simülatörde ServiceNow'da zaten var olanlar: Evren'in ServiceNow kaynaklı CI'ları (aynı sys_id)."""
        if self.simulator is None or self._seeded:
            return
        rows = conn.execute(
            """SELECT c.sys_id, c.sys_class_name, coalesce(c.name, ''), coalesce(c.serial_number, '')
                 FROM cmdb.cmdb_ci c
                WHERE EXISTS (SELECT 1 FROM ingest.identity_link l WHERE l.active AND l.ci_sys_id = c.sys_id
                                AND l.source_key = c.sys_id)""").fetchall()
        for sys_id, cls, name, serial in rows:
            if sys_id not in self.simulator.cis:
                self.simulator.seed(sys_id, cls, name=name, **({"serial_number": serial} if serial else {}))
        # Önceki itmelerle ServiceNow'a gitmiş kayıtlar (çalışan yeniden başlasa da simülatör onları bilir)
        pushed = conn.execute(
            """SELECT m.sn_sys_id, coalesce(m.sn_class, c.sys_class_name), m.evren_sys_id, coalesce(c.name, ''),
                      coalesce(c.serial_number, '')
                 FROM sync.sn_ci_map m JOIN cmdb.cmdb_ci c ON c.sys_id = m.evren_sys_id
                WHERE m.state = 'in_sync' AND m.sn_sys_id IS NOT NULL""").fetchall()
        for sn_id, cls, ci, name, serial in pushed:
            self.simulator.seed(sn_id, cls, source=(self.source_name, ci), name=name,
                                **({"serial_number": serial} if serial else {}))
        self._seeded = True

    def cycle(self, conn: psycopg.Connection[Any]) -> dict[str, int]:
        self._last_cycle = time.monotonic()
        self._fields.clear()
        self._refs.clear()
        totals = {"sent": 0, "inserted": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "errors": 0, "relations": 0,
                  "held": 0, "skipped_same": 0}
        mode, max_changes = self.setting(conn)
        failure: str | None = None
        if mode != "off" and self.client is not None:
            self.seed_simulator(conn)
            approved = mode == "active" and conn.execute(
                "SELECT EXISTS (SELECT 1 FROM sync.replica_run WHERE status = 'approved')").fetchone()[0]
            limit = self.config.limit
            stop_at = limit if mode == "shadow" or approved else max(limit, max_changes + 1)
            changed, more = self.collect(conn, mode, stop_at, totals)
            run = self.open_run(conn, mode, len(changed), max_changes, more) if changed else None
            if changed and run is None:
                totals["held"] = 1
            if not changed and mode == "active":
                # Onaylı veya bekletilmiş toplu itmenin gönderilecek bir şeyi kalmadı
                conn.execute("""UPDATE sync.replica_run SET status = 'done', finished_at = now()
                                 WHERE status IN ('held', 'approved')""")
            if run is None and mode == "active" and not totals["held"]:
                run = self.relations_run(conn)       # CI değişikliği yok ama gidecek ilişki var
            if run is not None:
                try:
                    self.push(conn, mode, run, changed[:limit], totals)
                    if mode == "active":
                        self.push_relations(conn, run, totals)
                except HttpError as exc:
                    failure = str(exc)[:300]
                    log.warning("ServiceNow'a gönderilemedi: %s", failure)
                self.close_run(conn, run, totals, failure, more or len(changed) > limit)
        if mode == "active":
            conn.execute("""UPDATE sync.sn_rel_map x SET state = 'removed', removed_at = now(), updated_at = now()
                             WHERE x.state <> 'removed'
                               AND NOT EXISTS (SELECT 1 FROM cmdb.cmdb_rel_ci r WHERE r.sys_id = x.evren_rel)""")
        self.heartbeat(conn, failed_now=failure is not None)
        if self.config.health_file:
            Path(self.config.health_file).touch()
        return totals

    def relations_run(self, conn: psycopg.Connection[Any]) -> int | None:
        """CI değişikliği olmayan turda gidecek ilişki varsa çalışma kaydı açar (ör. ilişki sonradan eklendi)."""
        pending = conn.execute(
            """SELECT EXISTS (SELECT 1 FROM cmdb.cmdb_rel_ci r
                               JOIN sync.sn_ci_map mp ON mp.evren_sys_id = r.parent AND mp.state = 'in_sync'
                               JOIN sync.sn_ci_map mc ON mc.evren_sys_id = r.child AND mc.state = 'in_sync'
                               LEFT JOIN sync.sn_rel_map x ON x.evren_rel = r.sys_id
                              WHERE x.evren_rel IS NULL
                                 OR (x.state = 'error' AND (x.next_try_at IS NULL OR x.next_try_at <= now())))""",
        ).fetchone()[0]  # type: ignore[index]
        if not pending:
            return None
        return int(conn.execute("""INSERT INTO sync.replica_run (mode, status, worker) VALUES ('active', 'running', %s)
                                   RETURNING id""", (self.config.worker_id,)).fetchone()[0])  # type: ignore[index]

    def close_run(self, conn: psycopg.Connection[Any], run: int, t: dict[str, int], failure: str | None,
                  remaining: bool = False) -> None:
        status = conn.execute("SELECT status FROM sync.replica_run WHERE id = %s", (run,)).fetchone()[0]  # type: ignore[index]
        new_status = "failed" if failure else "done"
        if status == "approved" and not failure and remaining:
            new_status = "approved"  # onaylı toplu itme sonraki turda sürer
        conn.execute(
            """UPDATE sync.replica_run SET status = %(st)s, finished_at = CASE WHEN %(st)s <> 'approved' THEN now() END,
                      sent = sent + %(sent)s, inserted = inserted + %(ins)s, updated = updated + %(upd)s,
                      unchanged = unchanged + %(unc)s, conflicts = conflicts + %(conf)s, errors = errors + %(err)s,
                      relations = relations + %(rel)s, last_error = %(fail)s
                WHERE id = %(id)s""",
            {"st": new_status, "sent": t["sent"], "ins": t["inserted"], "upd": t["updated"], "unc": t["unchanged"],
             "conf": t["conflicts"], "err": t["errors"], "rel": t["relations"], "fail": failure, "id": run})

    def push(self, conn: psycopg.Connection[Any], mode: str, run: int, items: list[tuple[BuiltItem, dict[str, Any]]],
             t: dict[str, int]) -> None:
        if not items:
            return
        origin = self.sn_origin(conn, [b.evren_sys_id for b, _r in items])
        # Beklenen ServiceNow kaydı: eşleme, insanın kabul ettiği kayıt (çelişki kararı) veya ServiceNow kökenli CI'da
        # aynı sys_id; hiçbiri yoksa CI Evren'de doğmuştur ve yeni kayıt beklenir
        expected = {b.evren_sys_id: (r["m_sn"] or r["m_accepted"]
                                     or (b.evren_sys_id if b.evren_sys_id in origin else None)) for b, r in items}
        verified = [(b, r) for b, r in items if r["m_state"] == "in_sync" and r["m_sn"]]
        unverified = [(b, r) for b, r in items if not (r["m_state"] == "in_sync" and r["m_sn"])]
        # Aynı kimlik anahtarını (seri, sınıf + ad) taşıyan ikinci CI aynı turda gönderilmez: ilki yazılınca
        # ServiceNow ikinciyi onunla eşleştirir. Gölgede bu çelişki olarak öngörülür; etkin modda ikinci CI ertelenir ve
        # sonraki turun denemesi çelişkiyi yazmadan önce yakalar.
        first, later = _split_same_identity(unverified)
        if mode == "shadow":
            self._send(conn, verified + first, expected, run, t, commit=False)
            for b, r in later:
                self.record(conn, b, Verdict("conflict", "INSERT", None, SAME_IDENTITY), commit=False, run=run,
                            attempts=int(r["m_attempts"] or 0))
                t["conflicts"] += 1
            return
        # Önce doğrula: eşlemesi doğrulanmamış CI ServiceNow'da denenir; yalnızca beklenen kayıtla eşleşen yazılır
        passed = self._send(conn, first, expected, run, t, commit=False, probe=True)
        self._send(conn, verified + passed, expected, run, t, commit=True)

    def _send(self, conn: psycopg.Connection[Any], items: list[tuple[BuiltItem, dict[str, Any]]],
              expected: dict[str, str | None], run: int, t: dict[str, int], *, commit: bool,
              probe: bool = False) -> list[tuple[BuiltItem, dict[str, Any]]]:
        """Toplu gönderim. ``probe``: etkin moddaki ön deneme — yalnızca çelişki/hata kaydedilir, geçenler döner."""
        assert self.client is not None
        passed: list[tuple[BuiltItem, dict[str, Any]]] = []
        for batch in _chunks(items, self.config.batch):
            try:
                results, _ = self.client.send([b.item for b, _r in batch], commit=commit)
            except HttpError:
                for b, r in batch:
                    self.record(conn, b, Verdict("error", "", None, "ServiceNow'a ulaşılamadı"), commit=commit or probe,
                                run=run, attempts=int(r["m_attempts"] or 0))
                    t["errors"] += 1
                self.failed += 1
                raise
            for i, (b, r) in enumerate(batch):
                res = results.get(i)
                found = res.sys_id if res else None
                v = judge(res, expected[b.evren_sys_id], taken_by_other=self.taken_by_other(conn, found, b.evren_sys_id),
                          committed=commit)
                ver = int(r["golden_version"])
                if probe:
                    if v.state == "ok":
                        passed.append((b, r))
                        continue
                    self.record(conn, b, v, commit=True, run=run, attempts=int(r["m_attempts"] or 0), version=ver)
                else:
                    self.record(conn, b, v, commit=commit, run=run, attempts=int(r["m_attempts"] or 0), version=ver)
                    t["sent"] += 1
                    self.processed += 1
                if v.state == "conflict":
                    t["conflicts"] += 1
                elif v.state == "error":
                    t["errors"] += 1
                elif v.operation == "INSERT":
                    t["inserted"] += 1
                elif v.operation in ("UPDATE", "UPDATE_WITH_UPGRADE", "UPDATE_WITH_DOWNGRADE", "UPDATE_WITH_SWITCH"):
                    t["updated"] += 1
                else:
                    t["unchanged"] += 1
        return passed

    def push_relations(self, conn: psycopg.Connection[Any], run: int, t: dict[str, int]) -> None:
        """İki ucu da replikada eşit olan, henüz gitmemiş ilişkiler. Uçlar kaynak anahtarıyla tanınır; yalnızca ad ve
        sınıf gönderilir (diğer alanlar değişmez)."""
        assert self.client is not None
        rows = conn.execute(
            """SELECT r.sys_id, r.parent, r.child, t.name, mp.sn_class, mc.sn_class, coalesce(pc.name, ''),
                      coalesce(cc.name, ''), coalesce(x.attempts, 0)
                 FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                 JOIN sync.sn_ci_map mp ON mp.evren_sys_id = r.parent AND mp.state = 'in_sync'
                 JOIN sync.sn_ci_map mc ON mc.evren_sys_id = r.child AND mc.state = 'in_sync'
                 JOIN cmdb.cmdb_ci pc ON pc.sys_id = r.parent JOIN cmdb.cmdb_ci cc ON cc.sys_id = r.child
                 LEFT JOIN sync.sn_rel_map x ON x.evren_rel = r.sys_id
                WHERE x.evren_rel IS NULL OR (x.state = 'error' AND (x.next_try_at IS NULL OR x.next_try_at <= now()))
                ORDER BY r.sys_updated_on LIMIT %s""", (self.config.limit,)).fetchall()
        per_batch = max(1, self.config.batch // 2)
        for batch in _chunks(rows, per_batch):
            index: dict[str, int] = {}
            items: list[dict[str, Any]] = []
            rels = [{"parent": _endpoint(index, items, p, pcls, pn, self.source_name),
                     "child": _endpoint(index, items, c, ccls, cn, self.source_name), "type": tname}
                    for _rid, p, c, tname, pcls, ccls, pn, cn, _a in batch]
            _items, results = self.client.send(items, rels, commit=True)
            for i, (rid, p, c, tname, *_rest, attempts) in enumerate(batch):
                res = results.get(i)
                ok = res is not None and not res.errors
                msg = None if ok else ((res.errors[0][1] or res.errors[0][0]) if res and res.errors
                                       else "ServiceNow yanıtında bu ilişki yok")[:300]
                conn.execute(
                    """INSERT INTO sync.sn_rel_map (evren_rel, parent, child, type_name, state, attempts, message,
                                                   pushed_at, next_try_at, updated_at)
                       VALUES (%(r)s, %(p)s, %(c)s, %(t)s, %(st)s, %(att)s, %(msg)s,
                               CASE WHEN %(ok)s THEN now() END, %(next)s, now())
                       ON CONFLICT (evren_rel) DO UPDATE SET state = EXCLUDED.state, attempts = EXCLUDED.attempts,
                         message = EXCLUDED.message, pushed_at = coalesce(EXCLUDED.pushed_at, sync.sn_rel_map.pushed_at),
                         next_try_at = EXCLUDED.next_try_at, updated_at = now()""",
                    {"r": rid, "p": p, "c": c, "t": tname, "st": "in_sync" if ok else "error",
                     "att": 0 if ok else attempts + 1, "msg": msg, "ok": ok,
                     "next": None if ok else datetime.now(UTC) + _backoff(attempts + 1)})
                if ok:
                    t["relations"] += 1
                else:
                    t["errors"] += 1

    # ------------------------------------------------------------------ nabız
    def heartbeat(self, conn: psycopg.Connection[Any], *, stopped: bool = False, failed_now: bool = False) -> None:
        mode, max_changes = self.setting(conn)
        states = dict(conn.execute("SELECT state, count(*) FROM sync.sn_ci_map GROUP BY state").fetchall())
        held = conn.execute("SELECT count(*) FROM sync.replica_run WHERE status = 'held'").fetchone()[0]  # type: ignore[index]
        details = {"connection": self.connection, "mode": mode, "max_changes": max_changes, "source": self.source_name,
                   "states": {k: int(v) for k, v in states.items()}, "held": int(held)}
        conn.execute(
            """INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed, failed,
                                                   last_error_at, poll_seconds, kind, details)
               VALUES (%(id)s, to_timestamp(%(started)s), now(), CASE WHEN %(stopped)s THEN now() END, %(processed)s,
                       %(failed)s, CASE WHEN %(failed_now)s THEN now() END, %(poll)s, 'sn_sync', %(details)s)
               ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(),
                 stopped_at = CASE WHEN %(stopped)s THEN now() END,
                 processed = EXCLUDED.processed, failed = EXCLUDED.failed,
                 last_error_at = COALESCE(EXCLUDED.last_error_at, ingest.worker_heartbeat.last_error_at),
                 poll_seconds = EXCLUDED.poll_seconds, details = EXCLUDED.details""",
            {"id": self.config.worker_id, "started": self.started_at, "stopped": stopped, "processed": self.processed,
             "failed": self.failed, "failed_now": failed_now, "poll": self.config.poll_seconds,
             "details": Jsonb(details)})

    # ------------------------------------------------------------------ döngü
    def run_once(self) -> dict[str, int]:
        with self.open() as conn:
            totals = self.cycle(conn)
            self.heartbeat(conn, stopped=True)
        return totals

    def serve(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                with self.open() as conn:
                    conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(CHANNEL)))
                    log.info("sn-sync hazır: kimlik=%s bağlantı=%s", self.config.worker_id, self.connection)
                    backoff = 1.0
                    self.cycle(conn)
                    while not self.stop.is_set():
                        notified = any(True for _ in conn.notifies(timeout=1.0, stop_after=1))
                        if notified or time.monotonic() - self._last_cycle >= self.config.poll_seconds:
                            self.cycle(conn)
                    self.heartbeat(conn, stopped=True)
            except psycopg.OperationalError as exc:
                log.warning("veritabanı bağlantısı yok (%s); %.0f sn sonra yeniden denenecek", type(exc).__name__,
                            backoff)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        log.info("sn-sync durdu: gönderilen=%d başarısız=%d", self.processed, self.failed)
