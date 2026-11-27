"""Kullanıcılar ve roller (U8.3, ADR-0034, F1-10).

* Kişiler ``ref.sys_user``'dan (ServiceNow, Entra veya Evren kaynaklı). Evren'de kişi açılabilir (``source = 'evren'``);
  ServiceNow/Entra kaynaklı kişide yalnızca Entra nesne kimliği bağlanır (veritabanı tetikleyicisi de zorlar).
* Rol ataması ``iam.principal_role``: kişiye (Entra'da nesne kimliği, geliştirmede kullanıcı adı) veya Entra grubuna.
  Gerekçe zorunlu; yetkili (elevated) rol süreli; makine rolü kişiye verilmez; kimse kendine veya üyesi olduğu gruba
  rol veremez, kendi rolünü geri alamaz; görevler ayrılığı (kişinin bilinen grup rolleriyle birlikte) denetlenir.
  Her değişiklik ``audit.security_event``'e gerekçesiyle düşer. Atamalar her istekte okunduğu için geri alınan rol
  hemen düşer (deps.effective_roles).
* Okuma: Yetki yöneticisi (``iam_admin``) ve Denetçi (``audit_read``). Yazma: yalnızca Yetki yöneticisi.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..deps import Ctx, context, require_csrf, user_principal_id
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1/iam", tags=["iam"])

_KEY = r"^[A-Za-z0-9_.:-]{8,128}$"
_SYSID = re.compile(r"^[0-9a-f]{32}$")
_USER_NAME = re.compile(r"^[\w.@'-]{2,120}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")

# Yetkili rol süre sınırı (gün); break-glass en çok bir gün. Yetkili olmayan rol süresiz olabilir.
ELEVATED_MAX_DAYS = 90
BREAK_GLASS = {"evren_admin": 1}
ANY_MAX_DAYS = 730
MIN_TEXT = 10

OPERATIONS = {
    "read": "Okuma", "create": "Oluşturma", "write": "Düzenleme", "delete": "Silme", "merge": "Birleştirme",
    "split": "Ayırma", "review_match": "Eşleştirme kararı", "ingest": "Veri gönderme", "schema_draft": "Şema taslağı",
    "schema_approve": "Şema onayı", "rules_admin": "Kural yönetimi", "iam_admin": "Yetki yönetimi",
    "audit_read": "Denetim izi okuma", "connector_read": "Bağlayıcı görme", "connector_admin": "Bağlayıcı yönetimi",
    "anomaly_review": "Anomali inceleme", "attest": "Sahiplik onayı", "retire": "Emekliye ayırma",
    "task_admin": "Yönetişim görevi yönetimi",
}
RESOURCES = {
    "*": "Her şey", "cmdb_ci": "Bütün CI'lar", "cmdb_rel_ci": "İlişkiler", "match_queue": "Eşleştirme kuyruğu",
    "schema": "Veri modeli", "rules": "Kurallar", "iam": "Kullanıcılar ve roller", "audit": "Denetim izi",
    "ingest": "Alım", "data_source": "Veri kaynakları", "anomaly": "Anomaliler", "platform": "Sistem durumu",
    "governance": "Yönetişim görevleri",
}
EVENTS = {"role_insert": "Rol verildi", "role_update": "Rol yenilendi", "role_delete": "Rol geri alındı",
          "user_create": "Kişi eklendi", "user_update": "Kişi güncellendi"}


def _can_read(ctx: Ctx) -> None:
    if not (ctx.grant("iam", "iam_admin").allowed or ctx.grant("audit", "audit_read").allowed):
        ctx.require("iam", "iam_admin")  # ret olayını yazar ve 403 döner


def _sysid(value: str) -> str:
    if not _SYSID.fullmatch(value):
        raise ApiError(404, "bulunamadi", "Kayıt bulunamadı.")
    return value


def _closure(roles: dict[str, dict[str, Any]], contains: list[tuple[str, str]]) -> dict[str, set[str]]:
    sub: dict[str, set[str]] = {r: set() for r in roles}
    for a, b in contains:
        sub.setdefault(a, set()).add(b)
    out: dict[str, set[str]] = {}
    for r in roles:
        seen, todo = {r}, [r]
        while todo:
            for n in sub.get(todo.pop(), ()):
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        out[r] = seen
    return out


def _roles(cur: psycopg.Cursor[Any]) -> dict[str, dict[str, Any]]:
    cur.execute("SELECT name, label_tr, description, elevated, machine FROM iam.role ORDER BY label_tr")
    return {n: {"name": n, "label_tr": label, "description": d, "elevated": e, "machine": m}
            for n, label, d, e, m in cur.fetchall()}


def _principal_key(ctx: Ctx, user: dict[str, Any]) -> str | None:
    """Kişinin rol atamasındaki kimliği: Entra'da nesne kimliği, geliştirmede kullanıcı adı (küçük harf)."""
    if ctx.principal.mode == "entra":
        return str(user["entra_oid"]).lower() if user.get("entra_oid") else None
    return str(user["user_name"]).lower()


def _person_rows(cur: psycopg.Cursor[Any], where: str, params: tuple[Any, ...], limit: int, offset: int) -> list[dict[str, Any]]:
    cur.execute(f"""SELECT u.sys_id, u.user_name, u.name, u.email, u.entra_oid, u.active, u.source,
                           (SELECT count(*) FROM ref.sys_user_grmember m WHERE m."user" = u.sys_id)
                      FROM ref.sys_user u WHERE {where}
                     ORDER BY u.active DESC, lower(coalesce(u.name, u.user_name)) LIMIT %s OFFSET %s""",  # noqa: S608 - koşul sabit parçalardan
                (*params, limit, offset))
    return [{"sys_id": a, "user_name": b, "name": c or b, "email": d, "entra_oid": str(e) if e else None, "active": f,
             "source": g, "group_count": h} for a, b, c, d, e, f, g, h in cur.fetchall()]


def _assignments(ctx: Ctx, cur: psycopg.Cursor[Any], people: list[dict[str, Any]],
                 roles: dict[str, dict[str, Any]]) -> None:
    """Kişilerin doğrudan ve grup yoluyla gelen rollerini ekler (``roles``)."""
    keys = {p["sys_id"]: _principal_key(ctx, p) for p in people}
    cur.execute("""SELECT m."user", g.name, g.entra_group_id::text FROM ref.sys_user_grmember m
                     JOIN ref.sys_user_group g ON g.sys_id = m."group"
                    WHERE m."user" = ANY(%s) AND g.entra_group_id IS NOT NULL AND g.active""",
                ([p["sys_id"] for p in people],))
    groups: dict[str, list[tuple[str, str]]] = {}
    for user, gname, gid in cur.fetchall():
        groups.setdefault(user, []).append((gname, gid))
    gids = sorted({gid for v in groups.values() for _n, gid in v})
    cur.execute("""SELECT pr.principal_type, pr.principal_id, pr.role, pr.granted_by, pr.granted_at, pr.expires_at,
                          pr.justification, coalesce(gu.name, gu.user_name)
                     FROM iam.principal_role pr
                     LEFT JOIN ref.sys_user gu ON (CASE WHEN %s = 'entra' THEN gu.entra_oid::text
                                                        ELSE lower(gu.user_name) END) = lower(pr.granted_by)
                    WHERE (pr.principal_type = 'user' AND pr.principal_id = ANY(%s))
                       OR (pr.principal_type = 'group' AND pr.principal_id = ANY(%s))
                    ORDER BY pr.granted_at""",
                (ctx.principal.mode, [k for k in keys.values() if k], gids))
    by_user: dict[str, list[dict[str, Any]]] = {}
    by_group: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now(UTC)
    for ptype, pid, role, by, at, exp, why, by_name in cur.fetchall():
        item = {"role": role, "label_tr": roles.get(role, {}).get("label_tr", role),
                "elevated": roles.get(role, {}).get("elevated", False), "granted_by": by_name or by,
                "granted_at": at.isoformat(), "expires_at": exp.isoformat() if exp else None,
                "expired": bool(exp and exp <= now), "justification": why}
        (by_user if ptype == "user" else by_group).setdefault(pid, []).append(item)
    for p in people:
        direct = [{**a, "via": "direct"} for a in by_user.get(keys[p["sys_id"]] or "", [])]
        via = [{**a, "via": "group", "group": gname} for gname, gid in groups.get(p["sys_id"], [])
               for a in by_group.get(gid, [])]
        p["roles"] = direct + via
        p["can_sign_in"] = ctx.principal.mode != "entra" or bool(p["entra_oid"])
        p["principal_key"] = keys[p["sys_id"]]


@router.get("/roles")
def list_roles(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Rol–yetki matrisi: roller (kalıtılan izinlerle), görevler ayrılığı kısıtları, rol sahipleri sayısı."""
    _can_read(ctx)
    with ctx.conn.cursor() as cur:
        roles = _roles(cur)
        cur.execute("SELECT role, contains FROM iam.role_contains")
        contains = cur.fetchall()
        cur.execute("""SELECT role, resource, operation, max_sensitivity, effect, condition IS NOT NULL, description
                         FROM iam.permission ORDER BY role, resource, operation""")
        perms: dict[str, list[dict[str, Any]]] = {}
        for role, res, op, sens, eff, cond, desc in cur.fetchall():
            perms.setdefault(role, []).append({"resource": res, "resource_tr": RESOURCES.get(res, res), "operation": op,
                                               "operation_tr": OPERATIONS.get(op, op), "max_sensitivity": sens,
                                               "effect": eff, "conditional": cond, "description": desc, "from": role})
        cur.execute("SELECT role_a, role_b, description FROM iam.sod_constraint ORDER BY role_a, role_b")
        sod = cur.fetchall()
        cur.execute("""SELECT role, principal_type, count(*) FROM iam.principal_role
                        WHERE expires_at IS NULL OR expires_at > now() GROUP BY role, principal_type""")
        holders: dict[str, dict[str, int]] = {}
        for role, ptype, n in cur.fetchall():
            holders.setdefault(role, {"user": 0, "group": 0, "service": 0})[ptype] = n
    closure = _closure(roles, contains)
    out = []
    for name, r in roles.items():
        inherited = sorted(closure[name] - {name}, key=lambda x: roles.get(x, {}).get("label_tr", x))
        out.append({**r, "contains": [{"name": c, "label_tr": roles[c]["label_tr"]} for c in inherited if c in roles],
                    "permissions": [p for c in sorted(closure[name]) for p in perms.get(c, [])],
                    "sod": [{"role": b if a == name else a, "label_tr": roles[b if a == name else a]["label_tr"],
                             "description": d} for a, b, d in sod if name in (a, b)],
                    "holders": holders.get(name, {"user": 0, "group": 0, "service": 0})})
    return {"roles": out, "operations": [{"id": k, "label": v} for k, v in OPERATIONS.items()],
            "policy": {"elevated_max_days": ELEVATED_MAX_DAYS, "break_glass_max_days": BREAK_GLASS,
                       "min_text": MIN_TEXT, "principal_key": "entra_oid" if ctx.principal.mode == "entra" else "user_name"},
            "can_admin": ctx.grant("iam", "iam_admin").allowed}


@router.get("/people")
def list_people(ctx: Ctx = Depends(context), q: str = Query("", max_length=120),
                role: str = Query("", max_length=64), status: Literal["active", "inactive", "all"] = "active",
                expiring: bool = False,
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """Kişiler ve rolleri. ``role`` verilirse yalnızca o rolü (doğrudan veya grup yoluyla) taşıyanlar."""
    _can_read(ctx)
    parts, params = ["true"], []
    if status != "all":
        parts.append("u.active = %s")
        params.append(status == "active")
    if q.strip():
        parts.append("(u.name ILIKE %s OR u.user_name ILIKE %s OR u.email ILIKE %s)")
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params += [like, like, like]
    if role:
        parts.append("""EXISTS (SELECT 1 FROM iam.principal_role pr
                         WHERE pr.role = %s AND (pr.expires_at IS NULL OR pr.expires_at > now())
                           AND ((pr.principal_type = 'user' AND pr.principal_id = CASE WHEN %s = 'entra'
                                    THEN u.entra_oid::text ELSE lower(u.user_name) END)
                             OR (pr.principal_type = 'group' AND pr.principal_id IN (
                                  SELECT g.entra_group_id::text FROM ref.sys_user_grmember m
                                    JOIN ref.sys_user_group g ON g.sys_id = m."group"
                                   WHERE m."user" = u.sys_id AND g.entra_group_id IS NOT NULL))))""")
        params += [role, ctx.principal.mode]
    if expiring:
        parts.append("""EXISTS (SELECT 1 FROM iam.principal_role pr WHERE pr.principal_type = 'user'
                           AND pr.principal_id = CASE WHEN %s = 'entra' THEN u.entra_oid::text ELSE lower(u.user_name) END
                           AND pr.expires_at > now() AND pr.expires_at < now() + interval '14 days')""")
        params.append(ctx.principal.mode)
    where = " AND ".join(parts)
    with ctx.conn.cursor() as cur:
        roles = _roles(cur)
        people = _person_rows(cur, where, tuple(params), limit + 1, offset)
        more = len(people) > limit
        people = people[:limit]
        _assignments(ctx, cur, people, roles)
        cur.execute(f"SELECT count(*) FROM ref.sys_user u WHERE {where}", tuple(params))  # noqa: S608 - sabit parçalar
        total = cur.fetchone()[0]  # type: ignore[index]
    return {"items": people, "total": total, "more": more, "mode": ctx.principal.mode,
            "can_admin": ctx.grant("iam", "iam_admin").allowed}


@router.get("/people/{sys_id}")
def get_person(sys_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    _can_read(ctx)
    _sysid(sys_id)
    with ctx.conn.cursor() as cur:
        roles = _roles(cur)
        people = _person_rows(cur, "u.sys_id = %s", (sys_id,), 1, 0)
        if not people:
            raise ApiError(404, "bulunamadi", "Kişi bulunamadı.")
        person = people[0]
        _assignments(ctx, cur, [person], roles)
        cur.execute("""SELECT g.sys_id, g.name, g.entra_group_id::text FROM ref.sys_user_grmember m
                         JOIN ref.sys_user_group g ON g.sys_id = m."group" WHERE m."user" = %s ORDER BY g.name""",
                    (sys_id,))
        person["groups"] = [{"sys_id": a, "name": b, "entra_group_id": c} for a, b, c in cur.fetchall()]
        key = person["principal_key"]
        person["events"] = _events(cur, roles, subjects=[f"user:{key}"] if key else [], people=[sys_id], limit=20)
    person["is_me"] = bool(person["principal_key"]) and person["principal_key"] == user_principal_id(ctx.principal)
    person["can_admin"] = ctx.grant("iam", "iam_admin").allowed
    return person


@router.get("/groups")
def list_groups(ctx: Ctx = Depends(context), q: str = Query("", max_length=120)) -> dict[str, Any]:
    _can_read(ctx)
    params: list[Any] = []
    cond = "true"
    if q.strip():
        cond = "g.name ILIKE %s"
        params.append("%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    with ctx.conn.cursor() as cur:
        roles = _roles(cur)
        cur.execute(f"""SELECT g.sys_id, g.name, g.email, g.entra_group_id::text, g.active, g.source,
                               (SELECT count(*) FROM ref.sys_user_grmember m WHERE m."group" = g.sys_id)
                          FROM ref.sys_user_group g WHERE {cond} ORDER BY g.active DESC, lower(g.name) LIMIT 500""",  # noqa: S608
                    tuple(params))
        groups = [{"sys_id": a, "name": b, "email": c, "entra_group_id": d, "active": e, "source": f, "members": n}
                  for a, b, c, d, e, f, n in cur.fetchall()]
        cur.execute("""SELECT principal_id, role, expires_at, justification, granted_at FROM iam.principal_role
                        WHERE principal_type = 'group' AND principal_id = ANY(%s)""",
                    ([g["entra_group_id"] for g in groups if g["entra_group_id"]],))
        by: dict[str, list[dict[str, Any]]] = {}
        now = datetime.now(UTC)
        for pid, role, exp, why, at in cur.fetchall():
            by.setdefault(pid, []).append({"role": role, "label_tr": roles.get(role, {}).get("label_tr", role),
                                           "elevated": roles.get(role, {}).get("elevated", False),
                                           "expires_at": exp.isoformat() if exp else None,
                                           "expired": bool(exp and exp <= now), "justification": why,
                                           "granted_at": at.isoformat()})
    for g in groups:
        g["roles"] = by.get(g["entra_group_id"] or "", [])
    return {"items": groups, "can_admin": ctx.grant("iam", "iam_admin").allowed}


def _events(cur: psycopg.Cursor[Any], roles: dict[str, dict[str, Any]], *, subjects: list[str] | None = None,
            people: list[str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    conds = ["e.action = ANY(%s)"]
    params: list[Any] = [list(EVENTS)]
    if subjects is not None or people is not None:
        conds.append("(e.resource = ANY(%s) OR e.resource = ANY(%s))")
        params += [subjects or [], [f"sys_user:{p}" for p in (people or [])]]
    cur.execute(f"""SELECT e.at, e.principal, e.action, e.resource, e.detail FROM audit.security_event e
                     WHERE {" AND ".join(conds)} ORDER BY e.at DESC, e.id DESC LIMIT %s""",  # noqa: S608 - sabit parçalar
                (*params, limit))
    out = []
    for at, who, action, res, detail in cur.fetchall():
        role = detail.get("role")
        out.append({"at": at.isoformat(), "actor": who, "action": action, "action_tr": EVENTS.get(action, action),
                    "subject": detail.get("subject") or res, "role": role,
                    "role_tr": roles.get(role, {}).get("label_tr", role) if role else None,
                    "expires_at": detail.get("expires_at"), "justification": detail.get("justification"),
                    "reason": detail.get("reason"), "changes": detail.get("changes")})
    return out


@router.get("/events")
def list_events(ctx: Ctx = Depends(context), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Yetki geçmişi: kim, kime, hangi rolü, ne zaman, hangi gerekçeyle verdi veya geri aldı."""
    _can_read(ctx)
    with ctx.conn.cursor() as cur:
        roles = _roles(cur)
        return {"items": _events(cur, roles, limit=limit)}


class PersonBody(BaseModel):
    user_name: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    entra_oid: uuid.UUID | None = None
    idempotency_key: str = Field(pattern=_KEY)


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    entra_oid: uuid.UUID | None = None
    clear_entra_oid: bool = False
    active: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


def _check_email(email: str | None) -> str | None:
    if email is None or not email.strip():
        return None
    email = email.strip()
    if not _EMAIL.fullmatch(email):
        raise ApiError(422, "eposta_gecersiz", "E-posta adresi geçerli görünmüyor.")
    return email


def _actor(cur: psycopg.Cursor[Any], ctx: Ctx, *, reason: str | None = None, subject: str | None = None) -> None:
    cur.execute("SELECT set_config('evren.actor', %s, true), set_config('evren.reason', %s, true), "
                "set_config('evren.subject', %s, true)", (ctx.principal.audit_id, reason or "", subject or ""))


def _audit(cur: psycopg.Cursor[Any], ctx: Ctx, action: str, sys_id: str, detail: dict[str, Any]) -> None:
    cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                "VALUES (%s, %s, %s, 'allow', %s)", (ctx.principal.audit_id, action, f"sys_user:{sys_id}", Jsonb(detail)))


@router.post("/people", dependencies=[Depends(require_csrf)], status_code=201)
def create_person(body: PersonBody, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Evren'de kişi kaydı açar (ServiceNow'da veya Entra eşitlemesinde henüz olmayan kişi için)."""
    ctx.require("iam", "iam_admin")
    user_name = body.user_name.strip()
    if not _USER_NAME.fullmatch(user_name):
        raise ApiError(422, "kullanici_adi_gecersiz", "Kullanıcı adı harf, rakam ve . _ - @ ' karakterlerinden oluşmalı "
                                                      "(Entra'daki oturum adı, ör. ayse.demir@kurum.com.tr).")
    email = _check_email(body.email)
    p = ctx.principal
    digest = request_hash("person", body.model_dump(mode="json", exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        try:
            with ctx.conn.transaction():
                cur.execute("""INSERT INTO ref.sys_user (sys_id, user_name, name, email, entra_oid, active, source)
                               VALUES (meta.new_sys_id(), %s, %s, %s, %s, true, 'evren') RETURNING sys_id""",
                            (user_name, body.name.strip(), email, body.entra_oid))
        except psycopg.errors.UniqueViolation as exc:
            if "entra_oid" in str(exc):
                raise ApiError(409, "entra_kimligi_var", "Bu Entra nesne kimliği başka bir kişiye bağlı.") from None
            raise ApiError(409, "kullanici_adi_var", "Bu kullanıcı adıyla bir kişi zaten var; listede arayın.") from None
        sys_id = cur.fetchone()[0]  # type: ignore[index]
        _audit(cur, ctx, "user_create", sys_id, {"subject": body.name.strip(), "user_name": user_name,
                                                  "entra_oid": str(body.entra_oid) if body.entra_oid else None})
        response = {"sys_id": sys_id, "user_name": user_name, "name": body.name.strip(),
                    "message": f"{body.name.strip()} eklendi. Şimdi rol verebilirsiniz."}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/iam/people", digest, 201, response)
    return JSONResponse({**response, "replayed": False}, status_code=201)


@router.patch("/people/{sys_id}", dependencies=[Depends(require_csrf)])
def update_person(sys_id: str, body: PersonPatch, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Kişiyi günceller. ServiceNow/Entra kaynaklı kişide yalnızca Entra nesne kimliği bağlanır. Pasifleştirilen
    kişinin doğrudan rolleri geri alınır (gerekçe zorunlu)."""
    ctx.require("iam", "iam_admin")
    _sysid(sys_id)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("SELECT user_name, name, email, entra_oid, active, source FROM ref.sys_user WHERE sys_id = %s FOR UPDATE",
                    (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kişi bulunamadı.")
        user_name, name, email, oid, active, source = row
        person = {"user_name": user_name, "entra_oid": oid}
        if _principal_key(ctx, person) == user_principal_id(ctx.principal):
            raise ApiError(403, "kendi_kaydi", "Kendi kişi kaydınızı buradan değiştiremezsiniz; başka bir yetki "
                                               "yöneticisi yapmalı.")
        changes: dict[str, Any] = {}
        if body.name is not None and body.name.strip() != name:
            changes["name"] = body.name.strip()
        if body.email is not None and (_check_email(body.email) or None) != email:
            changes["email"] = _check_email(body.email)
        if body.clear_entra_oid and oid is not None:
            changes["entra_oid"] = None
        elif body.entra_oid is not None and str(body.entra_oid) != (str(oid) if oid else None):
            changes["entra_oid"] = body.entra_oid
        if body.active is not None and body.active != active:
            changes["active"] = body.active
        if not changes:
            return {"sys_id": sys_id, "changed": [], "message": "Değişiklik yok."}
        if source != "evren" and set(changes) - {"entra_oid"}:
            raise ApiError(422, "kaynak_kisi", "Bu kişi ServiceNow veya Entra'dan geliyor; yalnızca Entra nesne kimliği "
                                               "bağlanabilir. Ad, e-posta ve durum kaynağında değiştirilmeli.")
        if "entra_oid" in changes or changes.get("active") is False:
            reason = (body.reason or "").strip()
            if len(reason) < MIN_TEXT:
                raise ApiError(422, "gerekce_gerekli", f"Entra kimliği bağlama ve pasifleştirme için en az {MIN_TEXT} "
                                                       "karakterlik gerekçe yazın.")
        _actor(cur, ctx, reason=body.reason, subject=changes.get("name") or name or user_name)
        revoked = []
        old_key = _principal_key(ctx, person)
        if (changes.get("active") is False or "entra_oid" in changes) and old_key:
            # Pasifleşen veya kimliği değişen kişinin eski kimliğine verilmiş roller geri alınır
            cur.execute("DELETE FROM iam.principal_role WHERE principal_type = 'user' AND principal_id = %s RETURNING role",
                        (old_key,))
            revoked = [r[0] for r in cur.fetchall()]
        sets = ", ".join(f"{k} = %s" for k in changes)
        try:
            with ctx.conn.transaction():
                cur.execute(f"UPDATE ref.sys_user SET {sets}, sys_updated_on = now() WHERE sys_id = %s",  # noqa: S608 - sütun adları sabit kümeden
                            (*changes.values(), sys_id))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "entra_kimligi_var", "Bu Entra nesne kimliği başka bir kişiye bağlı.") from None
        _audit(cur, ctx, "user_update", sys_id, {"subject": changes.get("name") or name or user_name,
                                                  "changes": {k: str(v) if v is not None else None for k, v in changes.items()},
                                                  "reason": body.reason, "revoked": revoked})
    msg = "Kişi güncellendi."
    if revoked:
        msg += f" Doğrudan verilmiş {len(revoked)} rol geri alındı."
    return {"sys_id": sys_id, "changed": sorted(changes), "revoked": revoked, "message": msg}


class GroupPatch(BaseModel):
    entra_group_id: uuid.UUID | None = None
    reason: str = Field(default="", max_length=500)


@router.patch("/groups/{sys_id}", dependencies=[Depends(require_csrf)])
def bind_group(sys_id: str, body: GroupPatch, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Gruba Entra grup kimliği bağlar veya kaldırır (grup rolleri bu kimlikle verilir)."""
    ctx.require("iam", "iam_admin")
    _sysid(sys_id)
    gid = body.entra_group_id
    reason = body.reason.strip()
    if len(reason) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"En az {MIN_TEXT} karakterlik gerekçe yazın.")
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("SELECT name, entra_group_id::text FROM ref.sys_user_group WHERE sys_id = %s FOR UPDATE", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Grup bulunamadı.")
        if row[1] and (gid is None or str(gid) != row[1]):
            cur.execute("SELECT count(*) FROM iam.principal_role WHERE principal_type = 'group' AND principal_id = %s",
                        (row[1],))
            if cur.fetchone()[0]:  # type: ignore[index]
                raise ApiError(409, "grup_rolleri_var", "Bu grubun rolleri var; kimliği değiştirmeden önce rolleri geri "
                                                        "alın.")
        try:
            with ctx.conn.transaction():
                cur.execute("UPDATE ref.sys_user_group SET entra_group_id = %s, sys_updated_on = now() WHERE sys_id = %s",
                            (gid, sys_id))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "grup_kimligi_var", "Bu Entra grup kimliği başka bir gruba bağlı.") from None
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'group_bind', %s, 'allow', %s)",
                    (ctx.principal.audit_id, f"sys_user_group:{sys_id}",
                     Jsonb({"subject": row[0], "entra_group_id": str(gid) if gid else None, "reason": reason})))
    return {"sys_id": sys_id, "entra_group_id": str(gid) if gid else None, "message": "Grup kimliği kaydedildi."}


class GrantBody(BaseModel):
    principal_type: Literal["user", "group"]
    target: str = Field(pattern=r"^[0-9a-f]{32}$")
    role: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    expires_at: datetime | None = None
    justification: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(pattern=_KEY)


class RevokeBody(BaseModel):
    principal_type: Literal["user", "group"]
    target: str = Field(pattern=r"^[0-9a-f]{32}$")
    role: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    reason: str = Field(min_length=1, max_length=500)


def _target(ctx: Ctx, cur: psycopg.Cursor[Any], ptype: str, sys_id: str) -> tuple[str, str, list[str]]:
    """(rol atamasındaki kimlik, görünen ad, bilinen grup kimlikleri). Hedef geçersizse anlaşılır hata."""
    if ptype == "user":
        cur.execute("SELECT user_name, name, entra_oid, active FROM ref.sys_user WHERE sys_id = %s", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kişi bulunamadı.")
        if not row[3]:
            raise ApiError(409, "kisi_pasif", "Pasif kişiye rol verilmez.")
        key = _principal_key(ctx, {"user_name": row[0], "entra_oid": row[2]})
        if key is None:
            raise ApiError(409, "entra_kimligi_yok", "Kişinin Entra nesne kimliği yok; Evren'e giriş yapamaz. Önce kişi "
                                                     "kaydına Entra nesne kimliğini bağlayın.")
        cur.execute("""SELECT g.entra_group_id::text FROM ref.sys_user_grmember m
                         JOIN ref.sys_user_group g ON g.sys_id = m."group"
                        WHERE m."user" = %s AND g.entra_group_id IS NOT NULL""", (sys_id,))
        return key, row[1] or row[0], [r[0] for r in cur.fetchall()]
    cur.execute("SELECT name, entra_group_id::text, active FROM ref.sys_user_group WHERE sys_id = %s", (sys_id,))
    row = cur.fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "Grup bulunamadı.")
    if not row[1]:
        raise ApiError(409, "grup_kimligi_yok", "Grubun Entra grup kimliği yok; rol, Entra grubuna verilir. Önce grup "
                                                "kimliğini bağlayın.")
    return row[1], row[0], [row[1]]


def _is_self(ctx: Ctx, cur: psycopg.Cursor[Any], ptype: str, key: str) -> bool:
    me = user_principal_id(ctx.principal)
    if ptype == "user":
        return key == me
    if key in ctx.principal.groups:
        return True
    if ctx.principal.mode == "dev":
        cur.execute("""SELECT 1 FROM ref.sys_user u JOIN ref.sys_user_grmember m ON m."user" = u.sys_id
                         JOIN ref.sys_user_group g ON g.sys_id = m."group"
                        WHERE lower(u.user_name) = %s AND g.entra_group_id::text = %s""", (me, key))
        return cur.fetchone() is not None
    return False


@router.post("/grants", dependencies=[Depends(require_csrf)], status_code=201)
def grant_role(body: GrantBody, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Rol ver veya süresini yenile. Gerekçe zorunlu; yetkili rol süreli; görevler ayrılığı denetlenir."""
    ctx.require("iam", "iam_admin")
    why = body.justification.strip()
    if len(why) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"Gerekçe en az {MIN_TEXT} karakter olmalı (ör. talep numarası ve neden).")
    p = ctx.principal
    digest = request_hash("grant", body.model_dump(mode="json", exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        roles = _roles(cur)
        role = roles.get(body.role)
        if role is None:
            raise ApiError(422, "rol_yok", "Böyle bir rol yok.")
        if role["machine"]:
            raise ApiError(422, "makine_rolu", f"{role['label_tr']} makine hesabı rolüdür; kişiye veya gruba verilmez.")
        now = datetime.now(UTC)
        exp = body.expires_at
        if exp is not None:
            if exp.tzinfo is None:
                raise ApiError(422, "saat_dilimi", "Bitiş zamanı saat dilimiyle gönderilmeli.")
            if exp <= now:
                raise ApiError(422, "bitis_gecmiste", "Bitiş zamanı gelecekte olmalı.")
        limit_days = BREAK_GLASS.get(body.role, ELEVATED_MAX_DAYS) if role["elevated"] else ANY_MAX_DAYS
        if role["elevated"] and exp is None:
            raise ApiError(422, "sure_gerekli", f"{role['label_tr']} yetkili bir roldür; en çok {limit_days} gün süreyle "
                                                "verilir. Bitiş tarihi seçin.")
        if exp is not None and exp > now + timedelta(days=limit_days, minutes=5):
            raise ApiError(422, "sure_uzun", f"{role['label_tr']} en çok {limit_days} gün süreyle verilebilir.")
        key, label, groups = _target(ctx, cur, body.principal_type, body.target)
        if _is_self(ctx, cur, body.principal_type, key):
            raise ApiError(403, "kendine_rol", "Kendinize veya üyesi olduğunuz gruba rol veremezsiniz; başka bir yetki "
                                               "yöneticisi yapmalı.")
        # Görevler ayrılığı: hedefin bugünkü rolleri (doğrudan + bilinen grupları) ile yeni rol
        cur.execute("""SELECT role FROM iam.principal_role WHERE (expires_at IS NULL OR expires_at > now())
                          AND ((principal_type = %s AND principal_id = %s)
                               OR (principal_type = 'group' AND principal_id = ANY(%s)))""",
                    (body.principal_type, key, groups))
        current = {r[0] for r in cur.fetchall()}
        cur.execute("""SELECT description FROM iam.sod_constraint
                        WHERE (role_a = %s AND role_b = ANY(%s)) OR (role_b = %s AND role_a = ANY(%s))""",
                    (body.role, sorted(current), body.role, sorted(current)))
        if (conflict := cur.fetchone()) is not None:
            raise ApiError(409, "gorevler_ayriligi", f"Görevler ayrılığı: {conflict[0]}.",
                           [{"kural": conflict[0]}])
        _actor(cur, ctx, subject=label)
        cur.execute("""INSERT INTO iam.principal_role (principal_type, principal_id, role, granted_by, expires_at,
                                                       justification)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (principal_type, principal_id, role) DO UPDATE
                          SET granted_by = EXCLUDED.granted_by, granted_at = now(), expires_at = EXCLUDED.expires_at,
                              justification = EXCLUDED.justification
                       RETURNING (xmax = 0)""",
                    (body.principal_type, key, body.role, user_principal_id(p), exp, why))
        created = cur.fetchone()[0]  # type: ignore[index]
        response = {"principal_type": body.principal_type, "principal_key": key, "role": body.role,
                    "expires_at": exp.isoformat() if exp else None, "renewed": not created,
                    "message": f"{label}: {role['label_tr']} rolü {'verildi' if created else 'yenilendi'}. "
                               "Hemen geçerli olur."}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/iam/grants", digest, 201, response)
    return JSONResponse({**response, "replayed": False}, status_code=201)


@router.post("/grants/revoke", dependencies=[Depends(require_csrf)])
def revoke_role(body: RevokeBody, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Rolü geri al (gerekçe zorunlu). Kişi bir sonraki isteğinde bu rolü kaybeder."""
    ctx.require("iam", "iam_admin")
    reason = body.reason.strip()
    if len(reason) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"Geri alma gerekçesi en az {MIN_TEXT} karakter olmalı.")
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if body.principal_type == "user":
            cur.execute("SELECT user_name, name, entra_oid FROM ref.sys_user WHERE sys_id = %s", (body.target,))
            row = cur.fetchone()
            if row is None:
                raise ApiError(404, "bulunamadi", "Kişi bulunamadı.")
            key, label = _principal_key(ctx, {"user_name": row[0], "entra_oid": row[2]}), row[1] or row[0]
        else:
            cur.execute("SELECT name, entra_group_id::text FROM ref.sys_user_group WHERE sys_id = %s", (body.target,))
            row = cur.fetchone()
            if row is None:
                raise ApiError(404, "bulunamadi", "Grup bulunamadı.")
            key, label = row[1], row[0]
        if not key:
            raise ApiError(404, "atama_yok", "Bu rol ataması bulunamadı.")
        if _is_self(ctx, cur, body.principal_type, key):
            raise ApiError(403, "kendine_rol", "Kendi rolünüzü veya üyesi olduğunuz grubun rolünü geri alamazsınız; "
                                               "başka bir yetki yöneticisi yapmalı.")
        _actor(cur, ctx, reason=reason, subject=label)
        cur.execute("DELETE FROM iam.principal_role WHERE principal_type = %s AND principal_id = %s AND role = %s",
                    (body.principal_type, key, body.role))
        if not cur.rowcount:
            raise ApiError(404, "atama_yok", "Bu rol ataması bulunamadı.")
        label_tr = _roles(cur).get(body.role, {}).get("label_tr", body.role)
    return {"principal_type": body.principal_type, "role": body.role,
            "message": f"{label}: {label_tr} rolü geri alındı. Bir sonraki işleminde geçerli olur."}
