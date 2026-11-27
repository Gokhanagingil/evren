"""Yönetişim görevleri (U8.4, ADR-0035, F1-24).

* Herkes kendine ve grubuna atanan görevleri görür ve kararını verir (``mine``, ``group``).
* Konfigürasyon yöneticisi (``governance`` / ``task_admin``) görev açar, iptal eder, bütün görevleri ve kanalları
  yönetir; Denetçi (``audit_read``) hepsini salt okunur görür.
* Görev ServiceNow kanalındaysa görev çalışanı teslim eder (``pg_notify('evren_task')`` ile uyanır). Karar Evren'de
  verilir; çalışan ServiceNow kaydını kararla kapatır.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from evren_core.governance import DECISIONS, OUTCOMES, ChannelSettings, Party, pick_assignee

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1", tags=["gorevler"])

_KEY = r"^[A-Za-z0-9_.:-]{8,128}$"
_SYSID = re.compile(r"^[0-9a-f]{32}$")
MIN_TEXT = 10
KIND_TR = {"manual": "Tek seferlik", "attestation": "Veri güncelliği teyidi", "access_review": "Erişim gözden geçirme",
           "checklist": "Kontrol listesi", "finding": "Bulgu"}
BASIS_TR = {"owned_by": "CI sahibi", "managed_by": "CI'ı yöneten", "support_group": "Destek grubu",
            "assigned": "Elle atandı", "unassigned": "Atanmamış"}
DELIVERY_TR = {"pending": "Teslim bekliyor", "delivered": "ServiceNow'da açık", "not_needed": "Evren'de",
               "failed": "Teslim edilemedi", "closed_sent": "ServiceNow'da kapatıldı",
               "kept_in_evren": "Evren'de (ServiceNow'da karşılık yok)"}


def _admin(ctx: Ctx) -> bool:
    return ctx.grant("governance", "task_admin").allowed


def _auditor(ctx: Ctx) -> bool:
    return ctx.grant("audit", "audit_read").allowed


def _my_groups(ctx: Ctx, user: dict[str, str] | None) -> list[str]:
    if not user:
        return []
    return [r[0] for r in ctx.conn.execute('SELECT "group" FROM ref.sys_user_grmember WHERE "user" = %s',
                                           (user["sys_id"],)).fetchall()]


_SELECT = """SELECT t.sys_id, t.number, t.kind, t.title, t.state, t.outcome, t.due_at, t.created_at, t.closed_at,
                    t.channel, ch.label_tr, ch.kind, t.delivery_status, t.external_number, t.external_closed,
                    t.ci_sys_id, ci.name, ci.sys_class_name, t.assignee_user, coalesce(u.name, u.user_name),
                    t.assignee_group, g.name, t.assignee_basis, t.source
               FROM gov.task t
               JOIN gov.task_channel ch ON ch.name = t.channel
               LEFT JOIN cmdb.cmdb_ci ci ON ci.sys_id = t.ci_sys_id
               LEFT JOIN ref.sys_user u ON u.sys_id = t.assignee_user
               LEFT JOIN ref.sys_user_group g ON g.sys_id = t.assignee_group"""


def _item(r: tuple[Any, ...]) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "sys_id": r[0], "number": r[1], "kind": r[2], "kind_tr": KIND_TR.get(r[2], r[2]), "title": r[3],
        "state": r[4], "outcome": r[5], "outcome_tr": OUTCOMES.get(r[5]) if r[5] else None,
        "due_at": r[6].isoformat(), "overdue": r[4] == "open" and r[6] < now, "created_at": r[7].isoformat(),
        "closed_at": r[8].isoformat() if r[8] else None,
        "channel": r[9], "channel_tr": r[10], "channel_kind": r[11], "delivery_status": r[12],
        "delivery_tr": DELIVERY_TR.get(r[12], r[12]), "external_number": r[13], "external_closed": r[14],
        "ci": {"sys_id": r[15], "name": r[16], "class": r[17]} if r[15] else None,
        "assignee": {"user": r[18], "user_name": r[19], "group": r[20], "group_name": r[21], "basis": r[22],
                     "basis_tr": BASIS_TR.get(r[22], r[22])},
        "source": r[23],
    }


@router.get("/tasks")
def list_tasks(ctx: Ctx = Depends(context),
               view: Literal["mine", "all", "unassigned", "attention"] = "mine",
               state: Literal["open", "closed", "all"] = "open", q: str = Query("", max_length=120),
               campaign: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
               limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """Görevler. ``mine``: bana veya grubuma atanan; ``all``/``unassigned``/``attention`` Konfigürasyon yöneticisi ve
    Denetçi içindir (``attention``: teslim edilemeyen veya ServiceNow'da kararsız kapatılan)."""
    admin, auditor = _admin(ctx), _auditor(ctx)
    user = ctx.sys_user()
    groups = _my_groups(ctx, user)
    conds: list[str] = []
    params: list[Any] = []
    if view == "mine":
        conds.append("(t.assignee_user = %s OR t.assignee_group = ANY(%s))")
        params += [user["sys_id"] if user else None, groups]
    elif not (admin or auditor):
        ctx.require("governance", "task_admin")
    elif view == "unassigned":
        conds.append("t.assignee_basis = 'unassigned'")
    elif view == "attention":
        conds.append("(t.delivery_status = 'failed' OR (t.external_closed AND t.state = 'open'))")
    if state == "open":
        conds.append("t.state = 'open'")
    elif state == "closed":
        conds.append("t.state <> 'open'")
    if campaign:
        conds.append("t.source = 'campaign' AND t.source_ref = %s")
        params.append(campaign)
    if q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append("(t.title ILIKE %s OR t.number ILIKE %s OR ci.name ILIKE %s)")
        params += [like, like, like]
    where = " AND ".join(conds) or "true"
    with ctx.conn.cursor() as cur:
        cur.execute(f"{_SELECT} WHERE {where} ORDER BY (t.state = 'open') DESC, t.due_at, t.number LIMIT %s OFFSET %s",
                    (*params, limit + 1, offset))
        rows = cur.fetchall()
        cur.execute("""SELECT count(*) FILTER (WHERE assignee_user = %s OR assignee_group = ANY(%s)),
                              count(*), count(*) FILTER (WHERE assignee_basis = 'unassigned'),
                              count(*) FILTER (WHERE delivery_status = 'failed' OR external_closed)
                         FROM gov.task WHERE state = 'open'""", (user["sys_id"] if user else None, groups))
        mine, all_, unassigned, attention = cur.fetchone()  # type: ignore[misc]
    more = len(rows) > limit
    counts = {"mine": mine}
    if admin or auditor:
        counts |= {"all": all_, "unassigned": unassigned, "attention": attention}
    return {"items": [_item(r) for r in rows[:limit]], "more": more, "counts": counts,
            "can_admin": admin, "can_read_all": admin or auditor, "mapped": user is not None}


def _load(ctx: Ctx, sys_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _SYSID.fullmatch(sys_id):
        raise ApiError(404, "bulunamadi", "Görev bulunamadı.")
    with ctx.conn.cursor() as cur:
        cur.execute(f"{_SELECT} WHERE t.sys_id = %s", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Görev bulunamadı.")
        cur.execute("""SELECT description, outcome_note, closed_by, created_by, delivery_note, external_state,
                              due_at, dedup_key, source_ref FROM gov.task WHERE sys_id = %s""", (sys_id,))
        extra = cur.fetchone()
    item = _item(row)
    user = ctx.sys_user()
    mine = bool(user) and (item["assignee"]["user"] == user["sys_id"]
                           or (item["assignee"]["group"] in _my_groups(ctx, user)))
    if not (mine or _admin(ctx) or _auditor(ctx)):
        raise ApiError(404, "bulunamadi", "Görev bulunamadı.")  # varlığı sızdırılmaz
    item |= {"description": extra[0], "outcome_note": extra[1], "closed_by": extra[2], "created_by": extra[3],
             "delivery_note": extra[4], "external_state": extra[5], "source_ref": extra[8]}
    return item, {"mine": mine, "user": user}


@router.get("/tasks/{sys_id}")
def get_task(sys_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    item, who = _load(ctx, sys_id)
    rows = ctx.conn.execute("SELECT at, actor, action, detail FROM gov.task_event WHERE task = %s ORDER BY id DESC LIMIT 50",
                            (sys_id,)).fetchall()
    item["events"] = [{"at": a.isoformat(), "actor": b, "action": c, "detail": d} for a, b, c, d in rows]
    open_ = item["state"] == "open"
    item["can_complete"] = open_ and (who["mine"] or _admin(ctx))
    item["can_cancel"] = open_ and _admin(ctx)
    if item["kind"] == "finding":
        # Kural görevi (ADR-0037): karar bulguya döner — istisna veya sorumlu ayıklaması
        item["outcomes"] = [{"id": "corrected", "label": "Düzeltildi (kural sonraki değerlendirmede doğrular)"},
                            {"id": "not_applicable", "label": "Geçerli değil (süreli istisna)"},
                            {"id": "rejected", "label": "Reddedildi (sorumlusu ben değilim)"}]
        item["finding"] = _finding(ctx, item.get("source_ref"))
    else:
        item["outcomes"] = [{"id": k, "label": OUTCOMES[k]} for k in DECISIONS]
    return item


def _finding(ctx: Ctx, finding: str | None) -> dict[str, Any] | None:
    if not finding:
        return None
    row = ctx.conn.execute("""SELECT f.sys_id, f.state, q.sys_id, q.name, q.description, q.exception_days, f.details,
                                     f.exception_until
                                FROM insight.finding f JOIN insight.quality_rule q ON q.sys_id = f.rule
                               WHERE f.sys_id = %s""", (finding,)).fetchone()
    if row is None:
        return None
    return {"sys_id": row[0], "state": row[1], "rule": {"sys_id": row[2], "name": row[3], "why": row[4]},
            "exception_days": row[5], "summary": (row[6] or {}).get("text") or "",
            "exception_until": row[7].isoformat() if row[7] else None}


class AssigneeIn(BaseModel):
    mode: Literal["auto", "user", "group"] = "auto"
    sys_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=4000)
    ci_sys_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    assignee: AssigneeIn = Field(default_factory=AssigneeIn)
    due_days: int = Field(default=14, ge=1, le=365)
    channel: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    idempotency_key: str = Field(pattern=_KEY)


def _party(cur: psycopg.Cursor[Any], table: str, sys_id: str | None) -> Party | None:
    if not sys_id:
        return None
    cur.execute(f"SELECT sys_id, active, source FROM ref.{table} WHERE sys_id = %s", (sys_id,))  # noqa: S608 - sabit tablo
    r = cur.fetchone()
    return Party(r[0], r[1], r[2]) if r else None


@router.post("/tasks", dependencies=[Depends(require_csrf)], status_code=201)
def create_task(body: TaskIn, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Tek seferlik görev aç. Sorumlu: CI'ın sahibi → yöneteni → destek grubu (``auto``) veya seçilen kişi/grup."""
    ctx.require("governance", "task_admin")
    p = ctx.principal
    digest = request_hash("task", body.model_dump(mode="json", exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        ci = None
        if body.ci_sys_id:
            cur.execute("SELECT sys_id, name, sys_class_name, owned_by, managed_by, support_group FROM cmdb.cmdb_ci "
                        "WHERE sys_id = %s", (body.ci_sys_id,))
            ci = cur.fetchone()
            if ci is None or not ctx.grant(ci[2], "read").allowed:
                raise ApiError(404, "ci_yok", "CI bulunamadı.")
        if body.assignee.mode == "auto":
            if ci is None:
                raise ApiError(422, "ci_gerekli", "Sorumluyu CI'dan bulmak için bir CI seçin veya kişi/grup seçin.")
            who = pick_assignee(owned_by=_party(cur, "sys_user", ci[3]), managed_by=_party(cur, "sys_user", ci[4]),
                                support_group=_party(cur, "sys_user_group", ci[5]))
            user, grp, basis = who.user, who.group, who.basis
        else:
            table = "sys_user" if body.assignee.mode == "user" else "sys_user_group"
            target = _party(cur, table, body.assignee.sys_id)
            if target is None or not target.active:
                raise ApiError(422, "sorumlu_yok", "Seçilen kişi veya grup bulunamadı ya da pasif.")
            user, grp, basis = (target.sys_id, None, "assigned") if table == "sys_user" else (None, target.sys_id,
                                                                                              "assigned")
        if body.channel:
            cur.execute("SELECT name, kind FROM gov.task_channel WHERE lifecycle = 'active' AND name = %s",
                        (body.channel,))
        else:
            cur.execute("SELECT name, kind FROM gov.task_channel WHERE lifecycle = 'active' AND is_default")
        ch = cur.fetchone()
        if ch is None:
            raise ApiError(422, "kanal_yok", "Etkin görev kanalı bulunamadı.")
        due = datetime.now(UTC) + timedelta(days=body.due_days)
        cur.execute("""INSERT INTO gov.task (kind, title, description, ci_sys_id, assignee_user, assignee_group,
                                             assignee_basis, due_at, source, channel, delivery_status, created_by)
                       VALUES ('manual', %s, %s, %s, %s, %s, %s, %s, 'manual', %s, %s, %s)
                       RETURNING sys_id, number""",
                    (body.title.strip(), body.description.strip(), body.ci_sys_id, user, grp, basis, due, ch[0],
                     "not_needed" if ch[1] == "evren" else "pending", p.audit_id))
        sys_id, number = cur.fetchone()  # type: ignore[misc]
        cur.execute("INSERT INTO gov.task_event (task, actor, action, detail) VALUES (%s, %s, 'created', %s)",
                    (sys_id, p.audit_id, Jsonb({"channel": ch[0], "basis": basis})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
        msg = f"{number} açıldı ({BASIS_TR[basis]})."
        if basis == "unassigned":
            msg = f"{number} açıldı ama CI'ın sahibi, yöneteni ve destek grubu yok: görev atanmamış listesinde."
        response = {"sys_id": sys_id, "number": number, "basis": basis, "channel": ch[0], "message": msg}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/tasks", digest, 201, response)
    return JSONResponse({**response, "replayed": False}, status_code=201)


class CompleteIn(BaseModel):
    outcome: Literal["confirmed", "corrected", "not_applicable", "rejected"]
    note: str = Field(default="", max_length=2000)


@router.post("/tasks/{sys_id}/complete", dependencies=[Depends(require_csrf)])
def complete_task(sys_id: str, body: CompleteIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Görevin kararı: sorumlu kişi, sorumlu grubun üyesi veya Konfigürasyon yöneticisi verir."""
    item, who = _load(ctx, sys_id)
    if not (who["mine"] or _admin(ctx)):
        ctx.require("governance", "task_admin")
    if item["state"] != "open":
        raise ApiError(409, "kapali", "Görev zaten kapalı.")
    note = body.note.strip()
    if body.outcome != "confirmed" and len(note) < MIN_TEXT:
        raise ApiError(422, "aciklama_gerekli", f"Bu karar için en az {MIN_TEXT} karakterlik açıklama yazın.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("""UPDATE gov.task SET state = 'done', outcome = %s, outcome_note = %s, closed_by = %s,
                              closed_at = now() WHERE sys_id = %s AND state = 'open'""",
                    (body.outcome, note or None, actor, sys_id))
        if not cur.rowcount:
            raise ApiError(409, "kapali", "Görev zaten kapalı.")
        cur.execute("INSERT INTO gov.task_event (task, actor, action, detail) VALUES (%s, %s, 'completed', %s)",
                    (sys_id, actor, Jsonb({"outcome": body.outcome})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
    tail = " ServiceNow kaydı da kapatılacak." if item["delivery_status"] == "delivered" else ""
    return {"sys_id": sys_id, "state": "done", "message": f"{item['number']} kapatıldı: {OUTCOMES[body.outcome]}.{tail}"}


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/tasks/{sys_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel_task(sys_id: str, body: CancelIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("governance", "task_admin")
    item, _ = _load(ctx, sys_id)
    if len(body.reason.strip()) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"İptal gerekçesi en az {MIN_TEXT} karakter olmalı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("""UPDATE gov.task SET state = 'cancelled', outcome = 'cancelled', outcome_note = %s,
                              closed_by = %s, closed_at = now() WHERE sys_id = %s AND state = 'open'""",
                    (body.reason.strip(), actor, sys_id))
        if not cur.rowcount:
            raise ApiError(409, "kapali", "Görev zaten kapalı.")
        cur.execute("INSERT INTO gov.task_event (task, actor, action, detail) VALUES (%s, %s, 'cancelled', %s)",
                    (sys_id, actor, Jsonb({"reason": body.reason.strip()})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
    return {"sys_id": sys_id, "state": "cancelled", "message": f"{item['number']} iptal edildi."}


@router.get("/task-channels")
def list_channels(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    if not (_admin(ctx) or _auditor(ctx)):
        ctx.require("governance", "task_admin")
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT c.name, c.kind, c.label_tr, c.lifecycle, c.is_default, c.settings, c.updated_by, c.updated_at,
                              count(t.sys_id) FILTER (WHERE t.state = 'open'),
                              count(t.sys_id) FILTER (WHERE t.delivery_status = 'failed' AND t.state = 'open')
                         FROM gov.task_channel c LEFT JOIN gov.task t ON t.channel = c.name
                        GROUP BY c.name ORDER BY c.is_default DESC, c.name""")
        channels = [{"name": a, "kind": b, "label_tr": c, "lifecycle": d, "is_default": e, "settings": f,
                     "updated_by": g, "updated_at": h.isoformat() if h else None, "open": i, "failed": j}
                    for a, b, c, d, e, f, g, h, i, j in cur.fetchall()]
        cur.execute("""SELECT seen_at, stopped_at, details FROM ingest.worker_heartbeat WHERE kind = 'governance'
                        ORDER BY seen_at DESC LIMIT 1""")
        hb = cur.fetchone()
    worker = None
    if hb:
        worker = {"seen_at": hb[0].isoformat(), "stopped": hb[1] is not None, "channel": (hb[2] or {}).get("channel"),
                  "alive": hb[1] is None and hb[0] > datetime.now(UTC) - timedelta(minutes=5)}
    return {"channels": channels, "worker": worker, "can_admin": _admin(ctx)}


class ChannelPatch(BaseModel):
    is_default: bool | None = None
    lifecycle: Literal["shadow", "active", "retiring", "retired"] | None = None
    settings: dict[str, Any] | None = None
    reason: str = Field(min_length=1, max_length=500)


@router.patch("/task-channels/{name}", dependencies=[Depends(require_csrf)])
def update_channel(name: str, body: ChannelPatch, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Varsayılan kanal, yaşam döngüsü ve ServiceNow ayarı (hedef tablo, durum eşlemesi, varsayılan atama grubu)."""
    ctx.require("governance", "task_admin")
    if len(body.reason.strip()) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"Gerekçe en az {MIN_TEXT} karakter olmalı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("SELECT kind, lifecycle, is_default, settings FROM gov.task_channel WHERE name = %s FOR UPDATE", (name,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kanal bulunamadı.")
        kind, lifecycle, is_default, settings = row
        changes: dict[str, Any] = {}
        if body.settings is not None:
            if kind != "servicenow":
                raise ApiError(422, "ayar_yok", "Evren kanalının ayarı yoktur.")
            try:
                settings = ChannelSettings.from_json(body.settings).to_json()
            except ValueError as exc:
                raise ApiError(422, "ayar_gecersiz", str(exc)) from None
            changes["settings"] = settings
        if body.lifecycle is not None and body.lifecycle != lifecycle:
            if kind == "evren":
                raise ApiError(422, "evren_kanali", "Evren kanalı her zaman etkindir.")
            changes["lifecycle"] = lifecycle = body.lifecycle
        if body.is_default and not is_default:
            if lifecycle != "active":
                raise ApiError(422, "etkin_degil", "Yalnızca etkin kanal varsayılan olabilir.")
            cur.execute("UPDATE gov.task_channel SET is_default = false, updated_by = %s, updated_at = now() "
                        "WHERE is_default", (actor,))
            changes["is_default"] = True
        elif is_default and (body.is_default is False or lifecycle != "active"):
            raise ApiError(422, "varsayilan_gerekli", "Varsayılan kanal kaldırılmadan önce başka bir kanalı varsayılan "
                                                       "yapın.")
        if not changes:
            return {"name": name, "changed": [], "message": "Değişiklik yok."}
        cur.execute("""UPDATE gov.task_channel SET settings = %s, lifecycle = %s,
                              is_default = CASE WHEN %s THEN true ELSE is_default END, updated_by = %s, updated_at = now()
                        WHERE name = %s""",
                    (Jsonb(settings), lifecycle, bool(changes.get("is_default")), actor, name))
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'task_channel_update', %s, 'allow', %s)",
                    (actor, f"task_channel:{name}", Jsonb({"changes": sorted(changes), "reason": body.reason.strip()})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (name,))
    return {"name": name, "changed": sorted(changes), "message": "Kanal ayarı kaydedildi."}
