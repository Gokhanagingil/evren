"""Tek kiracılı Microsoft Entra ID web oturumu (OIDC yetkilendirme kodu + PKCE). Belirteçler sunucudan çıkmaz.

Desen GRC Platformu'ndaki ``tedarik/vendor/entra_auth.py`` ile aynıdır (ADR-0005); Evren'e uyarlanmış ayrı bir
uygulamadır. Farklar: uygulama rolleri ``Evren.*`` değerleriyle Evren IAM rollerine eşlenir, ``groups`` talebi
``iam.principal_role`` grup atamaları için döndürülür.

Güvenlik özellikleri:
* PKCE (S256), ``state`` ve ``nonce``: süreç başına anahtarla mühürlenmiş, 5 dakikalık akış çerezinde; tarayıcıya bağlı.
* Aynı akış ikinci kez kullanılamaz (tekrar oynatma); eşzamanlı akış sayısı sınırlı.
* Kimlik belirteci: RS256, kiracı, hedef kitle, düzenleyen, ``nonce``, ``ver=2.0`` ve MFA kimlik doğrulama
  bağlamı (``acrs``) zorunlu. Belirteç yanıtı boyutu sınırlı; yönlendirme izlenmez.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import jwt
from cryptography.fernet import Fernet, InvalidToken

__all__ = ["ENTRA_ROLE_MAP", "Denied", "Entra", "EntraConfig", "EntraIdentity", "Unavailable", "configure_entra"]

FLOW_COOKIE = "__Host-evren_flow"
FLOW_SECONDS = 300
CALLBACK = "/auth/entra/callback"
AUTHORITY = "https://login.microsoftonline.com"
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

#: Entra uygulama rolü (app role value) → Evren IAM rolü (db/migrations/0002_iam_seed.sql).
ENTRA_ROLE_MAP: dict[str, str] = {
    "Evren.Reader": "evren_reader",
    "Evren.CIOwner": "evren_ci_owner",
    "Evren.CMDBEditor": "evren_cmdb_editor",
    "Evren.MatchReviewer": "evren_match_reviewer",
    "Evren.DataSteward": "evren_data_steward",
    "Evren.SchemaDesigner": "evren_schema_designer",
    "Evren.SchemaApprover": "evren_schema_approver",
    "Evren.SecurityReader": "evren_security_reader",
    "Evren.Auditor": "evren_auditor",
    "Evren.IAMAdmin": "evren_iam_admin",
    "Evren.Admin": "evren_admin",
}


class Denied(ValueError):
    """İleti işlemsel bir gerekçedir; hassas bilgi içermez."""


class Unavailable(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class EntraConfig:
    tenant: str
    client: str
    secret: str = field(repr=False)
    redirect: str
    context: str

    @property
    def issuer(self) -> str:
        return f"{AUTHORITY}/{self.tenant}/v2.0"

    @property
    def endpoint(self) -> str:
        return f"{AUTHORITY}/{self.tenant}/oauth2/v2.0"


@dataclass(frozen=True)
class EntraIdentity:
    oid: str
    name: str
    app_roles: tuple[str, ...]
    groups: tuple[str, ...]
    exp: int


def configure_entra(env: Mapping[str, str]) -> EntraConfig:
    tenant = env.get("EVREN_ENTRA_TENANT_ID", "").lower()
    client = env.get("EVREN_ENTRA_CLIENT_ID", "").lower()
    if not GUID.fullmatch(tenant) or not GUID.fullmatch(client):
        raise ValueError("Geçersiz Entra kiracı veya istemci kimliği (GUID)")
    context = env.get("EVREN_ENTRA_AUTH_CONTEXT", "")
    if not re.fullmatch(r"c[1-9][0-9]?", context):
        raise ValueError("Geçersiz Entra kimlik doğrulama bağlamı (c1…c99)")
    redirect = env.get("EVREN_ENTRA_REDIRECT_URI", "")
    uri = urlsplit(redirect)
    if (not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/auth/entra/callback", redirect)
            or not uri.hostname or uri.port == 0):
        raise ValueError("Tam bir HTTPS Entra dönüş adresi gerekir (…/auth/entra/callback)")
    secret = env.get("EVREN_ENTRA_CLIENT_SECRET", "")
    filename = env.get("EVREN_ENTRA_CLIENT_SECRET_FILE", "")
    if bool(secret) == bool(filename):
        raise ValueError("Tam olarak bir Entra istemci kimlik bilgisi yapılandırın")
    if filename:
        secret = Path(filename).read_text("utf-8").strip()
    if not 16 <= len(secret) <= 4096:
        raise ValueError("Geçersiz Entra istemci kimlik bilgisi")
    return EntraConfig(tenant, client, secret, redirect, context)


class Entra:
    def __init__(self, config: EntraConfig, *, jwks: Any | None = None,
                 token_fetch: Callable[[str, str], str] | None = None) -> None:
        self.config = config
        self.sealer = Fernet(Fernet.generate_key())
        self.used: dict[str, float] = {}
        self.inflight: set[str] = set()
        self.lock = threading.Lock()
        self.jwks = jwks or jwt.PyJWKClient(f"{AUTHORITY}/{config.tenant}/discovery/v2.0/keys", timeout=5)
        self._token_fetch = token_fetch or self._exchange

    def begin(self) -> tuple[str, str]:
        """Yetkilendirme adresi ve akış çerezi (Set-Cookie değeri)."""
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        browser = self.sealer.encrypt(json.dumps([state, nonce, verifier]).encode()).decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        claims = {"id_token": {"acrs": {"essential": True, "value": self.config.context}}}
        params = {"client_id": self.config.client, "redirect_uri": self.config.redirect,
                  "response_type": "code", "response_mode": "query", "scope": "openid profile",
                  "state": state, "nonce": nonce, "code_challenge": challenge,
                  "code_challenge_method": "S256", "claims": json.dumps(claims)}
        cookie = f"{FLOW_COOKIE}={browser}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={FLOW_SECONDS}"
        return f"{self.config.endpoint}/authorize?{urlencode(params)}", cookie

    def finish(self, query: str, cookie_header: str) -> EntraIdentity:
        try:
            data = parse_qs(query, max_num_fields=12)
            cookie: SimpleCookie = SimpleCookie()
            cookie.load(cookie_header or "")
            (state,) = data.get("state", [])
            browser = cookie[FLOW_COOKIE].value
            if not state.isascii() or len(browser) > 1024:
                raise Denied("invalid_flow")
            sealed_state, nonce, verifier = json.loads(self.sealer.decrypt(browser.encode("ascii"), ttl=FLOW_SECONDS))
            if not hmac.compare_digest(state, sealed_state):
                raise Denied("invalid_flow")
        except (KeyError, ValueError, CookieError, InvalidToken):
            raise Denied("invalid_flow") from None
        codes = data.get("code", [])
        if "error" in data or len(codes) != 1 or not 1 <= len(codes[0]) <= 16384:
            raise Denied("authorization_declined")
        now = time.monotonic()
        with self.lock:
            self.used = {k: expiry for k, expiry in self.used.items() if expiry > now}
            if state in self.used or state in self.inflight:
                raise Denied("replayed_flow")
            if len(self.inflight) >= 16 or len(self.used) >= 4096:
                raise Unavailable("flow_capacity")
            self.inflight.add(state)
        try:
            identity = self.verify(self._token_fetch(codes[0], verifier), nonce)
            with self.lock:
                self.used[state] = time.monotonic() + FLOW_SECONDS
            return identity
        finally:
            with self.lock:
                self.inflight.discard(state)

    def _exchange(self, code: str, verifier: str) -> str:
        form = urlencode({"grant_type": "authorization_code", "code": code, "client_id": self.config.client,
                          "client_secret": self.config.secret, "redirect_uri": self.config.redirect,
                          "code_verifier": verifier}).encode()
        request = Request(f"{self.config.endpoint}/token", data=form,  # noqa: S310 - sabit https Entra adresi
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with build_opener(_NoRedirect).open(request, timeout=6) as response:  # nosec B310
                raw = response.read(131073)
                if len(raw) > 131072:
                    raise Denied("invalid_token_response")
                return json.loads(raw)["id_token"]
        except HTTPError as exc:
            exc.close()
            raise Denied("code_exchange_rejected") from None
        except (URLError, TimeoutError, OSError):
            raise Unavailable("identity_provider_unavailable") from None
        except (ValueError, TypeError, KeyError):
            raise Denied("invalid_token_response") from None

    def verify_access(self, token: Any) -> EntraIdentity:
        """MCP ve makine istemcileri için Entra erişim belirteci (ADR-0043): RS256, kiracı, düzenleyen (v2.0), hedef
        kitle Evren uygulaması (istemci kimliği veya ``api://<istemci>``), süre. Kullanıcı adına belirteçte roller
        kullanıcının uygulama rolleri; uygulama belirtecinde (``idtyp=app``) makine kimliğinin uygulama rolleri."""
        if not isinstance(token, str) or len(token) > 65536:
            raise Denied("invalid_access_token")
        try:
            header = jwt.get_unverified_header(token)
            if (header.get("alg") != "RS256" or not isinstance(header.get("kid"), str)
                    or not 1 <= len(header["kid"]) <= 256):
                raise Denied("invalid_token_header")
            key = self.jwks.get_signing_key(header["kid"])
            claims = jwt.decode(token, key.key, algorithms=["RS256"],
                                audience=[self.config.client, f"api://{self.config.client}"],
                                issuer=self.config.issuer, options={"require": [
                                    "exp", "iat", "nbf", "iss", "aud", "sub", "tid", "oid", "ver"]})
        except jwt.exceptions.PyJWKClientConnectionError:
            raise Unavailable("signing_keys_unavailable") from None
        except (jwt.PyJWTError, TypeError, OverflowError):
            raise Denied("invalid_access_token") from None
        if (claims["ver"] != "2.0" or claims["tid"] != self.config.tenant
                or not isinstance(claims["oid"], str) or not GUID.fullmatch(claims["oid"])
                or type(claims["exp"]) is not int or not claims["sub"]):
            raise Denied("identity_mismatch")
        roles = claims.get("roles") or []
        groups = claims.get("groups") or []
        if not isinstance(roles, list) or not isinstance(groups, list):
            raise Denied("invalid_claims")
        name = claims.get("name") or claims.get("app_displayname")
        display = name.strip()[:160] if isinstance(name, str) and name.strip() else claims["oid"]
        return EntraIdentity(claims["oid"], display, tuple(r for r in roles if isinstance(r, str)),
                             tuple(g for g in groups if isinstance(g, str) and GUID.fullmatch(g)), claims["exp"])

    def verify(self, token: Any, nonce: str) -> EntraIdentity:
        if not isinstance(token, str) or len(token) > 65536:
            raise Denied("invalid_id_token")
        try:
            header = jwt.get_unverified_header(token)
            if (header.get("alg") != "RS256" or not isinstance(header.get("kid"), str)
                    or not 1 <= len(header["kid"]) <= 256):
                raise Denied("invalid_token_header")
            key = self.jwks.get_signing_key(header["kid"])
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=self.config.client,
                                issuer=self.config.issuer, options={"strict_aud": True, "require": [
                                    "exp", "iat", "nbf", "iss", "aud", "sub", "nonce", "tid", "oid", "ver"]})
        except jwt.exceptions.PyJWKClientConnectionError:
            raise Unavailable("signing_keys_unavailable") from None
        except (jwt.PyJWTError, TypeError, OverflowError):
            raise Denied("invalid_id_token") from None
        if (claims["ver"] != "2.0" or claims["tid"] != self.config.tenant
                or not isinstance(claims["oid"], str) or not GUID.fullmatch(claims["oid"])
                or type(claims["exp"]) is not int or not claims["sub"]
                or not isinstance(claims["nonce"], str) or not claims["nonce"].isascii()
                or not hmac.compare_digest(claims["nonce"], nonce)
                or ("azp" in claims and claims["azp"] != self.config.client)):
            raise Denied("identity_mismatch")
        acrs = claims.get("acrs")
        if not isinstance(acrs, list) or self.config.context not in acrs:
            raise Denied("required_context_missing")
        roles = claims.get("roles") or []
        groups = claims.get("groups") or []
        if not isinstance(roles, list) or not isinstance(groups, list):
            raise Denied("invalid_claims")
        name = claims.get("name")
        display = name.strip()[:160] if isinstance(name, str) and name.strip() else claims["oid"]
        return EntraIdentity(claims["oid"], display, tuple(r for r in roles if isinstance(r, str)),
                             tuple(g for g in groups if isinstance(g, str) and GUID.fullmatch(g)), claims["exp"])
