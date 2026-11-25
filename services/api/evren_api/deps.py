"""İstek bağlamı: kimlik, veritabanı bağlantısı, izinler ve CSRF denetimi."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import psycopg
from fastapi import Request
from psycopg.types.json import Jsonb

from .authz import Grant
from .errors import ApiError
from .security import Principal

__all__ = ["Ctx", "context", "require_csrf"]


@dataclass
class Ctx:
    request: Request
    conn: psycopg.Connection[Any]
    principal: Principal
    perms: list[dict[str, Any]]

    @property
    def state(self) -> Any:
        return self.request.app.state

    def grant(self, resource: str, operation: str) -> Grant:
        return self.state.authorizer.grant(self.perms, resource, operation)

    def require(self, resource: str, operation: str) -> Grant:
        g = self.grant(resource, operation)
        if not g.allowed:
            self.security_event(f"{operation}:{resource}", "deny", resource)
            raise ApiError(403, "yetki_yok", "Bu işlem için yetkiniz yok.", [{"resource": resource, "operation": operation}])
        return g

    def security_event(self, action: str, decision: str, resource: str | None = None,
                       detail: dict[str, Any] | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (self.principal.audit_id, action, resource, decision, Jsonb(detail or {})))


def principal_from_request(request: Request) -> Principal:
    settings = request.app.state.settings
    if settings.auth_mode == "dev":
        return Principal(settings.dev_user, settings.dev_name, frozenset(settings.dev_roles), "dev")
    principal = request.app.state.sessions.decode(request.cookies.get(settings.session_cookie))
    if principal is None:
        raise ApiError(401, "oturum_gerekli", "Oturum açmanız gerekiyor.")
    return principal


def context(request: Request) -> Iterator[Ctx]:
    principal = principal_from_request(request)
    with request.app.state.pool.connection() as conn:
        perms = request.app.state.authorizer.permissions(conn, principal.roles)
        yield Ctx(request, conn, principal, perms)


def require_csrf(request: Request) -> None:
    """Durum değiştiren isteklerde özel başlık + aynı köken (Origin varsa)."""
    if request.headers.get("x-evren-request") != "1":
        raise ApiError(403, "csrf", "İstek doğrulama başlığı eksik.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise ApiError(403, "csrf", "İstek başka bir kökenden geldi.")
