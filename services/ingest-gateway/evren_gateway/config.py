"""Alım geçidi yapılandırması (``EVREN_GATEWAY_*``, ``EVREN_DATABASE_URL_FILE``, ``EVREN_KAFKA_*``).

Güvenli varsayılanlar: yalnızca loopback'e bağlanır; ağa açılmak için TLS sertifikası (isteğe bağlı istemci CA ile
mTLS) veya açık ``EVREN_GATEWAY_INSECURE_HTTP=1`` (yalnızca geliştirme konteyneri) gerekir. Sırlar dosyadan okunur.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from evren_core.messaging import KafkaSettings

REPO_ROOT = Path(__file__).resolve().parents[3]


class ConfigError(ValueError):
    pass


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    try:
        value = int(raw) if raw else default
    except ValueError:
        raise ConfigError(f"{name} sayı olmalı") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} {lo}–{hi} aralığında olmalı")
    return value


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class GatewaySettings:
    db_conninfo: str = field(repr=False)
    kafka: KafkaSettings
    db_role: str = "evren_app_ingest"
    host: str = "127.0.0.1"
    port: int = 8089
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    tls_client_ca_file: str | None = None
    insecure_http: bool = False
    max_body_bytes: int = 8 * 1024 * 1024
    max_batch: int = 500
    publish_timeout_s: int = 30
    statement_timeout_ms: int = 10_000
    db_pool_size: int = 8
    metadata_dir: Path = REPO_ROOT / "metadata"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GatewaySettings:
        path = env.get("EVREN_DATABASE_URL_FILE")
        try:
            conninfo = Path(path).read_text(encoding="utf-8").strip() if path else env.get("EVREN_DATABASE_URL", "")
        except OSError:
            raise ConfigError("EVREN_DATABASE_URL_FILE okunamadı") from None
        try:
            kafka = KafkaSettings.from_env(env)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        host = env.get("EVREN_GATEWAY_HOST", "127.0.0.1")
        cert, key = env.get("EVREN_GATEWAY_TLS_CERT_FILE") or None, env.get("EVREN_GATEWAY_TLS_KEY_FILE") or None
        if bool(cert) != bool(key):
            raise ConfigError("EVREN_GATEWAY_TLS_CERT_FILE ve EVREN_GATEWAY_TLS_KEY_FILE birlikte verilmelidir")
        client_ca = env.get("EVREN_GATEWAY_TLS_CLIENT_CA_FILE") or None
        if client_ca and not cert:
            raise ConfigError("İstemci sertifikası doğrulaması (mTLS) için önce sunucu sertifikası gerekir")
        insecure = env.get("EVREN_GATEWAY_INSECURE_HTTP") == "1"
        if not _loopback(host) and not cert and not insecure:
            raise ConfigError("Geçit ağa TLS olmadan açılamaz: sertifika verin veya yalnızca geliştirme konteynerinde "
                              "EVREN_GATEWAY_INSECURE_HTTP=1 kullanın")
        return cls(
            db_conninfo=conninfo, kafka=kafka, db_role=env.get("EVREN_DB_ROLE", "evren_app_ingest"), host=host,
            port=_int(env, "EVREN_GATEWAY_PORT", 8089, 1, 65535), tls_cert_file=cert, tls_key_file=key,
            tls_client_ca_file=client_ca, insecure_http=insecure,
            max_body_bytes=_int(env, "EVREN_GATEWAY_MAX_BODY_BYTES", 8 * 1024 * 1024, 1024, 64 * 1024 * 1024),
            max_batch=_int(env, "EVREN_GATEWAY_MAX_BATCH", 500, 1, 5000),
            publish_timeout_s=_int(env, "EVREN_GATEWAY_PUBLISH_TIMEOUT", 30, 1, 300),
            metadata_dir=Path(env.get("EVREN_METADATA_DIR") or REPO_ROOT / "metadata"),
        )
