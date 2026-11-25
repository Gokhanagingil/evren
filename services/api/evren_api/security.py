"""Oturum, kimlik ve istek güvenliği.

* Oturum: Fernet ile şifreli ve imzalı, süreli çerez (sunucu tarafında oturum tablosu yok). İçerik: kullanıcı
  kimliği, görünen ad, Evren rolleri, bitiş zamanı. ``HttpOnly; SameSite=Strict``; HTTPS'te ``__Host-`` önekli.
* Durum değiştiren istekler: ``X-Evren-Request: 1`` başlığı zorunlu ve ``Origin`` varsa aynı köken olmalı
  (CSRF). Başka kökene CORS izni verilmediği için özel başlık tarayıcıdan kökenler arası gönderilemez.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["Principal", "SessionCodec"]


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    roles: frozenset[str]
    mode: str
    groups: tuple[str, ...] = field(default=())

    @property
    def audit_id(self) -> str:
        return f"{self.mode}:{self.id}"


class SessionCodec:
    def __init__(self, key: bytes, hours: int) -> None:
        self.fernet = Fernet(key)
        self.ttl = hours * 3600

    def encode(self, principal: Principal) -> str:
        body = {"sub": principal.id, "name": principal.name, "roles": sorted(principal.roles),
                "mode": principal.mode, "exp": int(time.time()) + self.ttl}
        return self.fernet.encrypt(json.dumps(body, separators=(",", ":")).encode()).decode()

    def decode(self, token: str | None) -> Principal | None:
        if not token or len(token) > 4096:
            return None
        try:
            body = json.loads(self.fernet.decrypt(token.encode("ascii"), ttl=self.ttl))
        except (InvalidToken, ValueError, UnicodeEncodeError):
            return None
        if not isinstance(body, dict) or body.get("exp", 0) < time.time():
            return None
        roles = body.get("roles")
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            return None
        return Principal(str(body["sub"]), str(body.get("name") or body["sub"]), frozenset(roles), str(body["mode"]))
