"""Bağlayıcıların HTTP katmanı (U5, ADR-0020): standart kütüphane, güvenli varsayılanlar, değiştirilebilir taşıma.

Yeni bağımlılık yoktur (ADR-0018'deki OpenSearch istemcisiyle aynı yaklaşım). İstemci yalnızca JSON konuşur.

* **TLS:** ``https`` zorunludur ve sertifika zinciri ile ana bilgisayar adı doğrulanır (kurum CA dosyası verilebilir).
  Düz ``http`` yalnızca açık izinle (``allow_http``) ve yerel geliştirme içindir.
* **Kimlik bilgisi:** yalnızca dosyadan okunur (``*_file``); adreste kullanıcı bilgisi reddedilir; ``repr`` ve hata
  iletileri sır, başlık ve sunucu gövdesi içermez.
* **Yeniden deneme:** bağlantı hatası, 429 ve 5xx yanıtlarında geri çekilmeli yeniden dener; ``Retry-After`` başlığına
  uyar. 4xx (429 dışı) kalıcı hatadır, yeniden denenmez.
* **Taşıma:** ``Transport`` sözleşmesi testte kaydedilmiş yanıtlarla (``ReplayTransport``) veya bir ASGI test
  istemcisiyle değiştirilir; böylece kaynak istemcileri ve alım geçidi gerçek ağ olmadan uçtan uca sınanır.
"""
from __future__ import annotations

import base64
import hashlib
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

__all__ = ["HttpClient", "HttpError", "HttpResponse", "RecordingTransport", "ReplayTransport", "Transport",
           "UrllibTransport", "read_secret", "request_fingerprint"]

MAX_BODY = 64 * 1024 * 1024  # tek yanıt için üst sınır (bayt)


class HttpError(RuntimeError):
    """Kaynak veya geçit isteği başarısız. İleti sır ve sunucu gövdesi içermez."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8")) if self.body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(f"Yanıt JSON değil (durum {self.status})", self.status) from None


class Transport(Protocol):
    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse: ...


def read_secret(path: str | Path | None, what: str) -> str:
    """Sırrı dosyadan okur (son satır sonu atılır). Dosya yoksa veya boşsa açık bir hata verir; sırrı yazmaz."""
    if not path:
        raise HttpError(f"{what} için sır dosyası tanımlı değil")
    p = Path(path)
    try:
        value = p.read_text(encoding="utf-8").strip()
    except OSError:
        raise HttpError(f"{what} sır dosyası okunamadı: {p.name}") from None
    if not value:
        raise HttpError(f"{what} sır dosyası boş: {p.name}")
    return value


CREDENTIAL_FIELDS = frozenset({"password", "userName", "username", "user", "secret", "client_secret"})
SENSITIVE_RESPONSE_HEADERS = frozenset({"vmware-api-session-id", "set-cookie", "x-auth-token"})
RECORDED_HEADERS = SENSITIVE_RESPONSE_HEADERS | {"content-type"}


def _masked_body(body: bytes | None) -> bytes:
    """Gövdedeki kimlik alanlarının değerleri parmak izine girmez (kayıtta parola özeti olmasın)."""
    if not body:
        return b""
    try:
        doc = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body

    def mask(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: ("***" if k in CREDENTIAL_FIELDS else mask(x)) for k, x in v.items()}
        if isinstance(v, list):
            return [mask(x) for x in v]
        return v

    return json.dumps(mask(doc), ensure_ascii=False, sort_keys=False).encode("utf-8")


def request_fingerprint(method: str, url: str, body: bytes | None) -> str:
    """Kaydedilmiş yanıt eşlemesi için istek parmak izi: yöntem + yol + sorgu + gövde özeti (alan sırası korunur).

    Kimlik alanları (``password``, ``userName`` …) özetten önce maskelenir: kayıt dosyası parolanın özetini taşımaz ve
    oynatma gerçek kimlik bilgisi istemez."""
    parts = urllib.parse.urlsplit(url)
    digest = hashlib.sha256(_masked_body(body)).hexdigest()[:16]
    return f"{method.upper()} {parts.path}{'?' + parts.query if parts.query else ''} #{digest}"


class UrllibTransport:
    def __init__(self, *, ca_file: str | None = None, allow_http: bool = False) -> None:
        self.allow_http = allow_http
        self.context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        scheme = urllib.parse.urlsplit(url).scheme
        if scheme != "https" and not (scheme == "http" and self.allow_http):
            raise HttpError("Yalnızca https adresine bağlanılır (düz http yalnızca açık izinle)")
        req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))  # noqa: S310 - şema denetlendi
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.context) as resp:  # noqa: S310  # nosec B310
                data = resp.read(MAX_BODY + 1)
                if len(data) > MAX_BODY:
                    raise HttpError("Yanıt boyut sınırını aştı", resp.status)
                return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()}, data)
        except urllib.error.HTTPError as exc:
            data = exc.read(MAX_BODY) if exc.fp else b""
            return HttpResponse(exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, data)
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError) as exc:
            reason = type(getattr(exc, "reason", exc)).__name__
            raise HttpError(f"Bağlantı kurulamadı ({reason})", retryable=True) from None


@dataclass
class ReplayTransport:
    """Kaydedilmiş yanıtlarla taşıma (testler, demo, kurum dışı geliştirme).

    ``responses``: istek parmak izi (``request_fingerprint``) → (durum, gövde). Kayıtlı olmayan istek hata verir;
    böylece istemci kodu beklenmedik bir istek yaparsa test kırmızıya döner. ``calls`` yapılan istekleri sırayla tutar.
    """

    responses: dict[str, tuple[int, Any]]
    calls: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, dict[str, str]] = field(default_factory=dict)  # parmak izi → yanıt başlıkları

    @classmethod
    def from_dir(cls, directory: str | Path) -> ReplayTransport:
        """``<dizin>/replay.json``: ``{"options": {...}, "responses": {"<parmak izi>": {"status": 200, "file": "…"}}}``.

        ``options`` kayıt sırasında kullanılan istemci ayarlarıdır (ör. sayfa boyutu); yeniden oynatmada aynısı
        kullanılmalıdır, yoksa istekler kayıtla eşleşmez."""
        d = Path(directory)
        index = json.loads((d / "replay.json").read_text(encoding="utf-8"))
        responses: dict[str, tuple[int, Any]] = {}
        headers: dict[str, dict[str, str]] = {}
        for fp, v in index["responses"].items():
            responses[fp] = (int(v.get("status", 200)), json.loads((d / v["file"]).read_text(encoding="utf-8")))
            headers[fp] = {str(k).lower(): str(x) for k, x in (v.get("headers") or {}).items()}
        return cls(responses, options=dict(index.get("options") or {}), headers=headers)

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        fp = request_fingerprint(method, url, body)
        self.calls.append(fp)
        if fp not in self.responses:
            raise HttpError(f"Kayıtlı yanıt yok: {fp}")
        status, payload = self.responses[fp]
        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        return HttpResponse(status, {"content-type": "application/json", **self.headers.get(fp, {})}, data)


class RecordingTransport:
    """Gerçek (veya simüle) taşımayı sarar ve her yanıtı ``ReplayTransport`` biçiminde kaydeder.

    Kurum ortamında bir kez gerçek kaynağa karşı çalıştırılır. Kayıt sır içermez: istek parmak izinde kimlik alanları
    maskelenir, oturum kimliği gibi gizli yanıt başlıkları yer tutucuyla saklanır, istek başlıkları hiç saklanmaz.
    Yanıt gövdeleri kurum verisidir; repoya konmadan önce kişisel veri açısından gözden geçirilmelidir.
    """

    def __init__(self, inner: Transport, directory: str | Path, options: Mapping[str, Any] | None = None) -> None:
        self.inner, self.dir = inner, Path(directory)
        self.index: dict[str, Any] = {"options": dict(options or {}), "responses": {}}

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        resp = self.inner.send(method, url, headers, body, timeout)
        fp = request_fingerprint(method, url, body)
        name = f"{len(self.index['responses']) + 1:03d}.json"
        try:
            payload = resp.json()
        except HttpError:
            payload = None  # JSON olmayan hata gövdesi kaydedilmez
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        entry: dict[str, Any] = {"status": resp.status, "file": name}
        kept = {k: ("kayit-yer-tutucu" if k in SENSITIVE_RESPONSE_HEADERS else v)  # oturum kimliği diske yazılmaz
                for k, v in resp.headers.items() if k in RECORDED_HEADERS and k != "content-type"}
        if kept:
            entry["headers"] = kept
        self.index["responses"][fp] = entry
        return resp

    def save(self) -> None:
        (self.dir / "replay.json").write_text(json.dumps(self.index, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")


class HttpClient:
    """JSON HTTP istemcisi: temel adres, kimlik doğrulama başlığı, yeniden deneme. Sırları ``repr``'da göstermez."""

    def __init__(self, base_url: str, *, transport: Transport | None = None, auth_header: str | None = None,
                 extra_headers: Mapping[str, str] | None = None, timeout: float = 30.0, retries: int = 4,
                 backoff: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> None:
        parts = urllib.parse.urlsplit(base_url)
        if parts.username or parts.password:
            raise HttpError("Adreste kullanıcı bilgisi olamaz; kimlik bilgisi dosyadan verilir")
        if parts.scheme not in ("https", "http"):
            raise HttpError("Adres https:// ile başlamalı")
        self.base_url = base_url.rstrip("/")
        self.transport = transport or UrllibTransport()
        self._auth = auth_header
        self.extra_headers = dict(extra_headers or {})
        self.timeout, self.retries, self.backoff, self.sleep = timeout, retries, backoff, sleep

    def __repr__(self) -> str:
        return f"HttpClient({self.base_url!r}, auth={'var' if self._auth else 'yok'})"

    @staticmethod
    def bearer(token: str) -> str:
        return f"Bearer {token}"

    @staticmethod
    def basic(user: str, password: str) -> str:
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")

    def request(self, method: str, path: str, payload: Any = None, *, params: Mapping[str, Any] | None = None,
                ok: tuple[int, ...] = (200, 201, 202)) -> HttpResponse:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers = {"Accept": "application/json", **self.extra_headers}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self._auth:
            headers["Authorization"] = self._auth
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.transport.send(method, url, headers, body, self.timeout)
            except HttpError as exc:
                if exc.retryable and attempt <= self.retries:
                    self.sleep(self.backoff * 2 ** (attempt - 1))
                    continue
                raise
            if resp.status in ok:
                return resp
            retryable = resp.status == 429 or resp.status >= 500
            if retryable and attempt <= self.retries:
                self.sleep(self._retry_after(resp) or self.backoff * 2 ** (attempt - 1))
                continue
            raise HttpError(f"{method} {urllib.parse.urlsplit(url).path} başarısız: durum {resp.status}",
                            resp.status, retryable)

    @staticmethod
    def _retry_after(resp: HttpResponse) -> float | None:
        try:
            return min(float(resp.headers.get("retry-after", "")), 120.0)
        except ValueError:
            return None

    def get_json(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params).json()

    def post_json(self, path: str, payload: Any, *, ok: tuple[int, ...] = (200, 201, 202)) -> Any:
        return self.request("POST", path, payload, ok=ok).json()
