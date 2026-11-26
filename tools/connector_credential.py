#!/usr/bin/env python3
"""Alım geçidi bağlayıcı kimlikleri (U2, ADR-0017): ver, listele, iptal et. Arayüzden yönetim U5'te gelir.

    python tools/connector_credential.py issue --name zabbix-dc1 --data-source Zabbix --by ahmet.yilmaz
    python tools/connector_credential.py list
    python tools/connector_credential.py revoke --name zabbix-dc1 --by ahmet.yilmaz

* Belirteç yalnızca ``issue`` çıktısında BİR KEZ görünür; veritabanında SHA-256 özeti tutulur. Kaybedilen belirteç
  kurtarılamaz: iptal edip yenisini verin. Belirteci kurum sır kasasına koyun; günlüğe, bilet veya sohbete yazmayın.
* ``--token-file DOSYA``: belirteci ekrana değil, yalnızca sahibinin okuyabildiği (0600) dosyaya yazar (otomasyon).
* Her kimlik tek bir veri kaynağına bağlıdır; emekli kaynağa kimlik verilmez. Her işlem ``audit.security_event``'e
  yazılır. Bağlantı yönetici rolüyledir (``EVREN_DATABASE_URL_FILE`` / libpq ortamı).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from _bootstrap import REPO_ROOT, database_url
from evren_core.credentials import new_token, token_hint, token_sha256
from evren_core.metadata import load_metadata
from evren_core.sources import SourceRegistryError


def _audit(cur: psycopg.Cursor, by: str, action: str, name: str, detail: dict) -> None:
    cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                "VALUES (%s, %s, %s, 'allow', %s)", (by, action, f"connector_credential:{name}", Jsonb(detail)))


def issue(conn: psycopg.Connection, name: str, data_source: str, by: str, days: int, rate: int) -> str:
    registry = load_metadata(REPO_ROOT / "metadata").sources
    try:
        src = registry.source(data_source)
    except SourceRegistryError:
        raise SystemExit(f"Tanımsız veri kaynağı: {data_source}") from None
    if src.lifecycle == "retired":
        raise SystemExit(f"{data_source} emekliye ayrılmış; kimlik verilmez.")
    token = new_token()
    expires = datetime.now(UTC) + timedelta(days=days)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("""INSERT INTO ingest.connector_credential (name, data_source, token_sha256, token_hint,
                                                                max_records_per_minute, created_by, expires_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (name, data_source, token_sha256(token), token_hint(token), rate, by, expires))
        _audit(cur, by, "connector_credential_issue", name,
               {"data_source": data_source, "expires_at": expires.isoformat(), "per_minute": rate})
    return token


def revoke(conn: psycopg.Connection, name: str, by: str) -> bool:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("""UPDATE ingest.connector_credential SET revoked_at = now(), revoked_by = %s
                        WHERE name = %s AND revoked_at IS NULL RETURNING data_source""", (by, name))
        row = cur.fetchone()
        if row:
            _audit(cur, by, "connector_credential_revoke", name, {"data_source": row[0]})
    return row is not None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_issue = sub.add_parser("issue", help="yeni bağlayıcı kimliği ver (belirteç bir kez gösterilir)")
    p_issue.add_argument("--name", required=True, help="kimlik adı, ör. zabbix-dc1")
    p_issue.add_argument("--data-source", required=True, help="veri kaynağı adı (metadata/data_sources.yaml)")
    p_issue.add_argument("--by", required=True, help="veren kişi (denetim izi)")
    p_issue.add_argument("--expires-days", type=int, default=365, choices=range(1, 731), metavar="1-730")
    p_issue.add_argument("--per-minute", type=int, default=60000, help="dakikalık kayıt sınırı")
    p_issue.add_argument("--token-file", type=Path, help="belirteci ekran yerine bu dosyaya yaz (0600)")
    p_rev = sub.add_parser("revoke", help="kimliği iptal et")
    p_rev.add_argument("--name", required=True)
    p_rev.add_argument("--by", required=True)
    sub.add_parser("list", help="kimlikleri listele (belirteç gösterilmez)")
    args = ap.parse_args(argv)
    try:
        with psycopg.connect(database_url(), autocommit=True) as conn:
            if args.cmd == "issue":
                token = issue(conn, args.name, args.data_source, args.by, args.expires_days, args.per_minute)
                if args.token_file:
                    fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(token + "\n")
                    print(f"Kimlik verildi: {args.name} ({args.data_source}); belirteç dosyada: {args.token_file}")
                else:
                    print(f"Kimlik verildi: {args.name} ({args.data_source}). Belirteç (yalnızca şimdi gösterilir):")
                    print(token)
                return 0
            if args.cmd == "revoke":
                if revoke(conn, args.name, args.by):
                    print(f"İptal edildi: {args.name}")
                    return 0
                print(f"Etkin kimlik bulunamadı: {args.name}", file=sys.stderr)
                return 1
            rows = conn.execute("""SELECT name, data_source, '…' || token_hint, created_by, expires_at::date,
                                          CASE WHEN revoked_at IS NOT NULL THEN 'iptal'
                                               WHEN expires_at <= now() THEN 'süresi doldu' ELSE 'etkin' END,
                                          coalesce(last_used_at::text, '-')
                                     FROM ingest.connector_credential ORDER BY name""").fetchall()
            for r in rows:
                print("  ".join(str(v) for v in r))
            if not rows:
                print("Kimlik yok.")
            return 0
    except psycopg.errors.UniqueViolation:
        print("Bu adla bir kimlik zaten var.", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"Veritabanı hatası: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
