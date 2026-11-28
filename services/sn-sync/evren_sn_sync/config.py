"""sn-sync çalışanının ayarları (ortamdan). Sırlar yalnızca dosyadan okunur (``*_FILE``); günlüğe yazılmaz.

ServiceNow replikası (ADR-0042):

* ``EVREN_SN_REPLICA_URL``           — örnek adresi (https; düz http yalnızca ``EVREN_SN_REPLICA_ALLOW_HTTP=1``)
* ``EVREN_SN_REPLICA_USER`` + ``EVREN_SN_REPLICA_PASSWORD_FILE`` veya ``EVREN_SN_REPLICA_TOKEN_FILE`` (OAuth belirteci).
  Entegrasyon kullanıcısı ayrı ve en az yetkilidir: IRE yazma ve kapsamdaki CMDB tablolarını okuma.
* ``EVREN_SN_REPLICA_CA_FILE``       — kurum CA dosyası (isteğe bağlı)
* ``EVREN_SN_REPLICA_SIMULATE=1``    — yalnızca geliştirme: bellek içi ServiceNow; açılışta Evren'in ServiceNow
  kaynaklı CI'larıyla doldurulur (ServiceNow'da zaten var olanlar)
* ``EVREN_SN_SOURCE_NAME``           — IRE veri kaynağı adı (varsayılan ``metadata/sn_sync.yaml`` → ``source_name``)
* ``EVREN_REPLICA_BATCH`` (25)       — istek başına öğe; ``EVREN_REPLICA_LIMIT`` (500) — tur başına aday

Mod (kapalı/gölge/etkin) ve toplu değişiklik sınırı veritabanındadır; panelden değişir (``sync.replica_setting``).
ServiceNow bağlantısı yoksa çalışan hiçbir şey göndermez; sistem durumunda "bağlantı yok" görünür.
"""
from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evren_core.connectors.http import HttpError, read_secret

__all__ = ["ConfigError", "ReplicaConfig"]


class ConfigError(ValueError):
    pass


def _secret(path: str, what: str) -> str:
    try:
        return read_secret(path, what)
    except HttpError as exc:
        raise ConfigError(str(exc)) from None


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(env.get(name) or default)
    except ValueError:
        raise ConfigError(f"{name} sayı olmalı") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} {lo}–{hi} arasında olmalı")
    return value


@dataclass(frozen=True)
class ReplicaConfig:
    conninfo: str = field(repr=False)
    role: str = "evren_app_sync"
    poll_seconds: int = 60
    batch: int = 25
    limit: int = 500
    sn_url: str | None = None
    sn_user: str | None = None
    sn_password: str | None = field(default=None, repr=False)
    sn_token: str | None = field(default=None, repr=False)
    sn_ca_file: str | None = None
    sn_allow_http: bool = False
    simulate: bool = False
    source_name: str | None = None
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    health_file: str | None = None
    log_level: str = "WARNING"

    @property
    def connection(self) -> str:
        """Sistem durumunda görünen bağlantı: simülatör | servicenow | yok."""
        if self.simulate:
            return "simülatör"
        return "servicenow" if self.sn_url else "yok"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> ReplicaConfig:
        env = os.environ if env is None else env
        conninfo = env.get("EVREN_DATABASE_URL", "")
        if env.get("EVREN_DATABASE_URL_FILE"):
            try:
                conninfo = Path(env["EVREN_DATABASE_URL_FILE"]).read_text(encoding="utf-8").strip()
            except OSError:
                raise ConfigError("EVREN_DATABASE_URL_FILE okunamadı") from None
        simulate = env.get("EVREN_SN_REPLICA_SIMULATE") == "1"
        url = (env.get("EVREN_SN_REPLICA_URL") or "").strip() or None
        allow_http = env.get("EVREN_SN_REPLICA_ALLOW_HTTP") == "1"
        user = password = token = None
        if url and not simulate:
            if not url.startswith("https://") and not (allow_http and url.startswith("http://")):
                raise ConfigError("EVREN_SN_REPLICA_URL https olmalı (düz http yalnızca EVREN_SN_REPLICA_ALLOW_HTTP=1)")
            if env.get("EVREN_SN_REPLICA_TOKEN_FILE"):
                token = _secret(env["EVREN_SN_REPLICA_TOKEN_FILE"], "ServiceNow replika belirteci")
            elif env.get("EVREN_SN_REPLICA_USER") and env.get("EVREN_SN_REPLICA_PASSWORD_FILE"):
                user = env["EVREN_SN_REPLICA_USER"]
                password = _secret(env["EVREN_SN_REPLICA_PASSWORD_FILE"], "ServiceNow replika parolası")
            else:
                raise ConfigError("ServiceNow replika kimliği yok: EVREN_SN_REPLICA_TOKEN_FILE veya "
                                  "EVREN_SN_REPLICA_USER + EVREN_SN_REPLICA_PASSWORD_FILE")
        values: dict[str, Any] = {
            "conninfo": conninfo, "role": env.get("EVREN_DB_ROLE", "evren_app_sync"),
            "poll_seconds": _int(env, "EVREN_WORKER_POLL_SECONDS", 60, 1, 3600),
            "batch": _int(env, "EVREN_REPLICA_BATCH", 25, 1, 200),
            "limit": _int(env, "EVREN_REPLICA_LIMIT", 500, 1, 20000),
            "sn_url": url, "sn_user": user, "sn_password": password, "sn_token": token,
            "sn_ca_file": env.get("EVREN_SN_REPLICA_CA_FILE") or None, "sn_allow_http": allow_http, "simulate": simulate,
            "source_name": (env.get("EVREN_SN_SOURCE_NAME") or "").strip() or None,
            "health_file": env.get("EVREN_WORKER_HEALTH_FILE") or None,
            "log_level": env.get("EVREN_LOG_LEVEL", "WARNING").upper(),
        }
        if env.get("EVREN_WORKER_NAME"):
            values["worker_id"] = env["EVREN_WORKER_NAME"]
        values.update(overrides)
        return cls(**values)
