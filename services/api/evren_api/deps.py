"""İstek bağlamı: kimlik, veritabanı bağlantısı, izinler ve CSRF denetimi."""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import psycopg
from fastapi import Request
from psycopg.types.json import Jsonb

from .authz import Grant
from .errors import ApiError
from .security import Principal

__all__ = ["Ctx", "context", "require_csrf"]

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class Ctx:
    request: Request
    conn: psycopg.Connection[Any]
    principal: Principal
    perms: list[dict[str, Any]]
    _user: tuple[bool, dict[str, str] | None] = field(default=(False, None), repr=False)

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

    def sys_user(self) -> dict[str, str] | None:
        """Oturumdaki kişinin etkin ``ref.sys_user`` kaydı (sahiplik onayı, ADR-0027). Entra oturumunda yalnızca
        değişmez nesne kimliğiyle (``entra_oid``) eşlenir; değişebilen UPN/e-posta yetki kararında kullanılmaz.
        Geliştirme oturumunda kullanıcı adıyla. Eşlenemezse ``None``."""
        if self._user[0]:
            return self._user[1]
        p = self.principal
        if p.mode == "entra":
            row = self.conn.execute(
                "SELECT sys_id, user_name, name FROM ref.sys_user WHERE active AND entra_oid = %s::uuid",
                (p.id,)).fetchone() if _GUID.match(p.id.lower()) else None
        else:
            row = self.conn.execute(
                "SELECT sys_id, user_name, name FROM ref.sys_user WHERE active AND lower(user_name) = lower(%s)",
                (p.id,)).fetchone()
        user = {"sys_id": row[0], "user_name": row[1], "name": row[2] or row[1]} if row else None
        self._user = (True, user)
        return user

    def security_event(self, action: str, decision: str, resource: str | None = None,
                       detail: dict[str, Any] | None = None) -> None:
        """Güvenlik olayını yazar. İstek bir veritabanı işleminin içindeyse (ör. ret ardından ApiError ile geri
        alınacak işlem) olay ayrı bir bağlantıyla yazılır; böylece ret kaydı geri alınmaz."""
        params = (self.principal.audit_id, action, resource, decision, Jsonb(detail or {}))
        query = ("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                 "VALUES (%s, %s, %s, %s, %s)")
        if self.conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
            self.conn.execute(query, params)
            return
        with self.state.pool.connection() as other:
            other.execute(query, params)


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
