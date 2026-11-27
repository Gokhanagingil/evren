"""İstek bağlamı: kimlik, veritabanı bağlantısı, izinler ve CSRF denetimi."""
from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

import psycopg
from fastapi import Request
from psycopg.types.json import Jsonb

from .authz import Grant
from .errors import ApiError
from .security import Principal

__all__ = ["Ctx", "context", "effective_roles", "require_csrf", "user_principal_id"]

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
        rid, ip = request_origin(self.request)
        params = (self.principal.audit_id, action, resource, decision, Jsonb(detail or {}), ip or None, rid or None)
        query = ("INSERT INTO audit.security_event (principal, action, resource, decision, detail, client_ip, request_id) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s)")
        if self.conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
            self.conn.execute(query, params)
            return
        with self.state.pool.connection() as other:
            other.execute(query, params)


def principal_from_request(request: Request) -> Principal:
    settings = request.app.state.settings
    if settings.auth_mode == "dev":
        roles = frozenset(settings.dev_roles)
        return Principal(settings.dev_user, settings.dev_name, roles, "dev", base_roles=roles)
    principal = request.app.state.sessions.decode(request.cookies.get(settings.session_cookie))
    if principal is None:
        raise ApiError(401, "oturum_gerekli", "Oturum açmanız gerekiyor.")
    return principal


def user_principal_id(principal: Principal) -> str:
    """``iam.principal_role`` içindeki kişi kimliği: Entra'da nesne kimliği (oid), geliştirmede kullanıcı adı."""
    return principal.id.lower()


def effective_roles(conn: psycopg.Connection[Any], principal: Principal) -> tuple[frozenset[str], list[str]]:
    """Kimlik sağlayıcının rolleri + veritabanındaki süresi dolmamış atamalar (kişiye ve gruplarına). Her istekte
    okunur: geri alınan veya süresi dolan rol hemen düşer. Gruplar: Entra'da oturumdaki rol verilmiş gruplar,
    geliştirmede kişinin ``ref.sys_user_grmember`` üyelikleri (Entra grup kimliği bağlı gruplar).
    Dönüş: (roller, görevler ayrılığı çatışmaları)."""
    groups = list(principal.groups)
    with conn.cursor() as cur:
        if principal.mode == "dev":
            cur.execute("""SELECT g.entra_group_id::text FROM ref.sys_user u
                             JOIN ref.sys_user_grmember m ON m."user" = u.sys_id
                             JOIN ref.sys_user_group g ON g.sys_id = m."group"
                            WHERE lower(u.user_name) = lower(%s) AND g.entra_group_id IS NOT NULL AND g.active""",
                        (principal.id,))
            groups += [r[0] for r in cur.fetchall()]
        cur.execute("""SELECT role FROM iam.principal_role
                        WHERE (expires_at IS NULL OR expires_at > now())
                          AND ((principal_type = 'user' AND principal_id = %s)
                               OR (principal_type = 'group' AND principal_id = ANY(%s)))""",
                    (user_principal_id(principal), groups))
        roles = set(principal.base_roles) | {r[0] for r in cur.fetchall()}
        # Kimlik sağlayıcının rolleri kendi aralarında girişte denetlendi (Entra) veya yapılandırmadır (geliştirme);
        # burada veritabanı atamasının yol açtığı çatışmalar aranır
        cur.execute("SELECT role_a, role_b, description FROM iam.sod_constraint WHERE role_a = ANY(%s) AND role_b = ANY(%s)",
                    (sorted(roles), sorted(roles)))
        conflicts = [d for a, b, d in cur.fetchall() if not {a, b} <= principal.base_roles]
    return frozenset(roles), conflicts


def request_origin(request: Request) -> tuple[str, str]:
    """(istek kimliği, kaynak IP) — denetim izi için; IP geçersizse boş (ör. test istemcisi)."""
    rid = getattr(request.state, "request_id", "") or ""
    host = request.client.host if request.client else ""
    try:
        ip = str(ipaddress.ip_address(host))
    except ValueError:
        ip = ""
    return rid, ip


def context(request: Request) -> Iterator[Ctx]:
    principal = principal_from_request(request)
    rid, ip = request_origin(request)
    with request.app.state.pool.connection() as conn:
        # Bu bağlantıdaki her denetim olayına istek kimliği ve kaynak IP işlenir (tetikleyici); bağlantı havuza
        # dönerken temizlenir (db.make_pool, reset).
        conn.execute("SELECT set_config('evren.request_id', %s, false), set_config('evren.client_ip', %s, false)",
                     (rid, ip))
        roles, conflicts = effective_roles(conn, principal)
        if conflicts:
            raise ApiError(403, "gorevler_ayriligi", "Rolleriniz görevler ayrılığı kuralıyla çatışıyor; yetki "
                                                     "yöneticinize başvurun.", [{"kural": c} for c in conflicts])
        principal = replace(principal, roles=roles)
        perms = request.app.state.authorizer.permissions(conn, principal.roles)
        yield Ctx(request, conn, principal, perms)


def require_csrf(request: Request) -> None:
    """Durum değiştiren isteklerde özel başlık + aynı köken (Origin varsa)."""
    if request.headers.get("x-evren-request") != "1":
        raise ApiError(403, "csrf", "İstek doğrulama başlığı eksik.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise ApiError(403, "csrf", "İstek başka bir kökenden geldi.")
