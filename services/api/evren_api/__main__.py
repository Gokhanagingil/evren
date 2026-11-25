"""Çalıştırma: ``EVREN_AUTH_MODE=dev python -m evren_api`` (services/api dizininden veya PYTHONPATH ile)."""
from __future__ import annotations

import os
import sys

import uvicorn

from .app import create_app
from .config import ConfigError, Settings


def main() -> int:
    try:
        settings = Settings.from_env(os.environ)
    except (ConfigError, ValueError) as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        return 2
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=False,
                server_header=False, date_header=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
