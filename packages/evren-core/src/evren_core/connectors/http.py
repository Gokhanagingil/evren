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


CREDENTIAL_FIELDS = frozenset({"password", "userName", "username", "user", "secret", "client_secret",
                               "client_assertion", "client_id"})
# Yanıt gövdesinde kayda yazılmayacak sırlar (OAuth belirteç yanıtı): değer yer tutucuyla saklanır.
RESPONSE_SECRET_FIELDS = frozenset({"access_token", "refresh_token", "id_token"})
SENSITIVE_RESPONSE_HEADERS = frozenset({"vmware-api-session-id", "set-cookie", "x-auth-token"})
RECORDED_HEADERS = SENSITIVE_RESPONSE_HEADERS | {"content-type"}
PLACEHOLDER = "kayit-yer-tutucu"


def _mask(v: Any, fields: frozenset[str], placeholder: str = "***") -> Any:
    if isinstance(v, dict):
        return {k: (placeholder if k in fields else _mask(x, fields, placeholder)) for k, x in v.items()}
    if isinstance(v, list):
        return [_mask(x, fields, placeholder) for x in v]
    return v


def _masked_body(body: bytes | None) -> bytes:
    """Gövdedeki kimlik alanlarının değerleri parmak izine girmez (kayıtta parola veya istemci sırrı özeti olmasın).
    JSON ve form kodlu (``application/x-www-form-urlencoded``) gövdeler maskelenir; başka biçim tümüyle atılır."""
    if not body:
        return b""
    try:
        return json.dumps(_mask(json.loads(body), CREDENTIAL_FIELDS), ensure_ascii=False).encode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    try:
        pairs = urllib.parse.parse_qsl(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        return b"<govde>"  # tanınmayan biçim: içerik parmak izine girmez
    return urllib.parse.urlencode([(k, "***" if k in CREDENTIAL_FIELDS else v) for k, v in pairs]).encode("ascii")


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
            text = (d / v["file"]).read_text(encoding="utf-8")
            # ham gövde (XML/SOAP): bayt olarak döner; JSON: çözülür
            responses[fp] = (int(v.get("status", 200)), text.encode("utf-8") if v.get("raw") else json.loads(text))
            headers[fp] = {str(k).lower(): str(x) for k, x in (v.get("headers") or {}).items()}
            if v.get("raw"):
                headers[fp]["content-type"] = str(v.get("content_type") or "application/octet-stream")
        return cls(responses, options=dict(index.get("options") or {}), headers=headers)

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        fp = request_fingerprint(method, url, body)
        self.calls.append(fp)
        if fp not in self.responses:
            raise HttpError(f"Kayıtlı yanıt yok: {fp}")
        status, payload = self.responses[fp]
        if isinstance(payload, bytes):
            data = payload
        else:
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
        n = len(self.index["responses"]) + 1
        ctype = str(resp.headers.get("content-type", ""))
        self.dir.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {"status": resp.status}
        if "xml" in ctype and resp.body:  # SOAP/XML yanıtı ham saklanır (sır içermez; kimlik başlıkta)
            name = f"{n:03d}.xml"
            (self.dir / name).write_text(resp.body.decode("utf-8", errors="replace"), encoding="utf-8")
            entry |= {"raw": True, "content_type": ctype}
        else:
            name = f"{n:03d}.json"
            try:
                payload = _mask(resp.json(), RESPONSE_SECRET_FIELDS, PLACEHOLDER)  # erişim belirteci diske yazılmaz
            except HttpError:
                payload = None  # JSON olmayan hata gövdesi kaydedilmez
            (self.dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        entry["file"] = name
        kept = {k: (PLACEHOLDER if k in SENSITIVE_RESPONSE_HEADERS else v)  # oturum kimliği diske yazılmaz
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
                ok: tuple[int, ...] = (200, 201, 202), form: Mapping[str, str] | None = None,
                raw: bytes | None = None, content_type: str | None = None,
                extra: Mapping[str, str] | None = None) -> HttpResponse:
        """JSON (``payload``), form (``form``) veya ham gövde (``raw`` + ``content_type``, ör. SOAP) gönderir."""
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Accept": "application/json", **self.extra_headers, **(extra or {})}
        if raw is not None:
            body: bytes | None = raw
            headers["Content-Type"] = content_type or "application/octet-stream"
            headers["Accept"] = content_type or "*/*"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = None if payload is None else json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
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

    def post_form(self, path: str, fields: Mapping[str, str]) -> Any:
        """Form kodlu POST (OAuth belirteç ucu). Gövde sır içerebilir; hata iletisine yazılmaz."""
        return self.request("POST", path, form=fields).json()

    def post_json(self, path: str, payload: Any, *, ok: tuple[int, ...] = (200, 201, 202)) -> Any:
        return self.request("POST", path, payload, ok=ok).json()
