"""Görev çalışanının ayarları (ortamdan). Sırlar yalnızca dosyadan okunur (``*_FILE``); günlüğe yazılmaz.

ServiceNow kanalı (``EVREN_SN_TASK_*``):

* ``EVREN_SN_TASK_URL``            — örnek adresi (https; düz http yalnızca ``EVREN_SN_TASK_ALLOW_HTTP=1``)
* ``EVREN_SN_TASK_USER`` + ``EVREN_SN_TASK_PASSWORD_FILE`` veya ``EVREN_SN_TASK_TOKEN_FILE`` (OAuth erişim belirteci)
* ``EVREN_SN_TASK_CA_FILE``        — kurum CA dosyası (isteğe bağlı)
* ``EVREN_SN_TASK_SIMULATE=1``     — yalnızca geliştirme: bellek içi ServiceNow (kalıcı değil)

ServiceNow ayarı yoksa kanal "kapalı"dır: ServiceNow kanalındaki görevler beklemede kalır ve sistem durumunda görünür.
Evren kanalı her zaman çalışır.
"""
from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evren_core.connectors.http import read_secret

__all__ = ["ConfigError", "GovernanceConfig"]


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class GovernanceConfig:
    conninfo: str = field(repr=False)
    role: str = "evren_app_governance"
    poll_seconds: int = 30
    sync_minutes: int = 5
    batch: int = 50
    max_attempts: int = 10
    public_url: str | None = None          # görev sayfası bağlantısı (ör. https://evren.kurum.local)
    sn_url: str | None = None
    sn_user: str | None = None
    sn_password: str | None = field(default=None, repr=False)
    sn_token: str | None = field(default=None, repr=False)
    sn_ca_file: str | None = None
    sn_allow_http: bool = False
    simulate: bool = False
    worker_id: str = field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    health_file: str | None = None
    log_level: str = "WARNING"

    @property
    def channel_mode(self) -> str:
        if self.simulate:
            return "simülatör"
        return "servicenow" if self.sn_url else "kapalı"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> GovernanceConfig:
        env = os.environ if env is None else env
        conninfo = env.get("EVREN_DATABASE_URL", "")
        if env.get("EVREN_DATABASE_URL_FILE"):
            try:
                conninfo = Path(env["EVREN_DATABASE_URL_FILE"]).read_text(encoding="utf-8").strip()
            except OSError:
                raise ConfigError("EVREN_DATABASE_URL_FILE okunamadı") from None
        try:
            poll = int(env.get("EVREN_WORKER_POLL_SECONDS", "30"))
            sync = int(env.get("EVREN_TASK_SYNC_MINUTES", "5"))
        except ValueError:
            raise ConfigError("EVREN_WORKER_POLL_SECONDS ve EVREN_TASK_SYNC_MINUTES sayı olmalı") from None
        if not 1 <= poll <= 3600 or not 1 <= sync <= 1440:
            raise ConfigError("EVREN_WORKER_POLL_SECONDS 1–3600, EVREN_TASK_SYNC_MINUTES 1–1440 olmalı")
        simulate = env.get("EVREN_SN_TASK_SIMULATE") == "1"
        url = (env.get("EVREN_SN_TASK_URL") or "").strip() or None
        allow_http = env.get("EVREN_SN_TASK_ALLOW_HTTP") == "1"
        user = password = token = None
        if url and not simulate:
            if not url.startswith("https://") and not (allow_http and url.startswith("http://")):
                raise ConfigError("EVREN_SN_TASK_URL https olmalı (düz http yalnızca EVREN_SN_TASK_ALLOW_HTTP=1)")
            if env.get("EVREN_SN_TASK_TOKEN_FILE"):
                token = read_secret(env["EVREN_SN_TASK_TOKEN_FILE"], "ServiceNow görev kanalı belirteci")
            elif env.get("EVREN_SN_TASK_USER") and env.get("EVREN_SN_TASK_PASSWORD_FILE"):
                user = env["EVREN_SN_TASK_USER"]
                password = read_secret(env["EVREN_SN_TASK_PASSWORD_FILE"], "ServiceNow görev kanalı parolası")
            else:
                raise ConfigError("ServiceNow görev kanalı kimliği yok: EVREN_SN_TASK_TOKEN_FILE veya "
                                  "EVREN_SN_TASK_USER + EVREN_SN_TASK_PASSWORD_FILE")
        public = (env.get("EVREN_PUBLIC_URL") or "").strip().rstrip("/") or None
        if public and not public.startswith(("https://", "http://")):
            raise ConfigError("EVREN_PUBLIC_URL http(s) adresi olmalı")
        values: dict[str, Any] = {
            "conninfo": conninfo, "role": env.get("EVREN_DB_ROLE", "evren_app_governance"), "poll_seconds": poll,
            "sync_minutes": sync, "public_url": public, "sn_url": url, "sn_user": user, "sn_password": password,
            "sn_token": token, "sn_ca_file": env.get("EVREN_SN_TASK_CA_FILE") or None, "sn_allow_http": allow_http,
            "simulate": simulate, "health_file": env.get("EVREN_WORKER_HEALTH_FILE") or None,
            "log_level": env.get("EVREN_LOG_LEVEL", "WARNING").upper(),
        }
        if env.get("EVREN_WORKER_NAME"):
            values["worker_id"] = env["EVREN_WORKER_NAME"]
        values.update(overrides)
        return cls(**values)
