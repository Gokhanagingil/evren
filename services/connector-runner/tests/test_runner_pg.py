"""Bağlayıcı çalıştırıcısı (U5.3b, ADR-0021): gerçek PostgreSQL (``evren_app_connector`` rolü), gerçek alt süreç.

* Uçtan uca: API rolüyle açılan istek → çalıştırıcı → ``tools/connector_run.py --simulate`` → sahte alım geçidi →
  istek sonucu (sayılar, run_id geçitteki çalışmayla aynı).
* Hata notu yer tutucuyla temizlenir; süre sınırı ve iptal süreci sonlandırır; zamanlanmış dakika bir kez açılır;
  yanıt vermeyen çalıştırıcının isteği ve süresi dolan istek kapanır; emekli kaynağın isteği reddedilir.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)

import psycopg
from conftest import REPO

from evren_connector_runner import Runner, RunnerConfig, load_instances

PREFIX = "test-runner-"


def owner() -> psycopg.Connection:
    return psycopg.connect(autocommit=True)


@pytest.fixture(autouse=True)
def clean():
    yield
    with owner() as c:
        c.execute("DELETE FROM ingest.connector_run_request WHERE instance LIKE %s", (PREFIX + "%",))
        c.execute("DELETE FROM ingest.connector_instance WHERE name LIKE %s", (PREFIX + "%",))
        c.execute("DELETE FROM ingest.worker_heartbeat WHERE worker_id LIKE %s", (PREFIX + "%",))


@pytest.fixture
def token(tmp_path):
    p = tmp_path / "gw-token"
    p.write_text("evt_" + uuid.uuid4().hex)  # sentetik geçit belirteci (çalışma başına rastgele)
    return p


def make_runner(tmp_path, metadata, gateway_url, token, *, name="zbx", worker="r1", clock=None, command=None,
                **extra) -> Runner:
    f = tmp_path / f"{worker}.yaml"
    item = {"name": PREFIX + name, "connector": "zabbix", "gateway_token_file": str(token),
            "simulate": "connectors/zabbix/samples", **extra}
    import yaml
    f.write_text(yaml.safe_dump({"connectors": [item]}), encoding="utf-8")
    cfg = RunnerConfig.from_env({"EVREN_CONNECTORS_FILE": str(f), "EVREN_GATEWAY_URL": gateway_url,
                                 "EVREN_GATEWAY_ALLOW_HTTP": "1", "EVREN_CONNECTOR_ALLOW_SIMULATE": "1"},
                                conninfo="", worker_id=PREFIX + worker)
    kw = {"clock": clock} if clock else {}
    return Runner(cfg, metadata, load_instances(f, metadata, allow_simulate=True), command=command, **kw)


def api_request(instance: str, *, mode: str = "snapshot", by: str = "ayse") -> uuid.UUID:
    with owner() as c:
        c.execute("SET ROLE evren_app_api")
        return c.execute("""INSERT INTO ingest.connector_run_request (instance, data_source, mode, requested_by)
                            VALUES (%s, 'Zabbix', %s, %s) RETURNING id""", (instance, mode, by)).fetchone()[0]


def request_row(rid: uuid.UUID) -> dict:
    with owner() as c:
        cur = c.execute("""SELECT status, run_id, exit_code, summary, result_note, claimed_by, origin, mode, since
                             FROM ingest.connector_run_request WHERE id = %s""", (rid,))
        return dict(zip([d.name for d in cur.description], cur.fetchone(), strict=True))


def drive(runner: Runner, conn, timeout: float = 60) -> None:
    limit = time.monotonic() + timeout
    while runner.jobs and time.monotonic() < limit:
        runner.reap(conn)
        time.sleep(0.1)
    assert not runner.jobs, "çalışma süre içinde bitmedi"


def fake(script: str):
    """Gerçek bağlayıcı yerine küçük bir Python süreci (çalıştırıcının argümanları yok sayılır)."""
    return lambda _inst: [sys.executable, "-c", script]


def test_manual_request_runs_real_connector_through_gateway(tmp_path, metadata, gateway, token):
    runner = make_runner(tmp_path, metadata, gateway.url, token)
    with runner.open() as conn:
        runner.register(conn)
        rid = api_request(PREFIX + "zbx")
        assert runner.cycle(conn)["started"] == 1
        drive(runner, conn)
    row = request_row(rid)
    assert row["status"] == "succeeded" and row["exit_code"] == 0 and row["claimed_by"] == PREFIX + "r1"
    assert row["summary"]["sent"] == 5 and row["summary"]["completed"] is True
    assert row["summary"]["effective_mode"] == "snapshot" and row["result_note"] is None
    run = gateway.runs[str(row["run_id"])]  # geçitte açılan çalışma isteğin run_id'si
    assert run["records"] == 5 and run["complete"]["completed"] is True
    assert run["source"] == f"urn:evren:connector:{PREFIX}zbx"
    assert gateway.auth == {"Bearer " + token.read_text()}  # belirteç dosyadan
    with owner() as c:
        assert c.execute("""SELECT count(*) FROM audit.security_event WHERE action = 'connector_run_start'
                             AND resource = %s""", (f"connector_run_request:{rid}",)).fetchone()[0] == 1


def test_registered_instance_has_no_secret_or_address(tmp_path, metadata, gateway, token):
    runner = make_runner(tmp_path, metadata, gateway.url, token, schedule="*/15 * * * *",
                         clock=lambda: datetime(2026, 11, 26, 7, 1, tzinfo=UTC))
    with runner.open() as conn:
        runner.register(conn)
    with owner() as c:
        row = c.execute("SELECT * FROM ingest.connector_instance WHERE name = %s", (PREFIX + "zbx",)).fetchone()
    text = " ".join(str(v) for v in row)
    assert str(token) not in text and str(tmp_path) not in text and "127.0.0.1" not in text
    assert row[1:6] == ("zabbix", "Zabbix", "*/15 * * * *", "snapshot", "Europe/Istanbul")
    assert row[6] == datetime(2026, 11, 26, 7, 15, tzinfo=UTC)  # 10:01 İstanbul → sonraki 10:15


def test_child_environment_has_no_database_address(tmp_path, metadata, gateway, token, monkeypatch):
    monkeypatch.setenv("EVREN_DATABASE_URL", "postgresql://u:p@db/evren")
    monkeypatch.setenv("PGPASSWORD", "gizli")
    runner = make_runner(tmp_path, metadata, gateway.url, token)
    env = runner.child_env(runner.instances[PREFIX + "zbx"])
    assert "EVREN_DATABASE_URL" not in env and "PGPASSWORD" not in env and "EVREN_DATABASE_URL_FILE" not in env
    assert env["EVREN_CONNECTOR_TOKEN_FILE"] == str(token) and env["EVREN_GATEWAY_ALLOW_HTTP"] == "1"
    assert "EVREN_ARCHIVE_DIR" not in env  # arşiv kapalıysa aktarılmaz


def test_runner_archives_runs_and_prunes_expired(tmp_path, metadata, gateway, token):
    """ADR-0024: çalıştırıcı arşiv ayarını alt sürece aktarır; gerçek bağlayıcı çalışması şifreli arşiv yazar;
    saklama süresi dolan çalışma silinir."""
    from cryptography.fernet import Fernet

    from evren_core.connectors.archive import ArchiveWriter, load_keys, runs

    key = tmp_path / "archive.key"
    key.write_text(Fernet.generate_key().decode(), encoding="utf-8")
    root = tmp_path / "arsiv"
    runner = make_runner(tmp_path, metadata, gateway.url, token)
    runner.config = dataclasses.replace(runner.config, archive_dir=str(root), archive_key_file=str(key),
                                        archive_retention_days=30)
    env = runner.child_env(runner.instances[PREFIX + "zbx"])
    assert env["EVREN_ARCHIVE_DIR"] == str(root) and env["EVREN_ARCHIVE_KEY_FILE"] == str(key)
    with runner.open() as conn:
        runner.register(conn)
        rid = api_request(PREFIX + "zbx")
        runner.cycle(conn)
        drive(runner, conn)
    assert request_row(rid)["status"] == "succeeded"
    listed = runs(root)
    assert len(listed) == 1 and listed[0]["records"] == 5 and listed[0]["completed"] is True
    old = ArchiveWriter(root, "Zabbix", uuid.uuid4(), load_keys(key), started_at=datetime.now(UTC) - timedelta(days=60))
    old.close(completed=True)
    assert runner.prune_archive(force=True) == [str(old.dir.relative_to(root))]
    assert len(runs(root)) == 1


def test_failed_run_note_is_redacted(tmp_path, metadata, gateway, token):
    script = ("import sys; sys.stderr.write('Yapılandırma hatası: " + str(token)
              + " okunamadı; https://zabbix.kurum/api 401\\n'); sys.exit(2)")
    runner = make_runner(tmp_path, metadata, gateway.url, token, command=fake(script))
    with runner.open() as conn:
        runner.register(conn)
        rid = api_request(PREFIX + "zbx")
        runner.cycle(conn)
        drive(runner, conn)
    row = request_row(rid)
    assert row["status"] == "failed" and row["exit_code"] == 2
    assert str(token) not in row["result_note"] and "zabbix.kurum" not in row["result_note"]
    assert row["result_note"].startswith("Yapılandırma hatası: ‹yapılandırma› okunamadı; ‹adres›")


def test_timeout_and_cancel_terminate_the_process(tmp_path, metadata, gateway, token):
    runner = make_runner(tmp_path, metadata, gateway.url, token, command=fake("import time; time.sleep(60)"))
    with runner.open() as conn:
        runner.register(conn)
        rid = api_request(PREFIX + "zbx")
        runner.cycle(conn)
        runner.jobs[rid].deadline = time.monotonic() - 1  # süre sınırı doldu
        drive(runner, conn, timeout=20)
        row = request_row(rid)
        assert row["status"] == "failed" and "Süre sınırı aşıldı" in row["result_note"]
        rid2 = api_request(PREFIX + "zbx")
        runner.cycle(conn)
        with owner() as c:  # arayüzden iptal: API yalnızca işlevle
            c.execute("SET ROLE evren_app_api")
            assert c.execute("SELECT ingest.cancel_connector_run_request(%s, 'ayse')", (rid2,)).fetchone()[0] == \
                "running"
        drive(runner, conn, timeout=20)
    row = request_row(rid2)
    assert row["status"] == "cancelled" and row["summary"]["duration_s"] < 20


def test_scheduler_opens_a_minute_once_across_runners_and_skips_while_active(tmp_path, metadata, gateway, token):
    now = [datetime(2026, 11, 26, 7, 15, tzinfo=UTC)]  # 10:15 İstanbul
    a = make_runner(tmp_path, metadata, gateway.url, token, worker="a", schedule="*/15 * * * *",
                    clock=lambda: now[0])
    b = make_runner(tmp_path, metadata, gateway.url, token, worker="b", schedule="*/15 * * * *",
                    clock=lambda: now[0])
    with a.open() as ca, b.open() as cb:
        a.register(ca)
        assert a.schedule(ca) == 1 and b.schedule(cb) == 0  # aynı dakika iki kez açılmaz
        now[0] += timedelta(minutes=15)
        assert a.schedule(ca) == 0  # önceki istek bekliyor: yeni istek yok
        with owner() as c:
            rows = c.execute("""SELECT origin, scheduled_for, requested_by FROM ingest.connector_run_request
                                 WHERE instance = %s""", (PREFIX + "zbx",)).fetchall()
        assert rows == [("schedule", datetime(2026, 11, 26, 7, 15, tzinfo=UTC), "zamanlayici")]
        assert a.schedule(ca) == 0  # aynı dakikada ikinci tur


def test_scheduler_catches_up_only_the_last_slot(tmp_path, metadata, gateway, token):
    now = [datetime(2026, 11, 26, 7, 1, tzinfo=UTC)]
    r = make_runner(tmp_path, metadata, gateway.url, token, schedule="*/15 * * * *", clock=lambda: now[0])
    with r.open() as conn:
        r.register(conn)
        assert r.schedule(conn) == 0
        now[0] = datetime(2026, 11, 26, 7, 50, tzinfo=UTC)  # 15, 30, 45 kaçırıldı
        assert r.schedule(conn) == 1
    with owner() as c:
        assert c.execute("SELECT scheduled_for FROM ingest.connector_run_request WHERE instance = %s",
                         (PREFIX + "zbx",)).fetchone()[0] == datetime(2026, 11, 26, 7, 45, tzinfo=UTC)


def test_delta_schedule_uses_last_success(tmp_path, metadata, gateway, token):
    now = [datetime(2026, 11, 26, 7, 0, tzinfo=UTC)]
    ok = fake("print('{\"fetched\": 1, \"sent\": 1, \"completed\": true}')")
    r = make_runner(tmp_path, metadata, gateway.url, token, schedule="0 * * * *", schedule_mode="delta",
                    clock=lambda: now[0], command=ok)
    with r.open() as conn:
        r.register(conn)
        assert r.schedule(conn) == 1  # başarılı çalışma yok → tam liste
        first = conn.execute("SELECT id, mode FROM ingest.connector_run_request WHERE instance = %s",
                             (PREFIX + "zbx",)).fetchone()
        assert first[1] == "snapshot"
        r.claim(conn)
        drive(r, conn)
        started = conn.execute("SELECT started_at FROM ingest.connector_run_request WHERE id = %s",
                               (first[0],)).fetchone()[0]
        now[0] += timedelta(hours=1)
        assert r.schedule(conn) == 1
        mode, since = conn.execute("""SELECT mode, since FROM ingest.connector_run_request WHERE instance = %s
                                       AND status = 'pending'""", (PREFIX + "zbx",)).fetchone()
    assert mode == "delta" and since == started - timedelta(minutes=10)


def test_recovery_of_unresponsive_runner_and_expired_request(tmp_path, metadata, gateway, token):
    r = make_runner(tmp_path, metadata, gateway.url, token)
    with r.open() as conn:
        r.register(conn)
    with owner() as c:
        c.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind)
                     VALUES (%s, now() - interval '2 hours', now() - interval '1 hour', 15, 'connector')""",
                  (PREFIX + "olu",))
        stale = c.execute("""INSERT INTO ingest.connector_run_request (instance, data_source, requested_by)
                             VALUES (%s, 'Zabbix', 'x') RETURNING id""", (PREFIX + "zbx",)).fetchone()[0]
        c.execute("""UPDATE ingest.connector_run_request SET status = 'running', claimed_by = %s,
                            started_at = now() - interval '1 hour', run_id = gen_random_uuid() WHERE id = %s""",
                  (PREFIX + "olu", stale))
    with r.open() as conn:
        assert r.recover(conn) == 1
    assert request_row(stale)["status"] == "failed"
    assert "yanıt vermiyor" in request_row(stale)["result_note"]
    with owner() as c:
        old = c.execute("""INSERT INTO ingest.connector_run_request (instance, data_source, requested_by, requested_at)
                           VALUES (%s, 'Zabbix', 'x', now() - interval '25 hours') RETURNING id""",
                        (PREFIX + "zbx",)).fetchone()[0]
    with r.open() as conn:
        assert r.recover(conn) == 1
    assert request_row(old)["status"] == "rejected" and "Süresi doldu" in request_row(old)["result_note"]


def test_retired_source_and_disabled_instance_are_rejected(tmp_path, metadata, gateway, token):
    r = make_runner(tmp_path, metadata, gateway.url, token, enabled=False)
    with r.open() as conn:
        r.register(conn)
        rid = api_request(PREFIX + "zbx")
        assert r.claim(conn) == 0
    row = request_row(rid)
    assert row["status"] == "rejected" and "devre dışı" in row["result_note"]
    r = make_runner(tmp_path, metadata, gateway.url, token, worker="r2")
    try:
        with owner() as c:
            c.execute("UPDATE ingest.data_source SET lifecycle = 'retired' WHERE name = 'Zabbix'")
        with r.open() as conn:
            r.register(conn)
            rid = api_request(PREFIX + "zbx")
            assert r.claim(conn) == 0
    finally:
        with owner() as c:
            c.execute("UPDATE ingest.data_source SET lifecycle = 'active' WHERE name = 'Zabbix'")
    row = request_row(rid)
    assert row["status"] == "rejected" and "emekliye" in row["result_note"]


def test_runner_role_cannot_touch_cmdb(tmp_path, metadata, gateway, token):
    r = make_runner(tmp_path, metadata, gateway.url, token)
    with r.open() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM cmdb.cmdb_ci")
    assert Path(REPO / "tools" / "connector_run.py").is_file()
