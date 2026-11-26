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
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from evren_core.connectors.sources import SOURCES, SourceSpec
from evren_core.metadata import Metadata
from evren_core.schedule import CronError, CronSchedule

__all__ = ["ConfigError", "InstanceConfig", "RunnerConfig", "load_instances"]

REPO_ROOT = Path(__file__).resolve().parents[3]
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
# yapılandırma anahtarı → alt sürecin ortam değişkeni (ADR-0020 tools/connector_run.py)
ENV_KEYS = {
    "url": "EVREN_SOURCE_URL", "token_file": "EVREN_SOURCE_TOKEN_FILE", "user": "EVREN_SOURCE_USER",
    "password_file": "EVREN_SOURCE_PASSWORD_FILE", "ca_file": "EVREN_SOURCE_CA_FILE",
    "tenant_id": "EVREN_SOURCE_TENANT_ID", "client_id": "EVREN_SOURCE_CLIENT_ID",
    "client_secret_file": "EVREN_SOURCE_CLIENT_SECRET_FILE", "login_url": "EVREN_SOURCE_LOGIN_URL",
    "scope": "EVREN_SOURCE_SCOPE", "gateway_token_file": "EVREN_CONNECTOR_TOKEN_FILE",
}
OTHER_KEYS = {"name", "connector", "schedule", "schedule_mode", "page_size", "api_release", "max_runtime_minutes",
              "enabled", "simulate"}
SECRET_WORDS = ("password", "passwd", "token", "secret", "api_key", "apikey", "credential")
REQUIRED_AUTH = {"bearer": ("token_file",), "basic": ("user", "password_file"), "login": ("user", "password_file"),
                 "oauth": ("tenant_id", "client_id", "client_secret_file")}


class ConfigError(ValueError):
    """Yapılandırma hatası. İleti sır ve dosya yolu içermez (yalnızca örnek ve anahtar adı)."""


@dataclass(frozen=True)
class InstanceConfig:
    name: str
    connector: str
    data_source: str
    settings: Mapping[str, str] = field(repr=False)  # ENV_KEYS anahtarları; yollar ve adres günlüğe yazılmaz
    schedule: CronSchedule | None = None
    schedule_mode: str = "snapshot"
    page_size: int | None = None
    api_release: str | None = None
    max_runtime_minutes: int = 120
    enabled: bool = True
    simulate: Path | None = None

    @property
    def spec(self) -> SourceSpec:
        return SOURCES[self.connector]

    def source_env(self) -> dict[str, str]:
        return {ENV_KEYS[k]: v for k, v in self.settings.items()}

    def redactions(self) -> list[str]:
        """Sonuç notunda yer tutucuyla değiştirilecek değerler (adres, kullanıcı, dosya yolları)."""
        return sorted({v for v in self.settings.values() if len(v) >= 3}, key=len, reverse=True)


def _check_file(name: str, key: str, value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigError(f"{name}: {key} mutlak dosya yolu olmalı")
    if not Path(value).is_file():
        raise ConfigError(f"{name}: {key} dosyası bulunamadı")
    return value


def _instance(raw: Any, md: Metadata, allow_simulate: bool) -> InstanceConfig:
    if not isinstance(raw, dict):
        raise ConfigError("her bağlayıcı örneği bir eşleme (anahtar: değer) olmalı")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        raise ConfigError("örnek adı geçersiz (harf/rakam ile başlar, 2–64 karakter: A-Z a-z 0-9 _ . -)")
    for key in raw:
        if key not in ENV_KEYS and key not in OTHER_KEYS:
            if any(w in str(key).lower() for w in SECRET_WORDS):
                raise ConfigError(f"{name}: '{key}' kabul edilmez; sır dosyaya yazılmaz, *_file ile yolunu verin")
            raise ConfigError(f"{name}: bilinmeyen anahtar '{key}'")
    connector = raw.get("connector")
    if connector not in SOURCES:
        raise ConfigError(f"{name}: tanımsız kaynak istemcisi (seçenekler: {', '.join(sorted(SOURCES))})")
    spec = SOURCES[connector]
    simulate = None
    if raw.get("simulate") is not None:
        if not allow_simulate:
            raise ConfigError(f"{name}: simulate yalnızca EVREN_CONNECTOR_ALLOW_SIMULATE=1 ile kullanılır")
        rel = Path(str(raw["simulate"]))
        if rel.is_absolute() or ".." in rel.parts or not (REPO_ROOT / rel).is_dir():
            raise ConfigError(f"{name}: simulate depo içindeki bir örnek veri klasörü olmalı")
        simulate = REPO_ROOT / rel
    settings: dict[str, str] = {}
    for key in ENV_KEYS:
        if raw.get(key) is None:
            continue
        value = raw[key]
        if key.endswith("_file"):
            settings[key] = _check_file(name, key, value)
        elif not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ConfigError(f"{name}: {key} metin olmalı")
        else:
            settings[key] = value.strip()
    if "gateway_token_file" not in settings:
        raise ConfigError(f"{name}: gateway_token_file gerekli (alım geçidi belirteci)")
    for key in ("url", "login_url"):
        if key in settings and not settings[key].lower().startswith("https://"):
            raise ConfigError(f"{name}: {key} https olmalı")
    if simulate is None:
        if "url" not in settings and not spec.default_url:
            raise ConfigError(f"{name}: url gerekli")
        missing = [k for k in REQUIRED_AUTH[spec.auth] if k not in settings]
        if missing:
            raise ConfigError(f"{name}: {connector} için gerekli: {', '.join(missing)}")
    src = next((s for s in md.data_sources if s["name"] == spec.data_source), None)
    if src is None:
        raise ConfigError(f"{name}: veri kaynağı {spec.data_source} metadata'da yok")
    expr = raw["schedule"] if "schedule" in raw else src.get("schedule")
    schedule = None
    if expr is not None:
        try:
            schedule = CronSchedule.parse(expr)
        except CronError as exc:
            raise ConfigError(f"{name}: zamanlama geçersiz: {exc}") from None
    mode = raw.get("schedule_mode", "snapshot")
    if mode not in ("snapshot", "delta"):
        raise ConfigError(f"{name}: schedule_mode snapshot veya delta olmalı")
    page_size, release = raw.get("page_size"), raw.get("api_release")
    if page_size is not None and ("page_size" not in spec.options or not isinstance(page_size, int)
                                  or not 1 <= page_size <= 10000):
        raise ConfigError(f"{name}: page_size bu kaynakta yok veya 1–10000 dışında")
    if release is not None and ("release" not in spec.options or not isinstance(release, str)
                                or not re.fullmatch(r"[0-9]+(\.[0-9]+){1,3}", release)):
        raise ConfigError(f"{name}: api_release bu kaynakta yok veya sürüm biçiminde değil")
    runtime = raw.get("max_runtime_minutes", 120)
    if not isinstance(runtime, int) or isinstance(runtime, bool) or not 1 <= runtime <= 1440:
        raise ConfigError(f"{name}: max_runtime_minutes 1–1440 olmalı")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{name}: enabled true/false olmalı")
    return InstanceConfig(name, connector, spec.data_source, settings, schedule, mode, page_size, release, runtime,
                          enabled, simulate)


def load_instances(path: str | Path, md: Metadata, *, allow_simulate: bool = False) -> list[InstanceConfig]:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise ConfigError("bağlayıcı örnek dosyası okunamadı (EVREN_CONNECTORS_FILE)") from None
    items = (doc or {}).get("connectors") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ConfigError("bağlayıcı örnek dosyasında 'connectors' listesi yok")
    out = [_instance(raw, md, allow_simulate) for raw in items]
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
