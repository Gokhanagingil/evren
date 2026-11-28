"""Görev çalışanının ayarları (ortamdan). Sırlar yalnızca dosyadan okunur (``*_FILE``); günlüğe yazılmaz.

ServiceNow kanalı (``EVREN_SN_TASK_*``):

* ``EVREN_SN_TASK_URL``            — örnek adresi (https; düz http yalnızca ``EVREN_SN_TASK_ALLOW_HTTP=1``)
* ``EVREN_SN_TASK_USER`` + ``EVREN_SN_TASK_PASSWORD_FILE`` veya ``EVREN_SN_TASK_TOKEN_FILE`` (OAuth erişim belirteci)
* ``EVREN_SN_TASK_CA_FILE``        — kurum CA dosyası (isteğe bağlı)
* ``EVREN_SN_TASK_SIMULATE=1``     — yalnızca geliştirme: bellek içi ServiceNow (kalıcı değil)

ServiceNow ayarı yoksa kanal "kapalı"dır: ServiceNow kanalındaki görevler beklemede kalır ve sistem durumunda görünür.
Evren kanalı her zaman çalışır.

Bildirim kanalları (ADR-0040; ikisi de isteğe bağlı, yoksa yalnızca uygulama içi bildirim):

* ``EVREN_SMTP_HOST``, ``EVREN_SMTP_PORT`` (587), ``EVREN_SMTP_TLS`` (``starttls`` | ``ssl``; düz bağlantı yalnızca
  ``none`` ve geri döngü adresinde), ``EVREN_SMTP_USER`` + ``EVREN_SMTP_PASSWORD_FILE``, ``EVREN_MAIL_FROM``,
  ``EVREN_SMTP_CA_FILE``
* ``EVREN_TEAMS_WEBHOOKS_FILE``    — sır dosyası: her satırda ``ad=https://…`` (Teams iş akışı gelen web kancası).
  Yönlendirmede yalnızca ad geçer; adres veritabanına, günlüğe ve arayüze yazılmaz.
* ``EVREN_NOTIFY_SIMULATE=1``      — yalnızca geliştirme: e-posta ve Teams bellek içi simülatöre gider
"""
from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evren_core.connectors.http import HttpError, read_secret

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
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_tls: str = "starttls"
    smtp_user: str | None = None
    smtp_password: str | None = field(default=None, repr=False)
    smtp_ca_file: str | None = None
    mail_from: str | None = None
    teams_webhooks: dict[str, str] = field(default_factory=dict, repr=False)
    notify_simulate: bool = False

    @property
    def email_mode(self) -> str:
        if self.notify_simulate:
            return "simülatör"
        return "smtp" if self.smtp_host and self.mail_from else "kapalı"

    @property
    def teams_mode(self) -> str:
        if self.notify_simulate:
            return "simülatör"
        return "açık" if self.teams_webhooks else "kapalı"

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
        notify = _notify_settings(env)
        values: dict[str, Any] = {
            **notify, "conninfo": conninfo, "role": env.get("EVREN_DB_ROLE", "evren_app_governance"), "poll_seconds": poll,
            "sync_minutes": sync, "public_url": public, "sn_url": url, "sn_user": user, "sn_password": password,
            "sn_token": token, "sn_ca_file": env.get("EVREN_SN_TASK_CA_FILE") or None, "sn_allow_http": allow_http,
            "simulate": simulate, "health_file": env.get("EVREN_WORKER_HEALTH_FILE") or None,
            "log_level": env.get("EVREN_LOG_LEVEL", "WARNING").upper(),
        }
        if env.get("EVREN_WORKER_NAME"):
            values["worker_id"] = env["EVREN_WORKER_NAME"]
        values.update(overrides)
        return cls(**values)


_TEAMS_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


def _secret(path: str | None, what: str) -> str:
    try:
        return read_secret(path, what)
    except HttpError as exc:
        raise ConfigError(str(exc)) from None


def _teams_webhooks(path: str) -> dict[str, str]:
    """``ad=https://…`` satırları; adres sır sayılır: hata iletisinde geçmez."""
    out: dict[str, str] = {}
    for n, line in enumerate(_secret(path, "Teams web kancası dosyası").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, url = line.partition("=")
        name, url = name.strip(), url.strip()
        if not sep or not _TEAMS_NAME.match(name):
            raise ConfigError(f"Teams web kancası dosyası satır {n}: 'ad=https://…' biçiminde olmalı (ad: küçük harf)")
        if not url.startswith("https://") or " " in url:
            raise ConfigError(f"Teams web kancası '{name}': adres https olmalı")
        out[name] = url
    return out


def _notify_settings(env: Mapping[str, str]) -> dict[str, Any]:
    simulate = env.get("EVREN_NOTIFY_SIMULATE") == "1"
    host = (env.get("EVREN_SMTP_HOST") or "").strip() or None
    tls = (env.get("EVREN_SMTP_TLS") or "starttls").strip().lower()
    if tls not in ("starttls", "ssl", "none"):
        raise ConfigError("EVREN_SMTP_TLS starttls, ssl veya none olmalı")
    try:
        port = int(env.get("EVREN_SMTP_PORT") or (465 if tls == "ssl" else 587))
    except ValueError:
        raise ConfigError("EVREN_SMTP_PORT sayı olmalı") from None
    mail_from = (env.get("EVREN_MAIL_FROM") or "").strip() or None
    user = password = None
    if host and not simulate:
        if tls == "none" and host not in ("localhost", "127.0.0.1", "::1"):
            raise ConfigError("Şifresiz SMTP (EVREN_SMTP_TLS=none) yalnızca geri döngü adresinde kullanılır")
        if not mail_from or "@" not in mail_from:
            raise ConfigError("EVREN_MAIL_FROM gönderen e-posta adresi olmalı")
        if env.get("EVREN_SMTP_USER"):
            user = env["EVREN_SMTP_USER"]
            password = _secret(env.get("EVREN_SMTP_PASSWORD_FILE"), "SMTP parolası")
    teams = _teams_webhooks(env["EVREN_TEAMS_WEBHOOKS_FILE"]) if env.get("EVREN_TEAMS_WEBHOOKS_FILE") else {}
    return {"smtp_host": host, "smtp_port": port, "smtp_tls": tls, "smtp_user": user, "smtp_password": password,
            "smtp_ca_file": env.get("EVREN_SMTP_CA_FILE") or None, "mail_from": mail_from, "teams_webhooks": teams,
            "notify_simulate": simulate}
