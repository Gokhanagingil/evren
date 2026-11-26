"""``evren connector-runner`` / ``python -m evren_connector_runner`` giriş noktası (ADR-0021)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys

from .config import ConfigError, RunnerConfig
from .runner import Runner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evren connector-runner", description=__doc__)
    ap.add_argument("--once", action="store_true", help="bir tur: zamanlama + bekleyen istekler; bitmelerini bekle")
    ap.add_argument("--check", action="store_true", help="yapılandırmayı doğrula ve çık (veritabanına bağlanmaz)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("EVREN_LOG_LEVEL", "INFO").upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = RunnerConfig.from_env()
        runner = Runner(config)
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(json.dumps({"instances": sorted(runner.instances), "tz": config.tz.key,
                          "max_parallel": config.max_parallel}, ensure_ascii=False))
        return 0
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: runner.stop.set())
    if args.once:
        print(json.dumps(runner.run_once(), ensure_ascii=False))
        return 0
    runner.serve()
    return 0
