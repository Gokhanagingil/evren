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
from .bmc_discovery import BMCDiscoveryClient, BMCDiscoverySimulator
from .servicenow import ServiceNowClient, ServiceNowSimulator
from .vcenter import VCenterClient, VCenterSimulator
from .zabbix import ZabbixClient, ZabbixSimulator

__all__ = ["SOURCES", "SourceSpec"]


@dataclass(frozen=True)
class SourceSpec:
    key: str
    data_source: str
    auth: Literal["bearer", "basic", "login"]  # login: kullanıcı + parola dosyası istemciye verilir (oturum açar)
    mapping_dir: str
    build: Callable[[HttpClient, Mapping[str, Any]], Any]
    simulator: Callable[[Path], Transport] | None = None
    options: tuple[str, ...] = ()


SOURCES: dict[str, SourceSpec] = {
    "zabbix": SourceSpec(
        key="zabbix", data_source="Zabbix", auth="bearer", mapping_dir="zabbix",
        build=lambda http, opts: ZabbixClient(http, page_size=int(opts.get("page_size", 500))),
        simulator=ZabbixSimulator.from_dir, options=("page_size",)),
    "vcenter": SourceSpec(
        key="vcenter", data_source="vCenter", auth="login", mapping_dir="vcenter",
        build=lambda http, opts: VCenterClient(http, user=opts["credentials"][0], password=opts["credentials"][1],
                                               release=str(opts.get("release", "8.0.1.0")),
                                               page_size=int(opts.get("page_size", 500))),
        simulator=VCenterSimulator.from_dir, options=("page_size", "release")),
    "servicenow": SourceSpec(
        key="servicenow", data_source="ServiceNow", auth="basic", mapping_dir="servicenow_cmdb",
        build=lambda http, opts: ServiceNowClient(http, page_size=int(opts.get("page_size", 1000))),
        simulator=ServiceNowSimulator.from_dir, options=("page_size",)),
    "bmc_discovery": SourceSpec(
        key="bmc_discovery", data_source="BMC", auth="bearer", mapping_dir="bmc_discovery",
        build=lambda http, opts: BMCDiscoveryClient(http, release=str(opts.get("release", "1.12")),
                                                    page_size=int(opts.get("page_size", 500))),
        simulator=BMCDiscoverySimulator.from_dir, options=("page_size", "release")),
}
