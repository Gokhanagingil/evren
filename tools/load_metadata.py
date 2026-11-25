#!/usr/bin/env python3
"""Metadata sözlüğünü doğrular ve veritabanı tohum SQL'ini üretir.

Kullanım:
  python tools/load_metadata.py --check                 # yalnızca doğrula
  python tools/load_metadata.py --sql build/seed.sql    # SQL üret
  psql "$EVREN_DATABASE_URL" -v ON_ERROR_STOP=1 -f build/seed.sql
"""
from __future__ import annotations

import argparse
import sys

from _bootstrap import REPO_ROOT
from evren_core.metadata import generate_seed_sql, load_metadata, validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metadata", default=str(REPO_ROOT / "metadata"))
    parser.add_argument("--check", action="store_true", help="Yalnızca doğrula")
    parser.add_argument("--sql", help="Üretilecek SQL dosyası ('-' = stdout)")
    args = parser.parse_args(argv)

    md = load_metadata(args.metadata)
    report = validate(md)
    for w in report.warnings:
        print(f"UYARI: {w}", file=sys.stderr)
    for e in report.errors:
        print(f"HATA: {e}", file=sys.stderr)
    if not report.ok:
        return 1
    verify = sum(1 for c in md.classes if c.get("verify")) + sum(
        1 for c in md.classes for f in c.get("fields") or [] if f.get("verify"))
    print(f"Metadata geçerli: {len(md.classes)} sınıf, {sum(len(c.get('fields') or []) for c in md.classes)} alan, "
          f"{len(md.relationship_types)} ilişki tipi, {len(md.data_sources)} veri kaynağı; "
          f"ServiceNow'dan doğrulanacak {verify} öğe.", file=sys.stderr)
    if args.sql:
        sql = generate_seed_sql(md)
        if args.sql == "-":
            sys.stdout.write(sql)
        else:
            from pathlib import Path

            Path(args.sql).parent.mkdir(parents=True, exist_ok=True)
            Path(args.sql).write_text(sql, encoding="utf-8")
            print(f"SQL yazıldı: {args.sql}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
