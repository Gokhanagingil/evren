"""Uzlaştırıcı çalışanı, gerçek bağlantılarla (göç 0009): PostgreSQL bildirimiyle uyanma ve düzgün kapanış.

Çalışan kendi bağlantılarını açtığı için veri kalıcı yazılır; bu yüzden test demo verisi yüklendikten sonra
çalışan API takımındadır (tools/load_demo_pg.py boş veritabanı ister).
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from evren_core.envelope import IngestRecord
from evren_core.metadata import load_metadata
from evren_reconciler import ReconcilerStore
from evren_reconciler.worker import Worker, WorkerConfig

from .conftest import REPO


def _committed_ci(metadata, tag: str) -> str:
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, metadata)
        run = store.start_run(uuid.uuid4(), "BMC")
        return store.apply(IngestRecord(data_source="BMC", run_id=run, source_key=f"wk-{tag}",
                                        class_hint="cmdb_ci_linux_server", observed_at=datetime.now(UTC),
                                        attributes={"name": f"wk-{tag}", "serial_number": f"CZWK{tag}"})).ci_sys_id


def _committed_edit(ci: str, attributes: dict) -> str:
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_api")
        return conn.execute("INSERT INTO ingest.manual_edit (ci_sys_id, attributes, requested_by) "
                            "VALUES (%s, %s, 'calisan-testi') RETURNING sys_id", (ci, Jsonb(attributes))).fetchone()[0]


def _status(edit: str) -> str:
    with psycopg.connect(autocommit=True) as conn:
        return conn.execute("SELECT status FROM ingest.manual_edit WHERE sys_id = %s", (edit,)).fetchone()[0]


def _until(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_worker_wakes_on_notify_and_stops_cleanly():
    metadata = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    ci = _committed_ci(metadata, tag)
    first = _committed_edit(ci, {"used_for": "Test"})
    worker = Worker(WorkerConfig(conninfo="", poll_seconds=600, worker_id=f"test-{tag}"), metadata)
    thread = threading.Thread(target=worker.serve, daemon=True)
    thread.start()
    try:
        assert _until(lambda: _status(first) == "applied", 10), "açılışta bekleyen istek uygulanmalı"
        # Tarama aralığı 600 sn: ikinci isteğin hemen uygulanması yalnızca bildirimle mümkündür.
        second = _committed_edit(ci, {"used_for": "Production"})
        started = time.monotonic()
        assert _until(lambda: _status(second) == "applied", 5), "bildirim (LISTEN/NOTIFY) çalışanı uyandırmalı"
        assert time.monotonic() - started < 5
    finally:
        worker.stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive(), "durdurma isteği en geç birkaç saniyede sonuç vermeli"
    with psycopg.connect(autocommit=True) as conn:
        row = conn.execute("SELECT processed, failed, stopped_at IS NOT NULL, poll_seconds FROM ingest.worker_heartbeat "
                           "WHERE worker_id = %s", (f"test-{tag}",)).fetchone()
        used_for = conn.execute("SELECT used_for FROM cmdb.cmdb_ci WHERE sys_id = %s", (ci,)).fetchone()[0]
    assert row[0] >= 2 and row[1] == 0 and row[2] is True and row[3] == 600
    assert used_for == "Production"
