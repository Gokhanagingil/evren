"""Evren bağlayıcı çalıştırıcı servisi (U5.3b, ADR-0021)."""
from .config import ConfigError, InstanceConfig, RunnerConfig, load_instances
from .runner import Runner

__all__ = ["ConfigError", "InstanceConfig", "Runner", "RunnerConfig", "load_instances", "main"]


def main(argv: list[str] | None = None) -> int:
    from .cli import main as _main

    return _main(argv)
