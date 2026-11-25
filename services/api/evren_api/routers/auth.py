"""Oturum: Entra ID girişi ve dönüşü, çıkış.

Roller: Entra uygulama rolleri (``Evren.*`` → IAM rolü) + ``iam.principal_role`` içinde kullanıcıya (oid) veya
gruba atanmış, süresi dolmamış roller. Grup üyeliği yoluyla oluşan görevler ayrılığı (SoD) çatışması oturumu
reddeder (docs/07: "uygulama katmanında denetlenir"). Her giriş kararı ``audit.security_event``'e yazılır.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from psycopg.types.json import Jsonb

from ..deps import require_csrf
from ..entra import ENTRA_ROLE_MAP, Denied, Unavailable
from ..errors import ApiError
from ..security import Principal

router = APIRouter(tags=["oturum"])


def _security_event(request: Request, principal: str, action: str, decision: str, detail: dict[str, Any]) -> None:
    with request.app.state.pool.connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO audit.security_event (principal, action, decision, detail) VALUES (%s, %s, %s, %s)",
                    (principal, action, decision, Jsonb(detail)))


def resolve_roles(conn: Any, oid: str, groups: tuple[str, ...], app_roles: tuple[str, ...]) -> tuple[set[str], list[str]]:
    roles = {ENTRA_ROLE_MAP[r] for r in app_roles if r in ENTRA_ROLE_MAP}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT role FROM iam.principal_role
                WHERE (expires_at IS NULL OR expires_at > now())
                  AND ((principal_type = 'user' AND principal_id = %s)
                       OR (principal_type = 'group' AND principal_id = ANY(%s)))""", (oid, list(groups)))
        roles |= {r[0] for r in cur.fetchall()}
        cur.execute("SELECT role_a, role_b, description FROM iam.sod_constraint WHERE role_a = ANY(%s) AND role_b = ANY(%s)",
                    (sorted(roles), sorted(roles)))
        conflicts = [d for _a, _b, d in cur.fetchall()]
    return roles, conflicts


@router.get("/auth/login", include_in_schema=False)
def login(request: Request) -> Response:
    settings = request.app.state.settings
    if settings.auth_mode == "dev":
        return RedirectResponse("/", status_code=303)
    url, flow_cookie = request.app.state.entra.begin()
    resp = RedirectResponse(url, status_code=303)
    resp.headers.append("set-cookie", flow_cookie)
    return resp


@router.get("/auth/entra/callback", include_in_schema=False)
def callback(request: Request) -> Response:
    state = request.app.state
    if state.settings.auth_mode != "entra":
        raise ApiError(404, "bulunamadi", "Kaynak bulunamadı.")
    try:
        ident = state.entra.finish(request.url.query, request.headers.get("cookie", ""))
    except Denied as exc:
        _security_event(request, "entra:anonim", "login", "deny", {"reason": str(exc)})
        raise ApiError(403, "giris_reddedildi", "Oturum açılamadı.", [{"neden": str(exc)}]) from None
    except Unavailable as exc:
        raise ApiError(503, "kimlik_saglayici", "Kimlik sağlayıcıya ulaşılamıyor; biraz sonra deneyin.",
                       [{"neden": str(exc)}]) from None
    with state.pool.connection() as conn:
        roles, conflicts = resolve_roles(conn, ident.oid, ident.groups, ident.app_roles)
    who = f"entra:{ident.oid}"
    if conflicts:
        _security_event(request, who, "login", "deny", {"reason": "sod_conflict", "conflicts": conflicts})
        raise ApiError(403, "gorevler_ayriligi", "Rolleriniz görevler ayrılığı kuralıyla çatışıyor; yetki yöneticinize başvurun.",
                       [{"kural": c} for c in conflicts])
    if not roles:
        _security_event(request, who, "login", "deny", {"reason": "role_missing"})
        raise ApiError(403, "rol_yok", "Evren için atanmış bir rolünüz yok.")
    principal = Principal(ident.oid, ident.name, frozenset(roles), "entra", ident.groups)
    _security_event(request, who, "login", "allow", {"roles": sorted(roles)})
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(state.settings.session_cookie, state.sessions.encode(principal), max_age=state.sessions.ttl,
                    httponly=True, secure=True, samesite="strict", path="/")
    resp.delete_cookie("__Host-evren_flow", path="/", secure=True, httponly=True, samesite="lax")
    return resp


@router.post("/auth/logout", include_in_schema=False, dependencies=[Depends(require_csrf)])
def logout(request: Request) -> Response:
    settings = request.app.state.settings
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(settings.session_cookie, path="/", secure=settings.cookie_secure, httponly=True,
                       samesite="strict")
    return resp
