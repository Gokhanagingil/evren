"""Zamanlanmış yönetişim kampanyaları (U8.5, ADR-0036, F1-25).

Konfigürasyon yöneticisi (``governance`` / ``task_admin``) kampanya tanımlar, önizler, etkinleştirir, duraklatır,
emekliye ayırır ve "şimdi çalıştır" ister; Denetçi salt okunur görür. Görevleri görev çalışanı açar (ADR-0035):
kapsamdaki her CI için sorumlusuna bir görev; aynı CI için açık görev varsa yenisi açılmaz.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from evren_core.governance import CAMPAIGN_KINDS, FREQUENCIES, campaign_scope, next_run

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1", tags=["kampanyalar"])

_KEY = r"^[A-Za-z0-9_.:-]{8,128}$"
_SYSID = re.compile(r"^[0-9a-f]{32}$")
MIN_TEXT = 10
STATE_TR = {"draft": "Taslak", "active": "Etkin", "paused": "Duraklatıldı", "retired": "Emekli"}
TRANSITIONS = {"draft": {"active", "retired"}, "active": {"paused", "retired"}, "paused": {"active", "retired"},
               "retired": set()}


def _reader(ctx: Ctx) -> None:
    if not (ctx.grant("governance", "task_admin").allowed or ctx.grant("audit", "audit_read").allowed):
        ctx.require("governance", "task_admin")


def _schedule(frequency: str | None, schedule: str | None) -> str:
    if frequency:
        if frequency not in FREQUENCIES:
            raise ApiError(422, "siklik_gecersiz", "Sıklık geçersiz.")
        return FREQUENCIES[frequency][1]
    if not schedule:
        raise ApiError(422, "siklik_gerekli", "Bir sıklık seçin veya zamanlama ifadesi yazın.")
    return schedule.strip()


def _validate(ctx: Ctx, scope_class: str, scope_query: str, schedule: str, tz: str) -> tuple[str, list[Any], datetime]:
    try:
        where, params = campaign_scope(ctx.state.metadata, scope_class, scope_query)
        nxt = next_run(schedule, tz, datetime.now(UTC))
    except ValueError as exc:
        raise ApiError(422, "kampanya_gecersiz", str(exc)) from None
    return where, params, nxt


def _preview(cur: psycopg.Cursor[Any], where: str, params: list[Any], campaign: str | None) -> dict[str, Any]:
    owner_ok = "coalesce(ou.active, false)"
    mgr_ok = f"(NOT {owner_ok} AND coalesce(mu.active, false))"
    grp_ok = f"(NOT {owner_ok} AND NOT coalesce(mu.active, false) AND coalesce(g.active, false))"
    joins = """FROM cmdb.cmdb_ci c LEFT JOIN ref.sys_user ou ON ou.sys_id = c.owned_by
               LEFT JOIN ref.sys_user mu ON mu.sys_id = c.managed_by
               LEFT JOIN ref.sys_user_group g ON g.sys_id = c.support_group"""
    cur.execute(f"""SELECT count(*), count(*) FILTER (WHERE {owner_ok}), count(*) FILTER (WHERE {mgr_ok}),
                           count(*) FILTER (WHERE {grp_ok}),
                           count(*) FILTER (WHERE NOT {owner_ok} AND NOT coalesce(mu.active, false)
                                              AND NOT coalesce(g.active, false)),
                           count(DISTINCT CASE WHEN {owner_ok} THEN ou.sys_id WHEN {mgr_ok} THEN mu.sys_id END),
                           count(DISTINCT CASE WHEN {grp_ok} THEN g.sys_id END)
                      {joins} WHERE {where}""", params)
    total, owned, managed, grp, unassigned, people, groups = cur.fetchone()  # type: ignore[misc]
    cur.execute(f"""SELECT c.sys_id, c.name, c.sys_class_name,
                           CASE WHEN {owner_ok} THEN coalesce(ou.name, ou.user_name)
                                WHEN {mgr_ok} THEN coalesce(mu.name, mu.user_name) END,
                           CASE WHEN {grp_ok} OR {owner_ok} OR {mgr_ok} THEN g.name END,
                           CASE WHEN {owner_ok} THEN 'owned_by' WHEN {mgr_ok} THEN 'managed_by'
                                WHEN {grp_ok} THEN 'support_group' ELSE 'unassigned' END
                      {joins} WHERE {where} ORDER BY c.name LIMIT 8""", params)
    sample = [{"sys_id": a, "name": b, "class": c, "user": d, "group": e, "basis": f} for a, b, c, d, e, f in cur.fetchall()]
    open_ = 0
    if campaign:
        cur.execute("SELECT count(*) FROM gov.task WHERE source_ref = %s AND state = 'open'", (campaign,))
        open_ = cur.fetchone()[0]  # type: ignore[index]
    return {"in_scope": total, "by_basis": {"owned_by": owned, "managed_by": managed, "support_group": grp,
                                           "unassigned": unassigned},
            "people": people, "groups": groups, "already_open": open_, "sample": sample}


class PreviewIn(BaseModel):
    scope_class: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    scope_query: str = Field(default="", max_length=2000)
    campaign: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@router.post("/campaigns/preview", dependencies=[Depends(require_csrf)])
def preview(body: PreviewIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Kapsam önizlemesi: kaç CI, sorumlu dayanağına göre dağılım, kaç kişi ve grup, örnek CI'lar.

    Önizleme bir denetimdir: geçersiz koşul 200 ile ``valid: false`` döner (CSV önizlemesi gibi); kayıt 422 verir."""
    ctx.require("governance", "task_admin")
    try:
        where, params = campaign_scope(ctx.state.metadata, body.scope_class, body.scope_query)
    except ValueError as exc:
        return {"valid": False, "problem": str(exc)}
    with ctx.conn.cursor() as cur:
        return {"valid": True, **_preview(cur, where, params, body.campaign)}


_COLS = """c.sys_id, c.name, c.kind, c.title, c.instructions, c.scope_class, c.scope_query, c.schedule, c.schedule_tz,
           c.due_days, c.channel, c.max_tasks, c.state, c.next_run_at, c.last_run_at, c.created_by, c.created_at,
           c.updated_by, c.updated_at,
           (SELECT count(*) FROM gov.task t WHERE t.source_ref = c.sys_id AND t.state = 'open'),
           (SELECT count(*) FROM gov.task t WHERE t.source_ref = c.sys_id AND t.state = 'open' AND t.due_at < now()),
           (SELECT count(*) FROM gov.task t WHERE t.source_ref = c.sys_id AND t.state <> 'open')"""


def _campaign(r: tuple[Any, ...], md: Any) -> dict[str, Any]:
    freq = next((k for k, (_label, cron) in FREQUENCIES.items() if cron == r[7]), None)
    cls = next((c for c in md.classes if c.get("name") == r[5]), {})
    return {
        "sys_id": r[0], "name": r[1], "kind": r[2], "kind_tr": CAMPAIGN_KINDS.get(r[2], r[2]), "title": r[3],
        "instructions": r[4], "scope_class": r[5], "scope_class_tr": cls.get("label_tr", r[5]), "scope_query": r[6],
        "schedule": r[7], "schedule_tz": r[8], "frequency": freq,
        "frequency_tr": FREQUENCIES[freq][0] if freq else f"Özel: {r[7]}", "due_days": r[9], "channel": r[10],
        "max_tasks": r[11], "state": r[12], "state_tr": STATE_TR[r[12]],
        "next_run_at": r[13].isoformat() if r[13] and r[12] == "active" else None,
        "last_run_at": r[14].isoformat() if r[14] else None, "created_by": r[15], "created_at": r[16].isoformat(),
        "updated_by": r[17], "updated_at": r[18].isoformat(), "open": r[19], "overdue": r[20], "closed": r[21],
    }


def _class_tree(md: Any) -> list[dict[str, Any]]:
    """Kapsam seçimi için sınıf ağacı: üst sınıf alt sınıflarını kapsar; kardeşler Türkçe ada göre sıralı."""
    label = {c["name"]: c.get("label_tr") or c.get("label") or c["name"] for c in md.classes}
    children: dict[str | None, list[str]] = {}
    for c in md.classes:
        children.setdefault(c.get("extends"), []).append(c["name"])
    out: list[dict[str, Any]] = []

    def walk(name: str, depth: int) -> None:
        out.append({"name": name, "label": label[name], "depth": depth})
        for child in sorted(children.get(name, []), key=lambda n: label[n].casefold()):
            walk(child, depth + 1)

    for root in sorted(children.get(None, []), key=lambda n: label[n].casefold()):
        walk(root, 0)
    return out


@router.get("/campaigns")
def list_campaigns(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    _reader(ctx)
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM gov.campaign c ORDER BY (c.state = 'retired'), c.name")  # noqa: S608 - sabit
        items = [_campaign(r, ctx.state.metadata) for r in cur.fetchall()]
    return {"items": items, "kinds": [{"id": k, "label": v} for k, v in CAMPAIGN_KINDS.items()],
            "frequencies": [{"id": k, "label": v[0], "schedule": v[1]} for k, v in FREQUENCIES.items()],
            "classes": _class_tree(ctx.state.metadata), "can_admin": ctx.grant("governance", "task_admin").allowed}


@router.get("/campaigns/{sys_id}")
def get_campaign(sys_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    _reader(ctx)
    if not _SYSID.fullmatch(sys_id):
        raise ApiError(404, "bulunamadi", "Kampanya bulunamadı.")
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM gov.campaign c WHERE c.sys_id = %s", (sys_id,))  # noqa: S608 - sabit
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kampanya bulunamadı.")
        item = _campaign(row, ctx.state.metadata)
        cur.execute("""SELECT id, trigger, requested_by, started_at, finished_at, status, in_scope, created, already_open,
                              unassigned, note FROM gov.campaign_run WHERE campaign = %s ORDER BY id DESC LIMIT 20""",
                    (sys_id,))
        item["runs"] = [{"id": a, "trigger": b, "requested_by": c, "started_at": d.isoformat(),
                         "finished_at": e.isoformat() if e else None, "status": f, "in_scope": g, "created": h,
                         "already_open": i, "unassigned": j, "note": k} for a, b, c, d, e, f, g, h, i, j, k in cur.fetchall()]
    item["transitions"] = sorted(TRANSITIONS[item["state"]])
    item["can_admin"] = ctx.grant("governance", "task_admin").allowed
    return item


class CampaignIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    kind: Literal["attestation", "access_review", "checklist"]
    title: str = Field(min_length=3, max_length=120)
    instructions: str = Field(default="", max_length=4000)
    scope_class: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    scope_query: str = Field(default="", max_length=2000)
    frequency: Literal["monthly", "quarterly", "semiannual", "annual"] | None = None
    schedule: str | None = Field(default=None, max_length=100)
    due_days: int = Field(default=30, ge=1, le=180)
    channel: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    max_tasks: int = Field(default=2000, ge=1, le=20000)
    activate: bool = False
    idempotency_key: str = Field(pattern=_KEY)


@router.post("/campaigns", dependencies=[Depends(require_csrf)], status_code=201)
def create_campaign(body: CampaignIn, ctx: Ctx = Depends(context)) -> JSONResponse:
    ctx.require("governance", "task_admin")
    schedule = _schedule(body.frequency, body.schedule)
    _where, _params, nxt = _validate(ctx, body.scope_class, body.scope_query, schedule, "Europe/Istanbul")
    p = ctx.principal
    digest = request_hash("campaign", body.model_dump(mode="json", exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        if body.channel:
            cur.execute("SELECT 1 FROM gov.task_channel WHERE name = %s AND lifecycle = 'active'", (body.channel,))
            if cur.fetchone() is None:
                raise ApiError(422, "kanal_yok", "Kanal bulunamadı veya etkin değil.")
        state = "active" if body.activate else "draft"
        try:
            with ctx.conn.transaction():
                cur.execute("""INSERT INTO gov.campaign (name, kind, title, instructions, scope_class, scope_query, schedule,
                                                         due_days, channel, max_tasks, state, next_run_at, created_by)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                            (body.name.strip(), body.kind, body.title.strip(), body.instructions.strip(), body.scope_class,
                             body.scope_query.strip(), schedule, body.due_days, body.channel, body.max_tasks, state,
                             nxt if state == "active" else None, p.audit_id))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "ad_var", "Bu adla bir kampanya zaten var.") from None
        sys_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'campaign_create', %s, 'allow', %s)",
                    (p.audit_id, f"campaign:{sys_id}", Jsonb({"name": body.name.strip(), "state": state})))
        first = nxt.astimezone(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
        msg = (f"Kampanya etkin; ilk görevler {first} tarihinde açılır. Hemen açmak için \"Şimdi çalıştır\"."
               if state == "active" else "Kampanya taslak olarak kaydedildi; önizleyip etkinleştirin.")
        response = {"sys_id": sys_id, "state": state, "next_run_at": nxt.isoformat() if state == "active" else None,
                    "message": msg}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/campaigns", digest, 201, response)
    return JSONResponse({**response, "replayed": False}, status_code=201)


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    title: str | None = Field(default=None, min_length=3, max_length=120)
    instructions: str | None = Field(default=None, max_length=4000)
    scope_class: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,79}$")
    scope_query: str | None = Field(default=None, max_length=2000)
    frequency: Literal["monthly", "quarterly", "semiannual", "annual"] | None = None
    schedule: str | None = Field(default=None, max_length=100)
    due_days: int | None = Field(default=None, ge=1, le=180)
    max_tasks: int | None = Field(default=None, ge=1, le=20000)
    state: Literal["active", "paused", "retired"] | None = None
    reason: str = Field(default="", max_length=500)


@router.patch("/campaigns/{sys_id}", dependencies=[Depends(require_csrf)])
def update_campaign(sys_id: str, body: CampaignPatch, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Taslak veya duraklatılmış kampanya düzenlenir; durum: etkinleştir, duraklat, emekliye ayır (gerekçeli)."""
    ctx.require("governance", "task_admin")
    if not _SYSID.fullmatch(sys_id):
        raise ApiError(404, "bulunamadi", "Kampanya bulunamadı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("""SELECT state, scope_class, scope_query, schedule, schedule_tz FROM gov.campaign
                        WHERE sys_id = %s FOR UPDATE""", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kampanya bulunamadı.")
        state, scope_class, scope_query, schedule, tz = row
        edits: dict[str, Any] = {k: v for k, v in body.model_dump(exclude={"state", "reason", "frequency", "schedule"}).items()
                                 if v is not None}
        if body.frequency or body.schedule:
            edits["schedule"] = _schedule(body.frequency, body.schedule)
        if edits and state not in ("draft", "paused"):
            raise ApiError(409, "duzenlenemez", "Yalnızca taslak veya duraklatılmış kampanya düzenlenir; önce duraklatın.")
        scope_class, scope_query = edits.get("scope_class", scope_class), edits.get("scope_query", scope_query)
        schedule = edits.get("schedule", schedule)
        _w, _p, nxt = _validate(ctx, scope_class, scope_query, schedule, tz)
        new_state = state
        if body.state and body.state != state:
            if body.state not in TRANSITIONS[state]:
                raise ApiError(409, "gecis_yok", f"{STATE_TR[state]} kampanya {STATE_TR[body.state].lower()} yapılamaz.")
            if len(body.reason.strip()) < MIN_TEXT:
                raise ApiError(422, "gerekce_gerekli", f"Durum değişikliği için en az {MIN_TEXT} karakterlik gerekçe yazın.")
            new_state = body.state
        if not edits and new_state == state:
            return {"sys_id": sys_id, "changed": [], "message": "Değişiklik yok."}
        sets = [f"{k} = %s" for k in edits] + ["state = %s", "next_run_at = %s", "updated_by = %s", "updated_at = now()"]
        values = [*edits.values(), new_state, nxt if new_state == "active" else None, actor]
        try:
            with ctx.conn.transaction():
                cur.execute(f"UPDATE gov.campaign SET {', '.join(sets)} WHERE sys_id = %s", (*values, sys_id))  # noqa: S608 - sütun adları sabit modelden
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "ad_var", "Bu adla bir kampanya zaten var.") from None
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'campaign_update', %s, 'allow', %s)",
                    (actor, f"campaign:{sys_id}", Jsonb({"changes": sorted(edits), "state": new_state,
                                                          "reason": body.reason.strip() or None})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
    msg = {"active": "Kampanya etkin.", "paused": "Kampanya duraklatıldı; açık görevler sürer.",
           "retired": "Kampanya emekliye ayrıldı; yeni görev açılmaz, açık görevler sürer."}.get(new_state, "Kaydedildi.")
    return {"sys_id": sys_id, "state": new_state, "changed": sorted(edits), "message": msg}


class RunIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/campaigns/{sys_id}/run", dependencies=[Depends(require_csrf)], status_code=202)
def run_campaign(sys_id: str, body: RunIn, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Şimdi çalıştır: görev çalışanı bir sonraki turunda yürütür (açık görevi olan CI'a yenisi açılmaz)."""
    ctx.require("governance", "task_admin")
    if len(body.reason.strip()) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"Gerekçe en az {MIN_TEXT} karakter olmalı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("SELECT state FROM gov.campaign WHERE sys_id = %s FOR UPDATE", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kampanya bulunamadı.")
        if row[0] != "active":
            raise ApiError(409, "etkin_degil", "Yalnızca etkin kampanya çalıştırılır.")
        cur.execute("SELECT 1 FROM gov.campaign_run WHERE campaign = %s AND status IN ('requested', 'running')", (sys_id,))
        if cur.fetchone():
            raise ApiError(409, "calisiyor", "Bu kampanya zaten çalışıyor veya çalışma isteği bekliyor.")
        cur.execute("""INSERT INTO gov.campaign_run (campaign, trigger, requested_by, status)
                       VALUES (%s, 'manual', %s, 'requested') RETURNING id""", (sys_id, actor))
        run = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'campaign_run', %s, 'allow', %s)",
                    (actor, f"campaign:{sys_id}", Jsonb({"run": run, "reason": body.reason.strip()})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
    return JSONResponse({"run": run, "message": "Çalışma istendi; görev çalışanı birkaç saniye içinde görevleri açar."},
                        status_code=202)
