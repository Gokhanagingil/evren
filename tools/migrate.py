#!/usr/bin/env python3
"""Veritabanı kurulumu ve güncellemesi (U1): izlenen göçler + metadata tohumu + servis giriş rolleri.

    python tools/migrate.py                 # bekleyen göçleri uygula, metadata'yı tohumla
    python tools/migrate.py --status        # uygulanan / bekleyen göçleri listele
    python tools/migrate.py --baseline 0011_change_sets   # psql ile kurulmuş veritabanını deftere al
    python tools/migrate.py --service-roles /run/secrets/   # servis giriş rollerini oluştur/parolala

* Göçler ``db/migrations/NNNN_*.sql`` ad sırasıyla, her biri bir kez uygulanır; ``meta.schema_migration``
  tablosu sürümü ve SHA-256 özetini tutar. Uygulanmış bir göçün içeriği değişmişse kurulum DURUR (sürüklenme):
  göçler değiştirilmez, yeni göç yazılır.
* Metadata tohumu (``load_metadata`` → ``generate_seed_sql``) her çalıştırmada yeniden uygulanır; yayınlanmış
  değişiklik setleri (``metadata/changes``) dahildir (ADR-0015).
* Servis giriş rolleri: her servis kendi LOGIN rolüyle bağlanır ve yalnızca kendi grup rolünü üstlenir (en az
  yetki, 0003). Parolalar dosyadan okunur (``<dizin>/<rol>``), günlüğe yazılmaz.
* Bağlantı: ``EVREN_DATABASE_URL_FILE`` / ``EVREN_DATABASE_URL`` veya libpq ortam değişkenleri (sahip rolü).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from _bootstrap import REPO_ROOT, database_url
from evren_core.metadata import generate_seed_sql, load_metadata, validate

MIGRATIONS = REPO_ROOT / "db" / "migrations"
#: Servis giriş rolü → üstlendiği grup rolü (0003_security.sql)
SERVICE_ROLES = {
    "evren_api_svc": "evren_app_api",
    "evren_worker_svc": "evren_app_reconciler",
    "evren_ingest_svc": "evren_app_reconciler",   # Kafka alım tüketicisi (U2): uzlaştırıcıyla aynı yetki, ayrı kimlik
    "evren_gateway_svc": "evren_app_ingest",
    "evren_indexer_svc": "evren_app_indexer",     # arama dizinleyicisi (U3): okur, yalnızca nabzını yazar
    "evren_connector_svc": "evren_app_connector",  # bağlayıcı çalıştırıcı (U5.3b): istek durumu, CMDB yetkisi yok
    "evren_sync_svc": "evren_app_sync",
    "evren_mcp_svc": "evren_app_mcp",
}
LEDGER = """CREATE TABLE IF NOT EXISTS meta.schema_migration (
  version    text PRIMARY KEY,
  sha256     text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT now()
)"""


class MigrationError(RuntimeError):
    pass


def files() -> list[tuple[str, Path, str]]:
    out = []
    for p in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        out.append((p.stem, p, hashlib.sha256(p.read_bytes()).hexdigest()))
    return out


def _has_schema(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('meta.sys_db_object') IS NOT NULL").fetchone()[0]


def ledger(conn: psycopg.Connection) -> dict[str, str]:
    if conn.execute("SELECT to_regnamespace('meta') IS NULL").fetchone()[0]:
        return {}
    conn.execute(LEDGER)
    return dict(conn.execute("SELECT version, sha256 FROM meta.schema_migration").fetchall())


def plan(conn: psycopg.Connection, *, baseline: str | None = None) -> list[tuple[str, Path, str]]:
    applied = ledger(conn)
    if baseline is not None:
        # Göç aracından önce psql ile kurulmuş veritabanı: verilen sürüme KADAR olan göçleri uygulanmış say (bir kez,
        # açıkça). Sürüm verilmesi zorunludur: veritabanı depodan geride olabilir, sonraki göçler uygulanmalıdır.
        known = [v for v, _p, _d in files()]
        if baseline not in known:
            raise MigrationError(f"Temel çizgi sürümü depoda yok: {baseline}")
        if applied:
            raise MigrationError("Göç defteri zaten dolu; temel çizgi yalnızca defteri olmayan veritabanına uygulanır")
        if not _has_schema(conn):
            raise MigrationError("Veritabanında Evren şeması yok; temel çizgi yerine normal kurulum yapın")
        with conn.transaction():
            conn.execute(LEDGER)
            for version, _p, digest in files():
                if version <= baseline:
                    conn.execute("INSERT INTO meta.schema_migration (version, sha256) VALUES (%s, %s)",
                                 (version, digest))
        applied = ledger(conn)
    drift = [v for v, _p, d in files() if v in applied and applied[v] != d]
    if drift:
        raise MigrationError("Uygulanmış göçlerin içeriği değişmiş (göçler değiştirilmez, yeni göç yazılır): "
                             + ", ".join(drift))
    unknown = sorted(set(applied) - {v for v, _p, _d in files()})
    if unknown:
        raise MigrationError("Veritabanında depoda olmayan göç kaydı var (eski sürüm mü?): " + ", ".join(unknown))
    return [f for f in files() if f[0] not in applied]


def migrate(conn: psycopg.Connection, *, baseline: str | None = None) -> list[str]:
    done = []
    for version, path, digest in plan(conn, baseline=baseline):
        # Göç dosyaları kendi BEGIN/COMMIT bloklarını içerir; defter kaydı göçün hemen ardından yazılır.
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute(LEDGER)
        conn.execute("INSERT INTO meta.schema_migration (version, sha256) VALUES (%s, %s)", (version, digest))
        done.append(version)
    return done


def seed(conn: psycopg.Connection) -> None:
    md = load_metadata(REPO_ROOT / "metadata")
    report = validate(md)
    if report.errors:
        raise MigrationError("Metadata geçersiz: " + "; ".join(report.errors))
    conn.execute(generate_seed_sql(md))


def service_roles(conn: psycopg.Connection, secret_dir: Path) -> list[str]:
    """Parola dosyası olan her servis için LOGIN rolü oluşturur/günceller ve grup rolünü verir."""
    created = []
    for login, group in SERVICE_ROLES.items():
        pfile = secret_dir / login
        if not pfile.is_file():
            continue
        password = pfile.read_text(encoding="utf-8").strip()
        if len(password) < 24:
            raise MigrationError(f"{login}: parola en az 24 karakter olmalı")
        with conn.transaction():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(verb + " ROLE {} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION "
                                 "CONNECTION LIMIT 50 PASSWORD {}").format(sql.Identifier(login), sql.Literal(password)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(login)))
            dbname = conn.info.dbname
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname),
                                                                              sql.Identifier(login)))
        created.append(login)
    return created


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", help="uygulanan ve bekleyen göçleri listele")
    ap.add_argument("--baseline", metavar="SÜRÜM",
                    help="psql ile kurulmuş veritabanında bu sürüme kadarki göçleri uygulanmış say (bir kez), "
                         "ör. 0011_change_sets")
    ap.add_argument("--no-seed", action="store_true", help="metadata tohumunu atla")
    ap.add_argument("--service-roles", metavar="DİZİN", type=Path, help="servis giriş rolleri için parola dizini")
    args = ap.parse_args(argv)
    try:
        with psycopg.connect(database_url(), autocommit=True) as conn:
            if args.status:
                pending = {v for v, _p, _d in plan(conn)}
                for version, _p, _d in files():
                    print(f"{'bekliyor ' if version in pending else 'uygulandı'}  {version}")
                return 0
            done = migrate(conn, baseline=args.baseline)
            print(f"Göç: {len(done)} uygulandı" + (f" ({', '.join(done)})" if done else " (güncel)"))
            if not args.no_seed:
                seed(conn)
                print("Metadata tohumu uygulandı.")
            if args.service_roles:
                roles = service_roles(conn, args.service_roles)
                print(f"Servis giriş rolleri: {', '.join(roles) or 'parola dosyası yok'}")
    except (MigrationError, psycopg.Error) as exc:
        # psycopg iletileri bağlantı ayrıntısı içerebilir: yalnızca sınıf ve kendi iletimiz.
        msg = str(exc) if isinstance(exc, MigrationError) else type(exc).__name__
        print(f"Kurulum başarısız: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
