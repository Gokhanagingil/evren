"""Göç aracı (U1, tools/migrate.py): izlenen göçler, sürüklenme tespiti, temel çizgi, servis giriş rolleri.

Her test kendi geçici veritabanını açar ve siler. ``EVREN_TEST_PG=1`` ve süper kullanıcı bağlantısı gerekir.
"""
from __future__ import annotations

import os
import secrets
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))

if not os.environ.get("EVREN_TEST_PG"):
    pytest.skip("EVREN_TEST_PG tanımlı değil", allow_module_level=True)
psycopg = pytest.importorskip("psycopg")

import migrate  # noqa: E402


@pytest.fixture
def scratch(monkeypatch):
    name = f"evren_mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dbname="postgres", autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    monkeypatch.setenv("EVREN_DATABASE_URL", f"dbname={name}")
    monkeypatch.delenv("EVREN_DATABASE_URL_FILE", raising=False)
    try:
        yield name
    finally:
        with psycopg.connect(dbname="postgres", autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_fresh_install_then_idempotent(scratch, capsys):
    assert migrate.main([]) == 0
    out = capsys.readouterr().out
    assert f"Göç: {len(migrate.files())} uygulandı" in out and "Metadata tohumu uygulandı" in out
    assert migrate.main([]) == 0
    assert "Göç: 0 uygulandı (güncel)" in capsys.readouterr().out
    with psycopg.connect(dbname=scratch) as c:
        assert c.execute("SELECT count(*) FROM meta.schema_migration").fetchone()[0] == len(migrate.files())
        assert c.execute("SELECT count(*) FROM meta.sys_db_object").fetchone()[0] > 30


def test_drift_and_unknown_versions_stop_the_install(scratch, capsys):
    assert migrate.main(["--no-seed"]) == 0
    capsys.readouterr()
    with psycopg.connect(dbname=scratch, autocommit=True) as c:
        c.execute("UPDATE meta.schema_migration SET sha256 = repeat('0', 64) WHERE version = '0001_core'")
    assert migrate.main(["--no-seed"]) == 1
    assert "içeriği değişmiş" in capsys.readouterr().err
    with psycopg.connect(dbname=scratch, autocommit=True) as c:
        c.execute("UPDATE meta.schema_migration SET sha256 = %s WHERE version = '0001_core'",
                  (dict((v, d) for v, _p, d in migrate.files())["0001_core"],))
        c.execute("INSERT INTO meta.schema_migration (version, sha256) VALUES ('9999_gelecek', repeat('a', 64))")
    assert migrate.main(["--no-seed"]) == 1
    assert "depoda olmayan göç" in capsys.readouterr().err


def test_baseline_marks_only_up_to_given_version(scratch, capsys):
    """psql ile kurulmuş, depodan GERİDE kalmış veritabanı: temel çizgiden sonraki göçler yine uygulanır."""
    versions = migrate.files()
    upto = versions[-2][0]
    with psycopg.connect(dbname=scratch, autocommit=True) as c:
        for v, path, _d in versions:
            if v <= upto:
                c.execute(path.read_text(encoding="utf-8"))
        c.execute("DROP TABLE IF EXISTS meta.schema_migration")
    assert migrate.main(["--baseline", "yok_0000", "--no-seed"]) == 1
    assert "Temel çizgi sürümü depoda yok" in capsys.readouterr().err
    assert migrate.main(["--baseline", upto, "--no-seed"]) == 0
    assert f"Göç: 1 uygulandı ({versions[-1][0]})" in capsys.readouterr().out
    assert migrate.main(["--baseline", upto, "--no-seed"]) == 1  # defter doluyken ikinci temel çizgi reddedilir
    assert "defteri zaten dolu" in capsys.readouterr().err


def test_service_login_roles_get_only_their_group_role(scratch, tmp_path):
    pw = secrets.token_urlsafe(32)
    (tmp_path / "evren_api_svc").write_text(pw, encoding="utf-8")
    (tmp_path / "evren_worker_svc").write_text("kisa", encoding="utf-8")  # 24 karakterden kısa → ret
    assert migrate.main(["--no-seed", "--service-roles", str(tmp_path)]) == 1
    (tmp_path / "evren_worker_svc").write_text(secrets.token_urlsafe(32), encoding="utf-8")
    assert migrate.main(["--no-seed", "--service-roles", str(tmp_path)]) == 0
    with psycopg.connect(dbname=scratch) as c:
        rows = dict(c.execute("""SELECT m.rolname, string_agg(g.rolname, ',') FROM pg_roles m
                                   JOIN pg_auth_members a ON a.member = m.oid JOIN pg_roles g ON g.oid = a.roleid
                                  WHERE m.rolname IN ('evren_api_svc', 'evren_worker_svc') GROUP BY 1""").fetchall())
        inherit = dict(c.execute("SELECT rolname, rolinherit FROM pg_roles WHERE rolname LIKE 'evren_%_svc'").fetchall())
    assert rows == {"evren_api_svc": "evren_app_api", "evren_worker_svc": "evren_app_reconciler"}
    assert inherit["evren_api_svc"] is False  # yetki yalnız SET ROLE ile (uygulama bağlantıda rolüne geçer)
