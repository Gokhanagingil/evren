"""Çalıştırıcı yapılandırması (U5.3b, ADR-0021 §2–3): ortam değişkenleri ve bağlayıcı örnek dosyası.

Örnek dosyası (``EVREN_CONNECTORS_FILE``, YAML) yalnızca çalıştırıcıda durur; veritabanına sırsız özeti gider::

    connectors:
      - name: zabbix-dc1                       # örnek adı (geçmişte ve arayüzde görünür)
        connector: zabbix                      # kaynak istemcisi (evren_core.connectors.sources.SOURCES)
        url: https://zabbix.kurum.local/api_jsonrpc.php
        token_file: /run/secrets/zabbix_dc1    # sır değil, sırrın DOSYA YOLU
        gateway_token_file: /run/secrets/gw_zabbix_dc1
        schedule: "15 */2 * * *"               # yoksa data_sources.yaml; null → zamanlama yok
        schedule_mode: snapshot                # veya delta (since: son başarılı çalışma − 10 dk)
        max_runtime_minutes: 120

**Sır değeri dosyaya yazılmaz:** ``password``, ``token``, ``secret`` gibi anahtarlar reddedilir; yalnızca ``*_file``
yolları kabul edilir. Kaynak adresi https olmalıdır. ``simulate`` (örnek veri klasörü) yalnızca
``EVREN_CONNECTOR_ALLOW_SIMULATE=1`` ile (geliştirme yığını, testler).
"""
from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from evren_core.connectors.instances import ConfigError, InstanceConfig, parse_instance
from evren_core.metadata import Metadata

__all__ = ["ConfigError", "InstanceConfig", "RunnerConfig", "load_instances"]

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_instances(path: str | Path, md: Metadata, *, allow_simulate: bool = False) -> list[InstanceConfig]:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise ConfigError("bağlayıcı örnek dosyası okunamadı (EVREN_CONNECTORS_FILE)") from None
    items = (doc or {}).get("connectors") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ConfigError("bağlayıcı örnek dosyasında 'connectors' listesi yok")
    out = [parse_instance(raw, md, allow_simulate=allow_simulate, repo_root=REPO_ROOT) for raw in items]
    names = [i.name for i in out]
    if len(names) != len(set(names)):
        raise ConfigError("bağlayıcı örnek adı tekrarlanıyor")
    return out


@dataclass(frozen=True)
class RunnerConfig:
    conninfo: str = field(repr=False)
    connectors_file: Path
    gateway_url: str
    role: str = "evren_app_connector"
    poll_seconds: int = 15
    max_parallel: int = 2
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Istanbul"))
    gateway_ca_file: str | None = None
    gateway_allow_http: bool = False
    allow_simulate: bool = False
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    health_file: str | None = None
    metadata_dir: Path = REPO_ROOT / "metadata"
    log_level: str = "WARNING"
    archive_dir: str | None = None          # ham yük arşivi kökü (ADR-0024); alt sürece aktarılır
    archive_key_file: str | None = None     # Fernet anahtar dosyası (yol; sır değil)
    archive_retention_days: int = 30        # günde bir kez süresi dolanlar silinir

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> RunnerConfig:
        env = os.environ if env is None else env
        conninfo = env.get("EVREN_DATABASE_URL", "")
        if env.get("EVREN_DATABASE_URL_FILE"):
            try:
                conninfo = Path(env["EVREN_DATABASE_URL_FILE"]).read_text(encoding="utf-8").strip()
            except OSError:
                raise ConfigError("EVREN_DATABASE_URL_FILE okunamadı") from None
        if not env.get("EVREN_CONNECTORS_FILE"):
            raise ConfigError("EVREN_CONNECTORS_FILE tanımlı değil")
        gateway = env.get("EVREN_GATEWAY_URL", "").strip()
        allow_http = env.get("EVREN_GATEWAY_ALLOW_HTTP") == "1"
        if not gateway.startswith("https://") and not (allow_http and gateway.startswith("http://")):
            raise ConfigError("EVREN_GATEWAY_URL https olmalı (düz http yalnızca EVREN_GATEWAY_ALLOW_HTTP=1)")
        try:
            poll = int(env.get("EVREN_WORKER_POLL_SECONDS", "15"))
            parallel = int(env.get("EVREN_CONNECTOR_MAX_PARALLEL", "2"))
        except ValueError:
            raise ConfigError("EVREN_WORKER_POLL_SECONDS ve EVREN_CONNECTOR_MAX_PARALLEL sayı olmalı") from None
        if not 1 <= poll <= 3600 or not 1 <= parallel <= 16:
            raise ConfigError("EVREN_WORKER_POLL_SECONDS 1–3600, EVREN_CONNECTOR_MAX_PARALLEL 1–16 olmalı")
        try:
            tz = ZoneInfo(env.get("EVREN_SCHEDULE_TZ", "Europe/Istanbul"))
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError("EVREN_SCHEDULE_TZ geçerli bir zaman dilimi değil") from None
        archive_dir = env.get("EVREN_ARCHIVE_DIR", "").strip() or None
        archive_key = env.get("EVREN_ARCHIVE_KEY_FILE", "").strip() or None
        try:
            retention = int(env.get("EVREN_ARCHIVE_RETENTION_DAYS", "30"))
        except ValueError:
            raise ConfigError("EVREN_ARCHIVE_RETENTION_DAYS sayı olmalı") from None
        if not 1 <= retention <= 3650:
            raise ConfigError("EVREN_ARCHIVE_RETENTION_DAYS 1–3650 olmalı")
        if archive_dir:
            from evren_core.connectors.archive import ArchiveError, load_keys
            try:
                load_keys(archive_key)  # anahtar yoksa veya geçersizse çalıştırıcı açılmaz (düz metin arşiv yok)
            except ArchiveError as exc:
                raise ConfigError(str(exc)) from None
            if not archive_dir.startswith("/"):
                raise ConfigError("EVREN_ARCHIVE_DIR mutlak yol olmalı")
        values: dict[str, Any] = {
            "conninfo": conninfo, "connectors_file": Path(env["EVREN_CONNECTORS_FILE"]), "gateway_url": gateway,
            "role": env.get("EVREN_DB_ROLE", "evren_app_connector"), "poll_seconds": poll, "max_parallel": parallel,
            "tz": tz, "gateway_ca_file": env.get("EVREN_GATEWAY_CA_FILE") or None, "gateway_allow_http": allow_http,
            "allow_simulate": env.get("EVREN_CONNECTOR_ALLOW_SIMULATE") == "1",
            "health_file": env.get("EVREN_WORKER_HEALTH_FILE") or None,
            "metadata_dir": Path(env.get("EVREN_METADATA_DIR", str(REPO_ROOT / "metadata"))),
            "log_level": env.get("EVREN_LOG_LEVEL", "WARNING"), "archive_dir": archive_dir,
            "archive_key_file": archive_key, "archive_retention_days": retention}
        if env.get("EVREN_RUNNER_NAME"):
            values["worker_id"] = f"{env['EVREN_RUNNER_NAME']}:{os.getpid()}"
        values.update(overrides)
        return cls(**values)
