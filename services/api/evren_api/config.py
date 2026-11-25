"""Yapılandırma: yalnızca ortam değişkenleri; sırlar dosya yolundan (``*_FILE``) okunur ve loglanmaz.

Güvenli varsayılanlar:
* ``EVREN_AUTH_MODE`` zorunludur; anonim erişim yoktur.
* ``dev`` modu yalnızca geri döngü adresinde (127.0.0.1, ::1) çalışır ve oturum açtırmaz. Geliştirme yığınında
  (compose) konteyner içinden yayın için ``EVREN_DEV_CONTAINER=1`` açıkça verilir: o zaman tüm arayüzlerde dinler
  ama yalnızca geri döngü ve özel ağ adreslerinden gelen isteklere yanıt verir; compose portu ana makinede
  yalnızca 127.0.0.1'e yayınlar (ADR-0016).
* ``entra`` modu HTTPS çerezi (``EVREN_COOKIE_SECURE=1``) ve dosyadan okunan oturum anahtarı ister.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet

from .entra import EntraConfig, configure_entra

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DEV_ROLES = ("evren_reader", "evren_match_reviewer")


class ConfigError(ValueError):
    """Yapılandırma hatası; mesaj sır içermez."""


def _read_secret(env: Mapping[str, str], name: str) -> str | None:
    path = env.get(f"{name}_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return env.get(name) or None


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    host: str = "127.0.0.1"
    port: int = 8088
    db_conninfo: str = field(default="", repr=False)
    db_role: str = "evren_app_api"
    db_pool_size: int = 10
    statement_timeout_ms: int = 5000
    cookie_secure: bool = False
    session_key: bytes = field(default=b"", repr=False)
    session_hours: int = 8
    dev_user: str = "dev"
    dev_name: str = "Geliştirici"
    dev_roles: tuple[str, ...] = DEFAULT_DEV_ROLES
    web_root: Path = REPO_ROOT / "apps" / "web"
    entra: EntraConfig | None = None
    dev_container: bool = False

    @property
    def session_cookie(self) -> str:
        return "__Host-evren_session" if self.cookie_secure else "evren_session"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        mode = env.get("EVREN_AUTH_MODE", "")
        if mode not in ("dev", "entra"):
            raise ConfigError("EVREN_AUTH_MODE zorunlu: dev veya entra")
        host = env.get("EVREN_HOST", "127.0.0.1")
        secure = env.get("EVREN_COOKIE_SECURE") == "1"
        dev_container = env.get("EVREN_DEV_CONTAINER") == "1"
        if dev_container and mode != "dev":
            raise ConfigError("EVREN_DEV_CONTAINER yalnızca dev modunda geçerlidir")
        if mode == "dev":
            # Tüm arayüzler yalnızca açıkça istenen geliştirme konteynerinde; istemci adresi süzgeci app.py'de.
            if not _loopback(host) and not (dev_container and host in ("0.0.0.0", "::")):  # noqa: S104
                raise ConfigError("dev modu yalnızca geri döngü adresinde çalışır (EVREN_HOST=127.0.0.1); "
                                  "geliştirme konteynerinde EVREN_DEV_CONTAINER=1 ve EVREN_HOST=0.0.0.0")
            if any(k.startswith("EVREN_ENTRA_") and v for k, v in env.items()):
                raise ConfigError("Entra yapılandırması için EVREN_AUTH_MODE=entra gerekir")
            key = (_read_secret(env, "EVREN_SESSION_KEY") or Fernet.generate_key().decode()).encode()
            entra = None
        else:
            if not secure:
                raise ConfigError("entra modu HTTPS çerezi ister: EVREN_COOKIE_SECURE=1")
            secret_key = _read_secret(env, "EVREN_SESSION_KEY")
            if not secret_key:
                raise ConfigError("entra modu oturum anahtarı ister: EVREN_SESSION_KEY_FILE")
            key = secret_key.encode()
            entra = configure_entra(env)
        try:
            Fernet(key)
        except ValueError:
            raise ConfigError("Oturum anahtarı geçerli bir Fernet anahtarı değil") from None
        roles = tuple(r.strip() for r in env.get("EVREN_DEV_ROLES", ",".join(DEFAULT_DEV_ROLES)).split(",") if r.strip())
        return cls(
            auth_mode=mode, host=host, port=int(env.get("EVREN_PORT", "8088")),
            db_conninfo=_read_secret(env, "EVREN_DATABASE_URL") or "",
            db_role=env.get("EVREN_DB_ROLE", "evren_app_api"),
            db_pool_size=int(env.get("EVREN_DB_POOL_SIZE", "10")),
            cookie_secure=secure, session_key=key,
            session_hours=int(env.get("EVREN_SESSION_HOURS", "8")),
            dev_user=env.get("EVREN_DEV_USER", "dev"), dev_name=env.get("EVREN_DEV_NAME", "Geliştirici"),
            dev_roles=roles, entra=entra, dev_container=dev_container,
        )
