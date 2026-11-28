"""``python -m evren_sn_sync [--once] [--check]``"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from .config import ConfigError, ReplicaConfig
from .worker import ReplicaWorker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evren_sn_sync", description="sn-sync: ServiceNow replikasına itme (IRE)")
    ap.add_argument("--once", action="store_true", help="bir tur çalış ve çık")
    ap.add_argument("--check", action="store_true", help="yalnızca yapılandırmayı denetle")
    args = ap.parse_args(argv)
    try:
        config = ReplicaConfig.from_env()
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.check:
        print(f"yapılandırma geçerli: bağlantı={config.connection} yoklama={config.poll_seconds} sn")
        return 0
    worker = ReplicaWorker(config)
    if args.once:
        print(worker.run_once())
        return 0
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    worker.serve()
    return 0
