"""Bağlayıcı örneği tanımı (ADR-0021 §2; panel sihirbazı U8.7, ADR-0038).

Örnek, bir kaynak istemcisinin bir kurulumdaki karşılığıdır (ör. ``zabbix-dc1``). Tanım çalıştırıcının örnek
dosyasında (``EVREN_CONNECTORS_FILE``) durur; sır değeri asla yazılmaz, yalnızca kasadan bağlanan dosyanın yolu.
Doğrulama burada tektir: çalıştırıcı dosyayı yüklerken (dosyalar da var mı) ve panel sihirbazı kurulum paketini
üretirken (yalnızca biçim) aynı kuralları kullanır.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..metadata import Metadata
from ..schedule import CronError, CronSchedule
from .sources import SOURCES, SourceSpec

__all__ = ["AUTH_FIELDS", "ENV_KEYS", "NAME", "URL_EXAMPLES", "ConfigError", "InstanceConfig", "parse_instance",
           "render_instance", "secret_files"]

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
# yapılandırma anahtarı → alt sürecin ortam değişkeni (ADR-0020 tools/connector_run.py)
ENV_KEYS = {
    "url": "EVREN_SOURCE_URL", "token_file": "EVREN_SOURCE_TOKEN_FILE", "user": "EVREN_SOURCE_USER",
    "password_file": "EVREN_SOURCE_PASSWORD_FILE", "ca_file": "EVREN_SOURCE_CA_FILE",
    "tenant_id": "EVREN_SOURCE_TENANT_ID", "client_id": "EVREN_SOURCE_CLIENT_ID",
    "client_secret_file": "EVREN_SOURCE_CLIENT_SECRET_FILE", "login_url": "EVREN_SOURCE_LOGIN_URL",
    "scope": "EVREN_SOURCE_SCOPE", "gateway_token_file": "EVREN_CONNECTOR_TOKEN_FILE",
    "lookback_days": "EVREN_SOURCE_LOOKBACK_DAYS",  # değişiklik kayıtlarında geriye bakış (ADR-0025)
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


def _check_file(name: str, key: str, value: Any, check_files: bool) -> str:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 500 or ".." in Path(value).parts:
        raise ConfigError(f"{name}: {key} mutlak dosya yolu olmalı")
    if check_files and not Path(value).is_file():
        raise ConfigError(f"{name}: {key} dosyası bulunamadı")
    return value


def parse_instance(raw: Any, md: Metadata, *, allow_simulate: bool = False, check_files: bool = True,
                   repo_root: Path | None = None) -> InstanceConfig:
    """Bir örnek tanımını doğrular. ``check_files``: dosyaların var olduğu da denetlenir (yalnızca çalıştırıcıda;
    panel sihirbazı yolu yalnızca biçimce denetler). ``repo_root``: ``simulate`` klasörünün kökü."""
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
        root = repo_root or Path.cwd()
        if rel.is_absolute() or ".." in rel.parts or not (root / rel).is_dir():
            raise ConfigError(f"{name}: simulate depo içindeki bir örnek veri klasörü olmalı")
        simulate = root / rel
    settings: dict[str, str] = {}
    for key in ENV_KEYS:
        if raw.get(key) is None:
            continue
        value = raw[key]
        if key.endswith("_file"):
            settings[key] = _check_file(name, key, value, check_files)
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


#: Sihirbazda kimlik türüne göre sorulan alanlar: (anahtar, Türkçe etiket, sır dosyası mı)
AUTH_FIELDS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "bearer": (("token_file", "API belirteci dosyası", True),),
    "basic": (("user", "Kullanıcı adı", False), ("password_file", "Parola dosyası", True)),
    "login": (("user", "Kullanıcı adı", False), ("password_file", "Parola dosyası", True)),
    "oauth": (("tenant_id", "Kiracı (tenant) kimliği", False), ("client_id", "İstemci (uygulama) kimliği", False),
              ("client_secret_file", "İstemci sırrı dosyası", True)),
}
#: Kaynak adresi örnekleri (yalnızca yer tutucu)
URL_EXAMPLES = {
    "zabbix": "https://zabbix.kurum.local/api_jsonrpc.php", "vcenter": "https://vcenter.kurum.local",
    "servicenow": "https://kurum.service-now.com", "servicenow_change": "https://kurum.service-now.com",
    "bmc_discovery": "https://discovery.kurum.local", "nnmi": "https://nnmi.kurum.local",
}
_ORDER = ("name", "connector", "url", "user", "token_file", "password_file", "tenant_id", "client_id",
          "client_secret_file", "scope", "login_url", "ca_file", "gateway_token_file", "schedule", "schedule_mode",
          "page_size", "api_release", "lookback_days", "max_runtime_minutes", "enabled")
_FILE_TR = {"token_file": "API belirteci", "password_file": "parola", "client_secret_file": "istemci sırrı",
            "ca_file": "kurum CA sertifikası (sır değil)", "gateway_token_file": "Evren alım geçidi belirteci"}


def secret_files(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    """Kasada bulunması (çalıştırıcıya bağlanması) gereken dosyalar: yol ve ne olduğu."""
    return [{"key": k, "path": str(raw[k]), "what": _FILE_TR.get(k, k)} for k in _ORDER
            if k.endswith("_file") and raw.get(k)]


def render_instance(raw: Mapping[str, Any]) -> str:
    """Örnek dosyasına eklenecek YAML bloğu (anahtarlar sabit sırada; boş olanlar yazılmaz)."""
    item = {k: raw[k] for k in _ORDER if k in raw and raw[k] not in (None, "")}
    if "schedule" in raw and raw["schedule"] is None:
        item["schedule"] = None  # yalnızca elle çalıştırma
    text = yaml.safe_dump([item], allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)
    return "".join(f"  {line}\n" if line else "\n" for line in text.rstrip("\n").split("\n"))
