"""Microsoft Defender for Endpoint istemcisi (F1-02, U5.2): makine envanteri (Uç nokta güvenliği rolü).

* **Kimlik doğrulama:** Entra istemci kimliği akışı (client credentials). ``POST {login}/{kiracı}/oauth2/v2.0/token``
  form gövdesiyle (``client_id``, ``client_secret``, ``scope``, ``grant_type``); istemci sırrı dosyadan okunur.
  Uygulama izni yalnızca okuma (``Machine.Read.All``). Belirteç bellekte tutulur, günlüğe ve kayda yazılmaz; API 401
  dönerse bir kez yenilenir (uzun çalışmada süresi dolabilir).
* **Uç:** ``GET {api}/api/machines?$top=…`` (ör. ``https://api.security.microsoft.com``). Sayfa en fazla 10.000.
* **Sayfalama:** yanıttaki ``@odata.nextLink`` izlenir, ama yalnızca API ile aynı şema ve ana bilgisayardaysa; adresin
  yolu ve sorgusu alınır, istek istemcinin temel adresine yapılır. Başka adrese giden bağlantı hata verir (belirteç
  başka bir sunucuya gitmez).
* **Kapsam:** sunucu işletim sistemleri eşleme dosyasında süzülür (``include_when``). Risk ve maruziyet alanları kısıtlı
  veridir; yalnızca ``cmdb.ci_security_posture``'a gider (eşleme bunları CI özniteliği yapmaz).
* **Hız sınırı:** API dakikada 100 çağrıyla sınırlıdır; 429 ve ``Retry-After`` HTTP katmanında karşılanır.

``DefenderSimulator`` belirteç ucunu ve makine listesini örnek veriyle yanıtlar.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..http import HttpClient, HttpError, HttpResponse

__all__ = ["DEFAULT_SCOPE", "DefenderClient", "DefenderSimulator"]

DEFAULT_SCOPE = "https://api.securitycenter.microsoft.com/.default"
_TENANT = re.compile(r"^[0-9a-fA-F-]{36}$|^[a-z0-9.-]+\.onmicrosoft\.com$")


class DefenderClient:
    data_source = "Defender"

    def __init__(self, http: HttpClient, login: HttpClient, *, tenant: str, client_id: str, client_secret: str,
                 scope: str = DEFAULT_SCOPE, page_size: int = 1000) -> None:
        if not _TENANT.match(tenant):
            raise HttpError("Entra kiracı kimliği geçersiz (GUID veya <ad>.onmicrosoft.com)")
        if not 1 <= page_size <= 10000:
            raise HttpError("Sayfa boyutu 1–10000 olmalı")
        self.http, self.login = http, login
        self._tenant, self._client_id, self._secret, self._scope = tenant, client_id, client_secret, scope
        self.page_size = page_size
        self.token_requests = 0

    def __repr__(self) -> str:
        return f"DefenderClient({self.http!r})"

    def _authenticate(self) -> None:
        self.token_requests += 1
        doc = self.login.post_form(f"/{self._tenant}/oauth2/v2.0/token", {
            "grant_type": "client_credentials", "client_id": self._client_id, "client_secret": self._secret,
            "scope": self._scope})
        token = doc.get("access_token") if isinstance(doc, dict) else None
        if not isinstance(token, str) or not token:
            raise HttpError("Entra belirteç yanıtında erişim belirteci yok")
        self.http.extra_headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str) -> dict[str, Any]:
        for attempt in (1, 2):
            try:
                doc = self.http.request("GET", path).json()
            except HttpError as exc:
                if exc.status == 401 and attempt == 1:  # belirteç süresi doldu: bir kez yenile
                    self._authenticate()
                    continue
                raise
            if not isinstance(doc, dict) or not isinstance(doc.get("value"), list):
                raise HttpError("Defender yanıtı beklenen biçimde değil")
            return doc
        raise HttpError("Defender kimlik doğrulaması başarısız")  # pragma: no cover

    def _next_path(self, link: Any) -> str:
        """``@odata.nextLink`` yalnızca API ile aynı şema/ana bilgisayarda izlenir; yol + sorgu döner."""
        if not isinstance(link, str):
            raise HttpError("Defender sonraki sayfa bağlantısı geçersiz")
        nxt, base = urllib.parse.urlsplit(link), urllib.parse.urlsplit(self.http.base_url)
        if (nxt.scheme, nxt.netloc.lower()) != (base.scheme, base.netloc.lower()) or not nxt.path.startswith("/api/"):
            raise HttpError("Defender sonraki sayfa bağlantısı başka bir adrese gidiyor; izlenmedi")
        return nxt.path + ("?" + nxt.query if nxt.query else "")

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        """Her çalışma tam listedir (``lastSeen`` süzgeci gerçek bir değişiklik süzgeci değildir)."""
        try:
            self._authenticate()
            path: str | None = f"/api/machines?$top={self.page_size}"
            while path:
                doc = self._get(path)
                for row in doc["value"]:
                    if not isinstance(row, dict) or not row.get("id"):
                        raise HttpError("Defender makine kaydında kimlik yok")
                    yield row
                link = doc.get("@odata.nextLink")
                path = self._next_path(link) if link else None
        finally:
            self.http.extra_headers.pop("Authorization", None)  # belirteç istemcide kalmaz


class DefenderSimulator:
    """Entra belirteç ucu + ``/api/machines`` alt kümesi (``Transport``). Örnek: ``connectors/defender/samples``.

    ``login`` ve ``api`` ana bilgisayarlarını tanır; yanlış sır 401, belirteçsiz API çağrısı 401. ``expire_after``:
    verilen sayıdaki API çağrısından sonra belirteci geçersiz sayar (yenileme sınaması). ``foreign_next``: sonraki
    sayfa bağlantısını başka bir ana bilgisayara verir (izlenmemeli).
    """

    API = "https://api.security.microsoft.com"

    def __init__(self, machines: list[dict[str, Any]], *, secret: str | None = None, expire_after: int | None = None,
                 foreign_next: bool = False) -> None:
        self.machines, self.secret = machines, secret
        self.expire_after, self.foreign_next = expire_after, foreign_next
        self.tokens: list[str] = []
        self.api_calls = 0
        self.forms: list[dict[str, str]] = []

    @classmethod
    def from_dir(cls, directory: str | Path, **kw: Any) -> DefenderSimulator:
        return cls(json.loads((Path(directory) / "machines.json").read_text(encoding="utf-8"))["machines"], **kw)

    @staticmethod
    def _json(status: int, payload: Any) -> HttpResponse:
        return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        if parts.path.endswith("/oauth2/v2.0/token"):
            form = dict(urllib.parse.parse_qsl((body or b"").decode("ascii")))
            self.forms.append(form)
            if form.get("grant_type") != "client_credentials" or (self.secret and form.get("client_secret")
                                                                  != self.secret):
                return self._json(401, {"error": "invalid_client", "error_description": "AADSTS7000215: gizli"})
            token = f"sim-token-{len(self.tokens) + 1}"
            self.tokens.append(token)
            return self._json(200, {"token_type": "Bearer", "expires_in": 3599, "access_token": token})
        auth = str(headers.get("Authorization", ""))
        valid = self.tokens[-1:] if self.tokens else []
        self.api_calls += 1
        if self.expire_after is not None and self.api_calls > self.expire_after and len(self.tokens) == 1:
            valid = []  # ilk belirtecin süresi doldu
        if auth not in {f"Bearer {t}" for t in valid}:
            return self._json(401, {"error": {"code": "Unauthorized", "message": "gizli ayrıntı"}})
        if method != "GET" or parts.path != "/api/machines":
            return self._json(404, {"error": {"code": "NotFound"}})
        q = dict(urllib.parse.parse_qsl(parts.query))
        top, skip = int(q.get("$top", "10000")), int(q.get("$skip", "0"))
        page: dict[str, Any] = {"@odata.context": f"{self.API}/api/$metadata#Machines",
                                "value": self.machines[skip:skip + top]}
        if skip + top < len(self.machines):
            host = "https://kotu.example" if self.foreign_next else self.API
            page["@odata.nextLink"] = f"{host}/api/machines?$top={top}&$skip={skip + top}"
        return self._json(200, page)
