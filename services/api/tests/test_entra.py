"""Entra ID akışı: sahte kimlik sağlayıcı (yerel RSA anahtarı) ile uçtan uca ve olumsuz senaryolar.

Ağ erişimi yoktur: imzalama anahtarı ve belirteç değişimi enjekte edilir; doğrulama kodu üretimdekiyle aynıdır.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import psycopg
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from evren_api import create_app
from evren_api.config import Settings
from evren_api.entra import Denied, Entra, EntraConfig

from .conftest import CSRF

TENANT = "11111111-2222-4333-8444-555555555555"
CLIENT = "66666666-7777-4888-9999-aaaaaaaaaaaa"
OID = "0f0e0d0c-0b0a-4909-8807-060504030201"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CONFIG = EntraConfig(TENANT, CLIENT, "x" * 32, "https://evren.ornek.test/auth/entra/callback", "c1")


class FakeJwks:
    def get_signing_key(self, kid: str):
        if kid != "k1":
            raise jwt.PyJWKClientError("bilinmeyen anahtar")
        return SimpleNamespace(key=KEY.public_key())


def token(nonce: str, **override):
    now = int(time.time())
    claims = {"iss": CONFIG.issuer, "aud": CLIENT, "sub": "s", "tid": TENANT, "oid": OID, "ver": "2.0",
              "nonce": nonce, "iat": now, "nbf": now, "exp": now + 600, "acrs": ["c1"], "name": "Ayşe Demir",
              "roles": ["Evren.Reader", "Evren.MatchReviewer"], "groups": []}
    claims.update(override)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})


class Idp:
    """Belirteç uç noktası taklidi: son akışın nonce'unu yetkilendirme adresinden okur."""

    def __init__(self) -> None:
        self.nonce = ""
        self.override: dict = {}

    def fetch(self, code: str, verifier: str) -> str:
        assert code == "kod-1" and len(verifier) >= 43
        return token(self.nonce, **self.override)


def entra_client(idp: Idp):
    env = {"EVREN_AUTH_MODE": "entra", "EVREN_COOKIE_SECURE": "1", "EVREN_SESSION_KEY": Fernet.generate_key().decode(),
           "EVREN_ENTRA_TENANT_ID": TENANT, "EVREN_ENTRA_CLIENT_ID": CLIENT, "EVREN_ENTRA_AUTH_CONTEXT": "c1",
           "EVREN_ENTRA_REDIRECT_URI": CONFIG.redirect, "EVREN_ENTRA_CLIENT_SECRET": "x" * 32, "EVREN_DB_POOL_SIZE": "2"}
    settings = Settings.from_env(env)
    entra = Entra(settings.entra, jwks=FakeJwks(), token_fetch=idp.fetch)
    return TestClient(create_app(settings, entra=entra), base_url="https://evren.ornek.test")


def sign_in(c: TestClient, idp: Idp, *, code: str = "kod-1"):
    start = c.get("/auth/login", follow_redirects=False)
    assert start.status_code == 303
    url = urlsplit(start.headers["location"])
    q = parse_qs(url.query)
    assert url.netloc == "login.microsoftonline.com" and q["code_challenge_method"] == ["S256"]
    assert '"acrs"' in q["claims"][0]
    idp.nonce = q["nonce"][0]
    return c.get("/auth/entra/callback", params={"state": q["state"][0], "code": code}, follow_redirects=False), q


def test_end_to_end_sign_in_maps_app_roles_and_sets_secure_session():
    idp = Idp()
    with entra_client(idp) as c:
        assert c.get("/api/v1/me").status_code == 401
        done, _ = sign_in(c, idp)
        assert done.status_code == 303 and done.headers["location"] == "/"
        cookie = done.headers["set-cookie"]
        assert "__Host-evren_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie
        assert "samesite=strict" in cookie.lower()
        me = c.get("/api/v1/me").json()
        assert me["name"] == "Ayşe Demir" and me["mode"] == "entra"
        assert {r["name"] for r in me["roles"]} == {"evren_reader", "evren_match_reviewer"}
        assert c.post("/auth/logout", headers=CSRF).status_code == 200


def test_replayed_flow_is_rejected_even_with_saved_flow_cookie():
    idp = Idp()
    with entra_client(idp) as c:
        start = c.get("/auth/login", follow_redirects=False)
        flow = c.cookies.get("__Host-evren_flow")
        q = parse_qs(urlsplit(start.headers["location"]).query)
        idp.nonce = q["nonce"][0]
        params = {"state": q["state"][0], "code": "kod-1"}
        assert c.get("/auth/entra/callback", params=params, follow_redirects=False).status_code == 303
        # Başarılı girişten sonra akış çerezi silinir: çerezsiz tekrar "geçersiz akış"
        assert c.get("/auth/entra/callback", params=params, follow_redirects=False).json()["error"]["details"] == [
            {"neden": "invalid_flow"}]
        # Saldırgan çerezi saklamış olsa bile aynı akış ikinci kez kabul edilmez
        c.cookies.set("__Host-evren_flow", flow, domain="evren.ornek.test")
        again = c.get("/auth/entra/callback", params=params, follow_redirects=False)
        assert again.status_code == 403 and again.json()["error"]["details"] == [{"neden": "replayed_flow"}]


@pytest.mark.parametrize("override,reason", [
    ({"acrs": ["c2"]}, "required_context_missing"),     # MFA bağlamı yok
    ({"tid": "99999999-2222-4333-8444-555555555555"}, "identity_mismatch"),  # düzenleyen doğru, kiracı yanlış
    ({"iss": "https://login.microsoftonline.com/99999999-2222-4333-8444-555555555555/v2.0"}, "invalid_id_token"),
    ({"aud": "baska-uygulama"}, "invalid_id_token"),
    ({"exp": int(time.time()) - 10}, "invalid_id_token"),
    ({"roles": ["Baska.Rol"]}, "rol_yok"),
])
def test_invalid_tokens_and_missing_roles_are_rejected(override, reason):
    idp = Idp()
    idp.override = override
    with entra_client(idp) as c:
        done, _ = sign_in(c, idp)
        assert done.status_code == 403
        err = done.json()["error"]
        assert reason in (err["code"], *(d.get("neden") for d in err["details"]))
        assert "__Host-evren_session" not in done.headers.get("set-cookie", "")


def test_wrong_nonce_and_forged_signature_are_rejected():
    entra = Entra(CONFIG, jwks=FakeJwks(), token_fetch=lambda c, v: "")
    with pytest.raises(Denied, match="identity_mismatch"):
        entra.verify(token("baska-nonce"), "beklenen-nonce")
    forged = jwt.encode({"nonce": "n"}, rsa.generate_private_key(public_exponent=65537, key_size=2048),
                        algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(Denied, match="invalid_id_token"):
        entra.verify(forged, "n")
    hs = jwt.encode({"nonce": "n"}, "gizli-anahtar-en-az-otuz-iki-bayt-uzunlukta", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(Denied, match="invalid_token_header"):
        entra.verify(hs, "n")


def test_group_assignment_creating_sod_conflict_blocks_sign_in():
    """Uygulama rolü Denetçi + grup ataması Konfigürasyon analisti → görevler ayrılığı çatışması → giriş yok."""
    group = str(uuid.uuid4())
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, justification) "
                     "VALUES ('group', %s, 'evren_cmdb_editor', 'test-yonetici', 'SoD testi')", (group,))
    try:
        idp = Idp()
        idp.override = {"roles": ["Evren.Auditor"], "groups": [group]}
        with entra_client(idp) as c:
            done, _ = sign_in(c, idp)
            assert done.status_code == 403 and done.json()["error"]["code"] == "gorevler_ayriligi"
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM iam.principal_role WHERE principal_id = %s", (group,))


def test_tampered_session_cookie_is_ignored():
    idp = Idp()
    with entra_client(idp) as c:
        sign_in(c, idp)
        value = c.cookies.get("__Host-evren_session")
        c.cookies.set("__Host-evren_session", value[:-4] + "AAAA", domain="evren.ornek.test")
        assert c.get("/api/v1/me").status_code == 401


def test_entra_configuration_is_strict():
    base = {"EVREN_AUTH_MODE": "entra", "EVREN_COOKIE_SECURE": "1", "EVREN_SESSION_KEY": Fernet.generate_key().decode(),
            "EVREN_ENTRA_TENANT_ID": TENANT, "EVREN_ENTRA_CLIENT_ID": CLIENT, "EVREN_ENTRA_AUTH_CONTEXT": "c1",
            "EVREN_ENTRA_REDIRECT_URI": CONFIG.redirect, "EVREN_ENTRA_CLIENT_SECRET": "x" * 32}
    assert Settings.from_env(base).entra is not None
    for bad in ({"EVREN_COOKIE_SECURE": "0"}, {"EVREN_ENTRA_REDIRECT_URI": "http://evren/auth/entra/callback"},
                {"EVREN_ENTRA_AUTH_CONTEXT": "mfa"}, {"EVREN_ENTRA_TENANT_ID": "kurum"}, {"EVREN_SESSION_KEY": ""}):
        with pytest.raises(ValueError):
            Settings.from_env({**base, **bad})
    with pytest.raises(ValueError, match="geri döngü"):
        Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_HOST": "0.0.0.0"})  # noqa: S104 - reddedildiğini test eder
    with pytest.raises(ValueError, match="zorunlu"):
        Settings.from_env({})
