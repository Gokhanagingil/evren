"""sn-sync (U9.1, ADR-0042): Evren golden record'unu ServiceNow CMDB replikasına IRE ile iter."""
from .config import ConfigError, ReplicaConfig
from .worker import ReplicaWorker

__all__ = ["ConfigError", "ReplicaConfig", "ReplicaWorker", "main"]


def main(argv: list[str] | None = None) -> int:
    from .cli import main as cli_main

    return cli_main(argv)
