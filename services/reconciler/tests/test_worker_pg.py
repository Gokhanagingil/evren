"""Uzlaştırıcı çalışanı (göç 0009): hata yalıtımı, geçici hata, sır sızmaması, yapılandırma, yetki sınırları.

Bu dosyadaki testler veriyi kalıcı yazmaz (işlem sonunda geri alınır). Çalışanın gerçek bağlantılarla bildirimle
uyanması ve düzgün kapanması veriyi kalıcı yazdığı için demo yüklemesinden sonra çalışan
``services/api/tests/test_worker_live.py`` içindedir.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from test_store_pg import fetch

from evren_core.envelope import IngestRecord
from evren_reconciler import ReconcilerStore
from evren_reconciler.worker import ConfigError, Worker, WorkerConfig


def test_unexpected_error_rejects_only_that_request(as_reconciler, metadata, monkeypatch):
    """Zehirli istek kuyruğu kilitlemez: yalnızca o isteğin değişiklikleri geri alınır, istek reddedilir."""
    store = ReconcilerStore(as_reconciler, metadata)
    run = store.start_run(uuid.uuid4(), "BMC")
    cis = [store.apply(IngestRecord(data_source="BMC", run_id=run, source_key=f"zh-{i}",
                                    class_hint="cmdb_ci_linux_server", observed_at=datetime.now(UTC),
                                    attributes={"name": f"zh-{i}", "serial_number": f"CZZH000{i}"})).ci_sys_id
           for i in range(2)]
    as_reconciler.execute("SET ROLE evren_app_api")
    for ci in cis:
        as_reconciler.execute("INSERT INTO ingest.manual_edit (ci_sys_id, attributes, requested_by) "
                              "VALUES (%s, '{\"used_for\": \"Test\"}', 't')", (ci,))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    bad = cis[0]
    original = ReconcilerStore._reconcile

    def explode(self, cur, ci, *args, **kwargs):
        if ci == bad:
            raise RuntimeError("beklenmeyen hata (test)")
        return original(self, cur, ci, *args, **kwargs)

    monkeypatch.setattr(ReconcilerStore, "_reconcile", explode)
    results = {r.ci_sys_id: r for r in store.apply_manual_edits()}
    assert results[bad].outcome == "manual_failed" and "hata kimliği" in results[bad].summary
    assert results[cis[1]].outcome == "manual"
    rows = dict(fetch(as_reconciler, "SELECT ci_sys_id, status FROM ingest.manual_edit WHERE ci_sys_id = ANY(%s)", cis))
    assert rows == {bad: "rejected", cis[1]: "applied"}
    # Kayıt noktası geri alındı: başarısız isteğin Manual kaynak kaydı yazılmadı, CI değişmedi
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.source_record WHERE data_source = 'Manual' "
                                "AND source_key = %s", bad)[0][0] == 0
    assert fetch(as_reconciler, "SELECT used_for FROM cmdb.cmdb_ci WHERE sys_id = %s", bad)[0][0] is None


def test_transient_database_error_leaves_request_pending(as_reconciler, metadata, monkeypatch):
    store = ReconcilerStore(as_reconciler, metadata)
    run = store.start_run(uuid.uuid4(), "BMC")
    ci = store.apply(IngestRecord(data_source="BMC", run_id=run, source_key="tr-1", class_hint="cmdb_ci_linux_server",
                                  observed_at=datetime.now(UTC), attributes={"name": "tr-1"})).ci_sys_id
    as_reconciler.execute("SET ROLE evren_app_api")
    as_reconciler.execute("INSERT INTO ingest.manual_edit (ci_sys_id, attributes, requested_by) "
                          "VALUES (%s, '{\"used_for\": \"Test\"}', 't')", (ci,))
    as_reconciler.execute("SET ROLE evren_app_reconciler")

    def transient(self, *args, **kwargs):
        raise psycopg.errors.DeadlockDetected("geçici (test)")

    monkeypatch.setattr(ReconcilerStore, "_process", transient)
    with pytest.raises(psycopg.OperationalError):
        store.apply_manual_edits()


def test_failed_decision_is_marked_not_applied(as_reconciler, metadata, monkeypatch):
    store = ReconcilerStore(as_reconciler, metadata)
    sn = store.start_run(uuid.uuid4(), "ServiceNow")
    a, b = "d" * 31 + "1", "d" * 31 + "2"
    for sid, serial in ((a, "CZDC00001"), (b, "CZDC00002")):
        store.import_record(IngestRecord(data_source="ServiceNow", run_id=sn, source_key=sid,
                                         class_hint="cmdb_ci_linux_server", observed_at=datetime.now(UTC),
                                         attributes={"name": "dc-dup", "serial_number": serial}), sid)
    zb = store.start_run(uuid.uuid4(), "Zabbix")
    assert store.apply(IngestRecord(data_source="Zabbix", run_id=zb, source_key="dc-z", class_hint="cmdb_ci_linux_server",
                                    observed_at=datetime.now(UTC), attributes={"name": "dc-dup"})).outcome == "review"
    as_reconciler.execute("SET ROLE evren_app_api")
    as_reconciler.execute("""UPDATE ingest.match_candidate SET status = 'accepted', decision = 'merge', decision_ci = %s,
                                    decided_by = 't', decided_at = now()
                              WHERE source_key = 'dc-z' AND status = 'open'""", (a,))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    monkeypatch.setattr(ReconcilerStore, "_attach", lambda *a, **k: (_ for _ in ()).throw(ValueError("test")))
    [res] = store.apply_decisions()
    assert res.outcome == "decision_failed"
    applied, error = fetch(as_reconciler, "SELECT applied_at IS NOT NULL, apply_error FROM ingest.match_candidate "
                                          "WHERE source_key = 'dc-z'")[0]
    assert applied and error.startswith("Uygulanamadı: beklenmeyen hata")
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_link WHERE source_key = 'dc-z'")[0][0] == 0


def test_connection_errors_never_leak_the_connection_string(metadata, caplog):
    canary = "-".join(("SIR", "parola", "12345"))  # çalışma anında kurulur (sır tarayıcısı için değil)
    attempts = []

    def refuse():
        attempts.append(1)
        if len(attempts) >= 2:
            worker.stop.set()
        raise psycopg.OperationalError(f"connection failed: password={canary}")

    worker = Worker(WorkerConfig(conninfo=f"postgresql://u:{canary}@h/db", worker_id="sizinti-testi"), metadata,
                    connect=refuse)
    with caplog.at_level(logging.DEBUG, logger="evren.reconciler"):
        worker.serve()
    assert len(attempts) == 2
    assert canary not in caplog.text and "OperationalError" in caplog.text
    assert canary not in repr(worker.config)


def test_config_from_env(tmp_path: Path):
    secret_file = tmp_path / "db"
    secret_file.write_text("postgresql://u:p@h/db\n", encoding="utf-8")
    cfg = WorkerConfig.from_env({"EVREN_DATABASE_URL_FILE": str(secret_file), "EVREN_WORKER_POLL_SECONDS": "5"})
    assert cfg.conninfo == "postgresql://u:p@h/db" and cfg.poll_seconds == 5 and cfg.role == "evren_app_reconciler"
    with pytest.raises(ConfigError):
        WorkerConfig.from_env({"EVREN_WORKER_POLL_SECONDS": "0"})
    with pytest.raises(ConfigError, match="okunamadı"):
        WorkerConfig.from_env({"EVREN_DATABASE_URL_FILE": str(tmp_path / "yok")})


@pytest.mark.parametrize(("role", "statement"), [
    ("evren_app_api", "INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds) "
                      "VALUES ('x', now(), now(), 30)"),
    ("evren_app_api", "UPDATE ingest.match_candidate SET apply_error = 'x'"),
    ("evren_readonly", "SELECT count(*) FROM ingest.worker_heartbeat"),
])
def test_worker_least_privilege(conn, role, statement):
    conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute(statement)
    conn.execute("RESET ROLE")


def test_rereconcile_cli(capsys):
    """Yaşam döngüsü değişikliği dağıtıldıktan sonra: --rereconcile KAYNAK (ADR-0015)."""
    from evren_reconciler.worker import main

    assert main(["--rereconcile", "Tanimsiz"]) == 2
    assert "Tanımsız veri kaynağı" in capsys.readouterr().err
    assert main(["--rereconcile", "ServiceNowChange"]) == 0  # CI'a bağlı kaydı olmayan kaynak: 0 CI
    assert "'cis': 0" in capsys.readouterr().out


def test_heartbeat_details_report_outbox_publisher_and_kafka(as_reconciler, metadata):
    """Sistem durumu (U8.1, ADR-0032): istek çalışanı olay yayıncısının açık olup olmadığını, alım tüketicisi Kafka
    bağlantısını ve gecikmesini nabız ayrıntısında bildirir (en az yetkili rolle yazılır)."""
    from evren_reconciler.worker import IngestWorker

    class FakeOutbox:
        pass

    tag = uuid.uuid4().hex[:6]
    worker = Worker(WorkerConfig(conninfo="", worker_id=f"hb-req-{tag}"), metadata, outbox=FakeOutbox())
    worker._heartbeat(as_reconciler)
    bare = Worker(WorkerConfig(conninfo="", worker_id=f"hb-bare-{tag}"), metadata)
    bare._heartbeat(as_reconciler)

    class Unreachable:
        def assignment(self):
            raise RuntimeError("broker yok")

    ingest = IngestWorker(WorkerConfig(conninfo="", worker_id=f"hb-ing-{tag}"), metadata,
                          consumer_factory=Unreachable, dlq=object())
    ingest._heartbeat(as_reconciler)  # tüketici henüz açılmadı
    ingest._consumer = Unreachable()
    ingest._heartbeat(as_reconciler)
    rows = dict(fetch(as_reconciler, "SELECT worker_id, details FROM ingest.worker_heartbeat WHERE worker_id LIKE %s",
                      f"hb-%-{tag}"))
    assert rows == {f"hb-req-{tag}": {"outbox_publisher": True}, f"hb-bare-{tag}": {"outbox_publisher": False},
                    f"hb-ing-{tag}": {"kafka": {"ok": False, "error": "RuntimeError"}}}
