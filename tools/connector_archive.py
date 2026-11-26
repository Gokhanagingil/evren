#!/usr/bin/env python3
"""Ham yük arşivi yönetimi (U5.4, ADR-0024): listele, doğrula, saklama süresiyle temizle.

    python tools/connector_archive.py list                       # çalışmalar (manifest özetleri; veri yok)
    python tools/connector_archive.py verify Zabbix/2026-11-26/<run_id>   # özet + şifre çözme denetimi
    python tools/connector_archive.py prune --days 30            # süresi dolan çalışma dizinlerini sil

Ortam: ``EVREN_ARCHIVE_DIR`` (arşiv kökü), ``EVREN_ARCHIVE_KEY_FILE`` (yalnızca ``verify``). Çıktı JSON'dur ve kaynak
verisi içermez. Yeniden işleme: ``tools/connector_run.py --connector <kaynak> --from-archive <çalışma>``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from _bootstrap import REPO_ROOT  # noqa: F401  # evren_core yolu
from evren_core.connectors.archive import ArchiveError, load_keys, open_run, prune, runs


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="arşivlenmiş çalışmalar")
    p_verify = sub.add_parser("verify", help="çalışmanın parçalarını özet ve şifre çözmeyle denetle")
    p_verify.add_argument("run")
    p_prune = sub.add_parser("prune", help="saklama süresi dolanları sil")
    p_prune.add_argument("--days", type=int, required=True)
    args = ap.parse_args(argv)
    env = dict(os.environ if env is None else env)
    root = env.get("EVREN_ARCHIVE_DIR", "").strip()
    if not root:
        print("Yapılandırma hatası: EVREN_ARCHIVE_DIR tanımlı değil", file=sys.stderr)
        return 2
    try:
        if args.cmd == "list":
            out: object = runs(root)
        elif args.cmd == "verify":
            run = open_run(root, args.run, load_keys(env.get("EVREN_ARCHIVE_KEY_FILE")))
            out = {"run": args.run, "records": sum(1 for _ in run.records()), "ok": True}
        else:
            out = {"removed": prune(root, args.days)}
    except ArchiveError as exc:
        print(f"Arşiv hatası: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
