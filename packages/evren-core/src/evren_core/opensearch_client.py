"""OpenSearch G/Ç adaptörü (U3, ADR-0018) — standart kütüphaneyle ince REST istemcisi; çekirdekte OpenSearch'e
dokunan TEK modül.

Neden ek bağımlılık yok: kullanılan uçlar (dizin oluşturma, toplu yazım, arama, takma ad) basit JSON/HTTPS'tir;
resmi istemci urllib3, requests, certifi ve dateutil zinciri getirir. Bağlantı güvenli varsayılanlıdır: HTTPS ve
sertifika doğrulaması; düz HTTP yalnızca ``EVREN_OPENSEARCH_ALLOW_HTTP=1`` ile (yerel geliştirme). Parola yalnızca
dosyadan okunur, ``repr``'da ve hata iletilerinde görünmez.
"""
from __future__ import annotations

import base64
import json
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

__all__ = ["OpenSearch", "OpenSearchConfigError", "OpenSearchError", "OpenSearchSettings"]

_INDEX = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,200}$")


class OpenSearchConfigError(ValueError):
    pass


class OpenSearchError(RuntimeError):
    """İstek başarısız (bağlantı veya HTTP durumu). İleti yalnızca durum ve tür içerir (yanıt gövdesi yansıtılmaz)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class OpenSearchSettings:
    url: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    ca_file: str | None = None
    timeout: float = 10.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> OpenSearchSettings:
        url = (env.get("EVREN_OPENSEARCH_URL") or "").strip().rstrip("/")
        if not url:
            raise OpenSearchConfigError("EVREN_OPENSEARCH_URL tanımlı değil")
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.path not in ("", "/"):
            raise OpenSearchConfigError("EVREN_OPENSEARCH_URL https://sunucu:port biçiminde olmalı")
        if parts.username or parts.password:
            raise OpenSearchConfigError("Kimlik bilgisi adrese yazılmaz: EVREN_OPENSEARCH_USERNAME / _PASSWORD_FILE")
        if parts.scheme == "http" and env.get("EVREN_OPENSEARCH_ALLOW_HTTP") != "1":
            raise OpenSearchConfigError("Düz HTTP yalnızca EVREN_OPENSEARCH_ALLOW_HTTP=1 ile (yerel geliştirme)")
        password = None
        if pfile := env.get("EVREN_OPENSEARCH_PASSWORD_FILE"):
            try:
                password = Path(pfile).read_text(encoding="utf-8").strip()
            except OSError:
                raise OpenSearchConfigError("OpenSearch parola dosyası okunamadı") from None
        username = env.get("EVREN_OPENSEARCH_USERNAME") or None
        if bool(username) != bool(password):
            raise OpenSearchConfigError("EVREN_OPENSEARCH_USERNAME ve EVREN_OPENSEARCH_PASSWORD_FILE birlikte verilmeli")
        return cls(url, username, password, env.get("EVREN_OPENSEARCH_CA_FILE") or None)


class OpenSearch:
    def __init__(self, settings: OpenSearchSettings) -> None:
        self.settings = settings
        self._ctx: ssl.SSLContext | None = None
        if settings.url.startswith("https://"):
            self._ctx = ssl.create_default_context(cafile=settings.ca_file)  # ana bilgisayar adı ve zincir doğrulanır
        self._auth = None
        if settings.username and settings.password:
            raw = f"{settings.username}:{settings.password}".encode()
            self._auth = "Basic " + base64.b64encode(raw).decode()

    # ------------------------------------------------------------------ temel istek
    def request(self, method: str, path: str, body: Any = None, *, ndjson: bool = False,
                ok: Iterable[int] = (200, 201)) -> Any:
        if not path.startswith("/"):
            raise ValueError("yol / ile başlamalı")
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            if ndjson:
                data = ("\n".join(json.dumps(line, ensure_ascii=False) for line in body) + "\n").encode()
                headers["Content-Type"] = "application/x-ndjson"
            else:
                data = json.dumps(body, ensure_ascii=False).encode()
                headers["Content-Type"] = "application/json"
        if self._auth:
            headers["Authorization"] = self._auth
        req = urllib.request.Request(self.settings.url + path, data=data, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=self.settings.timeout, context=self._ctx) as resp:  # nosec B310  # noqa: S310
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (OSError, ValueError) as exc:
            raise OpenSearchError(f"OpenSearch'e ulaşılamadı ({type(exc).__name__})") from None
        if status not in set(ok):
            raise OpenSearchError(f"OpenSearch isteği başarısız: {method} {path.split('?')[0]} → {status}", status)
        return json.loads(raw) if raw else None

    @staticmethod
    def _name(index: str) -> str:
        if not _INDEX.fullmatch(index):
            raise ValueError(f"Geçersiz dizin adı: {index}")
        return quote(index, safe="")

    # ------------------------------------------------------------------ işlemler
    def ping(self) -> bool:
        try:
            health = self.request("GET", "/_cluster/health")
        except OpenSearchError:
            return False
        return health.get("status") in ("green", "yellow")

    def create_index(self, index: str, body: Mapping[str, Any]) -> None:
        self.request("PUT", f"/{self._name(index)}", body)

    def delete_index(self, index: str) -> None:
        self.request("DELETE", f"/{self._name(index)}", ok=(200, 404))

    def alias_targets(self, alias: str) -> list[str]:
        res = self.request("GET", f"/_alias/{self._name(alias)}", ok=(200, 404))
        return sorted(k for k in (res or {}) if k not in ("error", "status"))

    def point_alias(self, alias: str, index: str) -> list[str]:
        """Takma adı tek işlemde yeni dizine taşır; eski dizin adlarını döndürür."""
        old = self.alias_targets(alias)
        actions = [{"remove": {"index": o, "alias": alias}} for o in old] + [{"add": {"index": index, "alias": alias}}]
        self.request("POST", "/_aliases", {"actions": actions})
        return [o for o in old if o != index]

    def bulk_index(self, index: str, docs: Iterable[Mapping[str, Any]], *, refresh: bool = False) -> int:
        """Belgeleri ``sys_id`` kimliğiyle yazar (aynı CI üzerine yazılır). Başarısız öğe varsa ``OpenSearchError``."""
        lines: list[Any] = []
        for d in docs:
            lines += [{"index": {"_index": index, "_id": d["sys_id"]}}, d]
        if not lines:
            return 0
        res = self.request("POST", "/_bulk" + ("?refresh=wait_for" if refresh else ""), lines, ndjson=True)
        if res.get("errors"):
            failed = sum(1 for it in res.get("items", []) if it.get("index", {}).get("status", 200) >= 300)
            raise OpenSearchError(f"Toplu yazımda {failed} belge başarısız")
        return len(lines) // 2

    def search(self, index: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/{self._name(index)}/_search", body)
