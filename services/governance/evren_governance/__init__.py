"""Görev çalışanı (U8.4, ADR-0035): yönetişim görevlerini kanala teslim eder, durumunu eşitler, kapatır."""
from .config import ConfigError, GovernanceConfig
from .worker import GovernanceWorker

__all__ = ["ConfigError", "GovernanceConfig", "GovernanceWorker", "main"]


def main(argv: list[str] | None = None) -> int:
    from .cli import main as cli_main

    return cli_main(argv)
