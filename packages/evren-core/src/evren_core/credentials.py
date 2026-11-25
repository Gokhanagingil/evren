"""Bağlayıcı belirteçleri (U2, ADR-0017): üretim, özet, biçim denetimi. Düz metin belirteç hiçbir yerde saklanmaz.

Belirteç ``evr_`` + 32 bayt rastgele (URL güvenli Base64, 43 karakter). Veritabanında yalnızca SHA-256 özeti ve son
dört karakteri (ayırt etmek için) tutulur. Yüksek entropili olduğundan yavaş özet (bcrypt vb.) gerekmez.
"""
from __future__ import annotations

import hashlib
import re
import secrets

__all__ = ["TOKEN_PREFIX", "new_token", "token_hint", "token_sha256", "well_formed"]

TOKEN_PREFIX = "evr_"  # noqa: S105 - sır değil, belirteç türü öneki
_TOKEN = re.compile(r"^evr_[A-Za-z0-9_-]{43}$")


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def well_formed(token: str) -> bool:
    return bool(_TOKEN.fullmatch(token))


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_hint(token: str) -> str:
    return token[-4:]
