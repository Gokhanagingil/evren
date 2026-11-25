#!/usr/bin/env python3
"""Geliştirme yığını için rastgele sırlar üretir (deploy/secrets/, git'e girmez).

    python tools/dev_secrets.py            # eksik olanları üret, var olanlara dokunma
    python tools/dev_secrets.py --force    # hepsini yeniden üret (veritabanı birimi de sıfırlanmalı)

Dosyalar compose ``secrets:`` ile konteynerlere ``/run/secrets/<ad>`` olarak bağlanır. Dizin 0700'dür; dosyalar
konteynerdeki root olmayan kullanıcı okuyabilsin diye 0644'tür (yalnızca yerel geliştirme içindir).
Üretimde sırlar kurum sır kasasından gelir (docs/12, ADR-0016).
"""
from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "deploy" / "secrets"
SERVICES = {"api": "evren_api_svc", "worker": "evren_worker_svc", "gateway": "evren_gateway_svc",
            "ingest": "evren_ingest_svc", "indexer": "evren_indexer_svc"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o700)

    def put(name: str, value: str) -> str:
        path = DIR / name
        if path.exists() and not args.force:
            return path.read_text(encoding="utf-8").strip()
        path.write_text(value + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
        return value

    owner = put("pg_owner_password", secrets.token_urlsafe(32))
    put("owner_dsn", f"postgresql://evren_owner:{owner}@postgres:5432/evren")
    for svc, login in SERVICES.items():
        pw = put(login, secrets.token_urlsafe(32))
        put(f"{svc}_dsn", f"postgresql://{login}:{pw}@postgres:5432/evren")
    put("session_key", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    print(f"Sırlar hazır: {DIR.relative_to(ROOT)}/ ({len(list(DIR.iterdir()))} dosya)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
