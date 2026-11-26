"""Kaynak istemcileri kaydı (U5, ADR-0020). Her kaynak: istemci, kimlik doğrulama türü, eşleme klasörü, simülatör.

Yeni kaynak eklemek: ``sources/<kaynak>.py`` içinde ``SourceClient`` (``data_source`` + ``fetch``) ve mümkünse bir
simülatör yazılır, burada ``SOURCES``'a eklenir; eşleme ``connectors/<klasör>/*mapping.yaml`` dosyalarındadır.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..http import HttpClient, Transport
from .zabbix import ZabbixClient, ZabbixSimulator

__all__ = ["SOURCES", "SourceSpec"]


@dataclass(frozen=True)
class SourceSpec:
    key: str
    data_source: str
    auth: Literal["bearer", "basic"]
    mapping_dir: str
    build: Callable[[HttpClient, Mapping[str, Any]], Any]
    simulator: Callable[[Path], Transport] | None = None
    options: tuple[str, ...] = ()


SOURCES: dict[str, SourceSpec] = {
    "zabbix": SourceSpec(
        key="zabbix", data_source="Zabbix", auth="bearer", mapping_dir="zabbix",
        build=lambda http, opts: ZabbixClient(http, page_size=int(opts.get("page_size", 500))),
        simulator=ZabbixSimulator.from_dir, options=("page_size",)),
}
