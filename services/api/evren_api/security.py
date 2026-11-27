"""Oturum, kimlik ve istek güvenliği.

* Oturum: Fernet ile şifreli ve imzalı, süreli çerez (sunucu tarafında oturum tablosu yok). İçerik: kullanıcı
  kimliği, görünen ad, Entra uygulama rollerinden gelen Evren rolleri (``app``), rol verilmiş Entra grupları
  (``grp``), bitiş zamanı. ``iam.principal_role``'deki roller çereze konmaz, her istekte okunur: geri alınan rol hemen
  düşer (ADR-0034). ``HttpOnly; SameSite=Strict``; HTTPS'te ``__Host-`` önekli.
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
    # Kimlik sağlayıcının verdiği roller (Entra uygulama rolleri; geliştirmede EVREN_DEV_ROLES). ``roles`` bunlara
    # veritabanındaki geçerli rol atamaları eklenerek her istekte hesaplanır (deps.context).
    base_roles: frozenset[str] = field(default=frozenset())

    @property
    def audit_id(self) -> str:
        return f"{self.mode}:{self.id}"


class SessionCodec:
    def __init__(self, key: bytes, hours: int) -> None:
        self.fernet = Fernet(key)
        self.ttl = hours * 3600

    def encode(self, principal: Principal) -> str:
        body = {"sub": principal.id, "name": principal.name, "app": sorted(principal.base_roles),
                "grp": list(principal.groups), "mode": principal.mode, "exp": int(time.time()) + self.ttl}
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
        app, grp = body.get("app"), body.get("grp")
        if not all(isinstance(v, list) and all(isinstance(r, str) for r in v) for v in (app, grp)):
            return None  # eski biçimli oturum: yeniden giriş
        return Principal(str(body["sub"]), str(body.get("name") or body["sub"]), frozenset(app), str(body["mode"]),
                         tuple(grp), frozenset(app))
