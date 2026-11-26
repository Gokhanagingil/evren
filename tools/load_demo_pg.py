#!/usr/bin/env python3
"""Sentetik demo envanterini GERÇEK uzlaştırıcıyla PostgreSQL'e yükler (F0-10 duman testi ve API geliştirme verisi).

``tools/build_demo.py`` ile aynı girdiyi (``feed``) kullanır; fark, kaydın bellek içi hat yerine
``evren_reconciler.ReconcilerStore`` üzerinden veritabanına yazılmasıdır. Böylece iki uygulamanın aynı
sonucu verdiği test edilebilir (services/reconciler/tests).

Varsayılan bağlantı libpq ortam değişkenleridir (PGHOST, PGPORT, PGUSER, PGDATABASE). Uzlaştırıcı
``SET ROLE evren_app_reconciler`` ile en az yetkiyle çalışır.

Kullanım: python tools/load_demo_pg.py [--role evren_app_reconciler]
UYARI: yalnızca boş bir geliştirme veritabanına yükleyin; sentetik veridir.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any

import psycopg
import yaml

from _bootstrap import REPO_ROOT, database_url

sys.path.insert(0, str(REPO_ROOT / "services" / "reconciler"))

from build_demo import NOW, feed
from evren_core.connectors import FieldMapping
from evren_core.envelope import IngestRecord
from evren_core.metadata import load_metadata
from evren_reconciler import ReconcilerStore

NS = uuid.UUID("6f1d0c5e-7b1a-4e55-9d4e-000000d3e0f0")


def run_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NS, name)


class PgSink:
    """``build_demo.Sink`` uygulaması: her veri kaynağı için bir çalışma açar, kayıtları uzlaştırıcıya verir."""

    def __init__(self, store: ReconcilerStore) -> None:
        self.store = store
        self.mappings: dict[str, FieldMapping] = {}
        for src in store.md.data_sources:
            for rel in src.get("mappings") or []:
                doc = yaml.safe_load((REPO_ROOT / rel).read_text(encoding="utf-8"))
                self.mappings[rel.split("/", 1)[1]] = FieldMapping.from_dict(doc)
        self.current: tuple[str, uuid.UUID] | None = None
        self.results: list[tuple[str, Any]] = []
        self.records_by_source: dict[str, list[IngestRecord]] = {}

    def _run_for(self, data_source: str, observed: datetime) -> uuid.UUID:
        if self.current and self.current[0] == data_source:
            return self.current[1]
        self.close()
        rid = run_id(f"{data_source}:1")
        self.store.start_run(rid, data_source, started_at=observed - timedelta(minutes=5))
        self.current = (data_source, rid)
        return rid

    def close(self) -> None:
        if self.current:
            self.store.finish_run(self.current[1], finished_at=NOW)
            self.current = None

    def ingest(self, mapping: str, raw: dict, observed_at: datetime, *, import_sys_id: str | None = None) -> None:
        m = self.mappings[mapping]
        rid = self._run_for(m.data_source, observed_at)
        rec = m.apply(raw, run_id=rid, observed_at=observed_at)
        if rec is None:
            return
        self.records_by_source.setdefault(rec.data_source, []).append(rec)
        res = self.store.import_record(rec, import_sys_id) if import_sys_id else self.store.apply(rec)
        self.results.append((rec.data_source, res))

    def manual(self, ci_sys_id: str, values: dict[str, Any], observed_at: datetime) -> None:
        self.close()
        rid = run_id(f"Manual:{ci_sys_id}")
        self.store.start_run(rid, "Manual", mode="delta", started_at=observed_at)
        with self.store.conn.cursor() as cur:
            cur.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s", (ci_sys_id,))
            cls = cur.fetchone()[0]  # type: ignore[index]
        rec = IngestRecord(data_source="Manual", run_id=rid, source_key=f"manual:{ci_sys_id}", class_hint=cls,
                           attributes=values, observed_at=observed_at)
        self.results.append(("Manual", self.store.attach_manual(rec, ci_sys_id)))
        self.store.finish_run(rid, finished_at=observed_at)


def seed_history(conn: psycopg.Connection[Any], counts: dict[str, int]) -> None:
    """Devre kesici taban çizgisi için geçmiş çalışmalar (sentetik; yalnızca demo)."""
    with conn.transaction(), conn.cursor() as cur:
        for ds, n in counts.items():
            for k in range(6, 0, -1):
                started = NOW - timedelta(days=k)
                cur.execute(
                    """INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, finished_at, completed,
                                                      record_count, changed_count, breaker_state, notes)
                       VALUES (%s, %s, 'snapshot', %s, %s, true, %s, 0, 'ok', 'sentetik geçmiş (demo)')""",
                    (run_id(f"{ds}:history:{k}"), ds, started, started + timedelta(minutes=9), n))


def seed_refs(conn: psycopg.Connection[Any], refs: dict[str, str], role: str | None) -> None:
    """Referans verileri (grup, kişi, konum, maliyet merkezi): üretimde sn-sync yansıtır; burada aynı rolle (evren_app_sync)."""
    # Çağıranın rolü ne olursa olsun (uzlaştırıcı ref.* tablolarına yazamaz) senkron rolüne geçilir, sonra geri dönülür.
    current = conn.execute("SELECT current_user").fetchone()[0]  # type: ignore[index]
    with conn.transaction():
        conn.execute("SET ROLE evren_app_sync")
        with conn.cursor() as cur:
            for key, sys_id in refs.items():
                kind, label = key.split(":", 1)
                if kind == "group":
                    cur.execute("INSERT INTO ref.sys_user_group (sys_id, name, source) VALUES (%s, %s, 'evren') "
                                "ON CONFLICT DO NOTHING", (sys_id, label))
                elif kind == "user":
                    user = label.split(" (")[0].lower().replace(" ", ".")
                    cur.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'evren') "
                                "ON CONFLICT DO NOTHING", (sys_id, user, label))
                elif kind == "location":
                    cur.execute("INSERT INTO ref.cmn_location (sys_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                (sys_id, label))
                elif kind == "cost_center":
                    cur.execute("INSERT INTO ref.cmn_cost_center (sys_id, name, source) VALUES (%s, %s, 'evren') "
                                "ON CONFLICT DO NOTHING", (sys_id, label))
        conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role or current)))


def load(conn: psycopg.Connection[Any], role: str | None = "evren_app_reconciler") -> dict[str, Any]:
    md = load_metadata(REPO_ROOT / "metadata")
    if role:
        conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
    store = ReconcilerStore(conn, md, clock=lambda: NOW)

    # 1) Geçmiş çalışmalar: kaynak başına beklenen kayıt sayısı (önce kuru bir sayım)
    class Counter:
        def __init__(self) -> None:
            self.mappings = PgSink(store).mappings
            self.counts: dict[str, int] = {}

        def ingest(self, mapping: str, raw: dict, observed_at: datetime, *, import_sys_id: str | None = None) -> None:
            m = self.mappings[mapping]
            if m.apply(raw, run_id=NS, observed_at=observed_at) is not None:
                self.counts[m.data_source] = self.counts.get(m.data_source, 0) + 1

        def manual(self, *_: Any) -> None:
            return None

    counter = Counter()
    feed(counter)
    seed_history(conn, counter.counts)

    # 2) Gerçek alım
    sink = PgSink(store)
    refs, _hosts = feed(sink)
    sink.close()
    seed_refs(conn, refs, role)

    # 3) NNMi'nin sonraki çalışması sayfalama ortasında kesildi (kısmi): devre kesici karantinaya almalı
    nnmi = sink.records_by_source.get("NNMi", [])
    rid = run_id("NNMi:2")
    store.start_run(rid, "NNMi", started_at=NOW - timedelta(hours=2))
    for rec in nnmi[: max(1, len(nnmi) * 2 // 5)]:
        store.apply(rec.model_copy(update={"run_id": rid, "observed_at": NOW - timedelta(hours=2)}))
    verdict = store.finish_run(rid, completed=False, finished_at=NOW - timedelta(hours=1, minutes=50))

    outcome: dict[str, int] = {}
    for _ds, res in sink.results:
        outcome[res.outcome] = outcome.get(res.outcome, 0) + 1
    return {"outcomes": outcome, "nnmi_partial": verdict.state.value}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--role", default="evren_app_reconciler", help="SET ROLE (boş: bağlantı kullanıcısı)")
    args = parser.parse_args(argv)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM cmdb.cmdb_ci")
            if cur.fetchone()[0]:  # type: ignore[index]
                print("cmdb.cmdb_ci boş değil; demo yalnızca boş geliştirme veritabanına yüklenir.", file=sys.stderr)
                return 2
        summary = load(conn, args.role or None)
    print(f"Yüklendi: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
