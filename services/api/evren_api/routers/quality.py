"""Kalite ve uyum kuralları, bulgular ve CMDB sağlık panosu (U8.6, ADR-0037; F1-09, F1-18, F1-26).

Okuma: CI okuma izni olan herkes (sağlık, kurallar, bulgular). Yazma: Konfigürasyon yöneticisi (``governance`` /
``task_admin``) kural tanımlar, önizler, durumunu değiştirir, "şimdi çalıştır" ister, bulguya istisna verir veya
istisnayı kaldırır. Kuralı görev çalışanı değerlendirir; API bulgu açmaz, kapatmaz.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from evren_core.health import DIMENSIONS
from evren_core.quality import (
    CHECK_KINDS,
    DIMENSION_TR,
    INTERVALS,
    SEVERITY_TR,
    TEMPLATES,
    compile_rule,
    describe_details,
    relation_violations,
)

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash
from .campaigns import STATE_TR, TRANSITIONS, _class_tree, _preview

router = APIRouter(prefix="/api/v1/quality", tags=["kalite"])

_KEY = r"^[A-Za-z0-9_.:-]{8,128}$"
_SYSID = re.compile(r"^[0-9a-f]{32}$")
MIN_TEXT = 10
PREVIEW_CANDIDATES = 5000
FINDING_STATE_TR = {"open": "Açık", "excepted": "İstisna", "resolved": "Giderildi"}
RUN_STATUS_TR = {"requested": "İstendi", "running": "Çalışıyor", "done": "Tamamlandı", "failed": "Başarısız",
                 "inconclusive": "Sonuç yok"}


def _admin(ctx: Ctx) -> bool:
    return ctx.grant("governance", "task_admin").allowed


def _role_problem(ctx: Ctx, roles: tuple[str, ...]) -> str | None:
    """Rolün etkin kaynakları son çalışmada sağlıklı mı (görev çalışanıyla aynı ölçüt)?"""
    md = ctx.state.metadata
    for role in roles:
        sources = [s.name for s in md.sources.sources_in_role(role)]
        if not sources:
            return f"{md.sources.roles[role].label_tr} rolünde etkin veri kaynağı yok; kural sonuç vermez."
        rows = dict(ctx.conn.execute(
            """SELECT DISTINCT ON (data_source) data_source, CASE WHEN completed THEN coalesce(breaker_state, 'warn')
                                                              ELSE 'open' END
                 FROM ingest.source_run WHERE data_source = ANY(%s) ORDER BY data_source, started_at DESC""",
            (sources,)).fetchall())
        bad = [s for s in sources if rows.get(s) != "ok"]
        if bad:
            return f"{', '.join(bad)} son çalışmada sağlıklı değil; kaynak düzelene kadar kural sonuç vermez."
    return None


# ---------------------------------------------------------------------------------------------- sağlık
@router.get("/health")
def health(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Son sağlık ölçümü, son 30 günün günlük eğilimi ve en çok bulgu üreten kurallar."""
    ctx.require("cmdb_ci", "read")
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT taken_at, details FROM insight.health_snapshot ORDER BY taken_at DESC LIMIT 1")
        last = cur.fetchone()
        cur.execute("""SELECT DISTINCT ON (taken_at::date) taken_at, overall, completeness, correctness, compliance,
                              coverage
                         FROM insight.health_snapshot WHERE taken_at > now() - interval '30 days'
                        ORDER BY taken_at::date, taken_at DESC""")
        trend = [{"at": a.isoformat(), "overall": _f(b), "completeness": _f(c), "correctness": _f(d),
                  "compliance": _f(e), "coverage": _f(f)} for a, b, c, d, e, f in cur.fetchall()]
        cur.execute("""SELECT state, count(*) FROM insight.finding WHERE state <> 'resolved' GROUP BY state""")
        counts = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM insight.finding WHERE state = 'open' AND triage")
        triage = cur.fetchone()[0]  # type: ignore[index]
    if last is None:
        return {"measured": False, "trend": [], "findings": {"open": 0, "excepted": 0, "triage": 0},
                "can_admin": _admin(ctx)}
    at, details = last
    age_h = (datetime.now(UTC) - at).total_seconds() / 3600
    return {"measured": True, "measured_at": at.isoformat(), "stale": age_h > 2, **details, "trend": trend,
            "findings": {"open": counts.get("open", 0), "excepted": counts.get("excepted", 0), "triage": triage},
            "can_admin": _admin(ctx)}


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------------------------- kurallar
_RULE_COLS = """q.sys_id, q.name, q.description, q.check_kind, q.params, q.scope_class, q.scope_query, q.dimension,
                q.severity, q.open_task, q.task_title, q.task_instructions, q.due_days, q.channel, q.exception_days,
                q.max_findings, q.interval_hours, q.state, q.next_run_at, q.last_run_at, q.created_by, q.created_at,
                q.updated_by, q.updated_at,
                (SELECT count(*) FROM insight.finding f WHERE f.rule = q.sys_id AND f.state = 'open'),
                (SELECT count(*) FROM insight.finding f WHERE f.rule = q.sys_id AND f.state = 'excepted'),
                (SELECT count(*) FROM insight.finding f WHERE f.rule = q.sys_id AND f.state = 'open' AND f.triage),
                (SELECT jsonb_build_object('status', r.status, 'at', r.started_at, 'in_scope', r.in_scope,
                                           'matched', r.matched, 'note', r.note)
                   FROM insight.quality_rule_run r WHERE r.rule = q.sys_id ORDER BY r.id DESC LIMIT 1)"""


def _rule(r: tuple[Any, ...], md: Any) -> dict[str, Any]:
    cls = next((c for c in md.classes if c.get("name") == r[5]), {})
    last = r[27]
    return {
        "sys_id": r[0], "name": r[1], "description": r[2], "check_kind": r[3],
        "check_kind_tr": CHECK_KINDS.get(r[3], {}).get("label", r[3]), "params": r[4], "scope_class": r[5],
        "scope_class_tr": cls.get("label_tr", r[5]), "scope_query": r[6], "dimension": r[7],
        "dimension_tr": DIMENSION_TR.get(r[7], r[7]), "severity": r[8], "severity_tr": SEVERITY_TR.get(r[8], r[8]),
        "open_task": r[9], "task_title": r[10], "task_instructions": r[11], "due_days": r[12], "channel": r[13],
        "exception_days": r[14], "max_findings": r[15], "interval_hours": r[16],
        "interval_tr": INTERVALS.get(r[16], f"{r[16]} saatte bir"), "state": r[17], "state_tr": STATE_TR[r[17]],
        "next_run_at": r[18].isoformat() if r[18] and r[17] == "active" else None,
        "last_run_at": r[19].isoformat() if r[19] else None, "created_by": r[20], "created_at": r[21].isoformat(),
        "updated_by": r[22], "updated_at": r[23].isoformat(), "open": r[24], "excepted": r[25], "triage": r[26],
        "last_run": ({**last, "status_tr": RUN_STATUS_TR.get(last["status"], last["status"])} if last else None),
    }


@router.get("/rules")
def list_rules(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT {_RULE_COLS} FROM insight.quality_rule q ORDER BY (q.state = 'retired'), q.name")  # noqa: S608 - sabit
        items = [_rule(r, md) for r in cur.fetchall()]
    return {
        "items": items, "can_admin": _admin(ctx),
        "kinds": [{"id": k, **v} for k, v in CHECK_KINDS.items()],
        "dimensions": [{"id": d, "label": DIMENSION_TR[d]} for d in DIMENSIONS],
        "severities": [{"id": k, "label": v} for k, v in SEVERITY_TR.items()],
        "intervals": [{"id": k, "label": v} for k, v in INTERVALS.items()],
        "roles": [{"id": r.name, "label": r.label_tr} for r in md.sources.roles.values()],
        "relationship_types": sorted(t["name"] for t in md.relationship_types),
        "templates": TEMPLATES, "classes": _class_tree(md),
    }


@router.get("/fields")
def class_fields(cls: str = Query(alias="class", pattern=r"^[a-z][a-z0-9_]{1,79}$"),
                 ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Boş alan kuralı için sınıfın (ve atalarının) sözlük alanları."""
    ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    if cls not in md.hierarchy:
        raise ApiError(404, "bulunamadi", "Sınıf bulunamadı.")
    fields = md.fields_of(cls)
    items = sorted(({"name": k, "label": str(v.get("label_tr") or k), "type": v.get("type")} for k, v in fields.items()),
                   key=lambda f: f["label"].casefold())
    return {"class": cls, "items": items}


@router.get("/rules/{sys_id}")
def get_rule(sys_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    if not _SYSID.fullmatch(sys_id):
        raise ApiError(404, "bulunamadi", "Kural bulunamadı.")
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT {_RULE_COLS} FROM insight.quality_rule q WHERE q.sys_id = %s", (sys_id,))  # noqa: S608 - sabit
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kural bulunamadı.")
        item = _rule(row, ctx.state.metadata)
        cur.execute("""SELECT id, trigger, requested_by, started_at, finished_at, status, in_scope, matched, opened,
                              resolved, tasks_opened, note
                         FROM insight.quality_rule_run WHERE rule = %s ORDER BY id DESC LIMIT 20""", (sys_id,))
        item["runs"] = [{"id": a, "trigger": b, "requested_by": c, "started_at": d.isoformat(),
                         "finished_at": e.isoformat() if e else None, "status": f,
                         "status_tr": RUN_STATUS_TR.get(f, f), "in_scope": g, "matched": h, "opened": i,
                         "resolved": j, "tasks_opened": k, "note": n}
                        for a, b, c, d, e, f, g, h, i, j, k, n in cur.fetchall()]
    item["transitions"] = sorted(TRANSITIONS[item["state"]])
    item["can_admin"] = _admin(ctx)
    return item


class PreviewIn(BaseModel):
    check_kind: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    scope_class: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    scope_query: str = Field(default="", max_length=2000)
    rule: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@router.post("/rules/preview", dependencies=[Depends(require_csrf)])
def preview(body: PreviewIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Kural etkinleşse kaç CI bulgu olur, görevler kime gider; geçersiz kural 200 ile ``valid: false``."""
    ctx.require("governance", "task_admin")
    md = ctx.state.metadata
    try:
        plan = compile_rule(md, body.scope_class, body.scope_query, body.check_kind, body.params)
    except ValueError as exc:
        return {"valid": False, "problem": str(exc)}
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM cmdb.cmdb_ci c WHERE {plan.scope_sql}", plan.scope_params)  # noqa: S608 - sözlükten derlendi
        in_scope = cur.fetchone()[0]  # type: ignore[index]
        where, params = plan.where
        partial = False
        texts: dict[str, str] = {}
        if plan.relation_check:
            cur.execute(f"SELECT c.sys_id FROM cmdb.cmdb_ci c WHERE {where} LIMIT %s",  # noqa: S608 - sözlükten derlendi
                        [*params, PREVIEW_CANDIDATES + 1])
            cand = [r[0] for r in cur.fetchall()]
            partial = len(cand) > PREVIEW_CANDIDATES
            cur.execute("""SELECT r.parent, p.sys_class_name, t.name, ch.sys_class_name, ch.name
                             FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_ci p ON p.sys_id = r.parent
                             JOIN cmdb.cmdb_ci ch ON ch.sys_id = r.child JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                            WHERE r.parent = ANY(%s)""", (cand[:PREVIEW_CANDIDATES],))
            rels: dict[str, list[tuple[str, str, str, str]]] = {}
            for parent, pc, rt, cc, cn in cur.fetchall():
                rels.setdefault(parent, []).append((pc, rt, cc, cn))
            for ci, found in rels.items():
                if bad := relation_violations(md, found):
                    texts[ci] = describe_details("relation_not_allowed", {"relations": bad})
            where, params = "c.sys_id = ANY(%s)", [sorted(texts)]
        p = _preview(cur, where, params, None)
        if plan.detail_sql and p["sample"]:
            labels = {k: str(v.get("label_tr") or k) for k, v in md.fields_of(body.scope_class).items()}
            cur.execute(f"SELECT c.sys_id, {plan.detail_sql} FROM cmdb.cmdb_ci c WHERE c.sys_id = ANY(%s)",  # noqa: S608 - sözlükten derlendi
                        [*plan.detail_params, [s["sys_id"] for s in p["sample"]]])
            role_labels = {k: v.label_tr for k, v in md.sources.roles.items()}
            for ci, extra in cur.fetchall():
                d = extra if isinstance(extra, dict) else {"empty": list(extra or [])}
                texts[ci] = describe_details(body.check_kind, d, labels, role_labels=role_labels)
        for s in p["sample"]:
            s["why"] = texts.get(s["sys_id"]) or plan.summary
        current = 0
        if body.rule:
            cur.execute("SELECT count(*) FROM insight.finding WHERE rule = %s AND state <> 'resolved'", (body.rule,))
            current = cur.fetchone()[0]  # type: ignore[index]
    return {"valid": True, "in_scope": in_scope, "matched": p.pop("in_scope"), "partial": partial,
            "summary": plan.summary, "current_findings": current,
            "inconclusive": _role_problem(ctx, plan.depends_on_roles) if plan.depends_on_roles else None, **p}


class RuleIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=2000)
    check_kind: Literal["no_relation", "missing_fields", "not_seen_by_role", "matches_condition", "relation_not_allowed",
                        "coverage_gap"]
    params: dict[str, Any] = Field(default_factory=dict)
    scope_class: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    scope_query: str = Field(default="", max_length=2000)
    dimension: Literal["completeness", "correctness", "compliance", "coverage"]
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    open_task: bool = True
    task_title: str = Field(min_length=3, max_length=120)
    task_instructions: str = Field(default="", max_length=4000)
    due_days: int = Field(default=30, ge=1, le=180)
    channel: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    exception_days: int = Field(default=180, ge=1, le=730)
    max_findings: int = Field(default=5000, ge=1, le=50000)
    interval_hours: Literal[1, 6, 24, 168] = 24
    activate: bool = False
    idempotency_key: str = Field(pattern=_KEY)


def _validate(ctx: Ctx, kind: str, params: dict[str, Any], scope_class: str, scope_query: str) -> None:
    try:
        compile_rule(ctx.state.metadata, scope_class, scope_query, kind, params)
    except ValueError as exc:
        raise ApiError(422, "kural_gecersiz", str(exc)) from None


def _channel_ok(cur: psycopg.Cursor[Any], channel: str | None) -> None:
    if channel:
        cur.execute("SELECT 1 FROM gov.task_channel WHERE name = %s AND lifecycle = 'active'", (channel,))
        if cur.fetchone() is None:
            raise ApiError(422, "kanal_yok", "Kanal bulunamadı veya etkin değil.")


@router.post("/rules", dependencies=[Depends(require_csrf)], status_code=201)
def create_rule(body: RuleIn, ctx: Ctx = Depends(context)) -> JSONResponse:
    ctx.require("governance", "task_admin")
    _validate(ctx, body.check_kind, body.params, body.scope_class, body.scope_query)
    p = ctx.principal
    digest = request_hash("quality_rule", body.model_dump(mode="json", exclude={"idempotency_key"}))
    state = "active" if body.activate else "draft"
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        _channel_ok(cur, body.channel)
        try:
            with ctx.conn.transaction():
                cur.execute(
                    """INSERT INTO insight.quality_rule (name, description, check_kind, params, scope_class, scope_query,
                                                         dimension, severity, open_task, task_title, task_instructions,
                                                         due_days, channel, exception_days, max_findings, interval_hours,
                                                         state, next_run_at, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               CASE WHEN %s = 'active' THEN now() END, %s) RETURNING sys_id""",
                    (body.name.strip(), body.description.strip(), body.check_kind, Jsonb(body.params), body.scope_class,
                     body.scope_query.strip(), body.dimension, body.severity, body.open_task, body.task_title.strip(),
                     body.task_instructions.strip(), body.due_days, body.channel, body.exception_days, body.max_findings,
                     body.interval_hours, state, state, p.audit_id))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "ad_var", "Bu adla bir kural zaten var.") from None
        sys_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'quality_rule_create', %s, 'allow', %s)",
                    (p.audit_id, f"quality_rule:{sys_id}", Jsonb({"name": body.name.strip(), "state": state,
                                                                   "check_kind": body.check_kind})))
        if state == "active":
            cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
        msg = ("Kural etkin; görev çalışanı birkaç saniye içinde değerlendirir." if state == "active"
               else "Kural taslak olarak kaydedildi; önizleyip etkinleştirin.")
        response = {"sys_id": sys_id, "state": state, "message": msg}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/quality/rules", digest, 201, response)
    return JSONResponse({**response, "replayed": False}, status_code=201)


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    params: dict[str, Any] | None = None
    scope_class: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,79}$")
    scope_query: str | None = Field(default=None, max_length=2000)
    dimension: Literal["completeness", "correctness", "compliance", "coverage"] | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    open_task: bool | None = None
    task_title: str | None = Field(default=None, min_length=3, max_length=120)
    task_instructions: str | None = Field(default=None, max_length=4000)
    due_days: int | None = Field(default=None, ge=1, le=180)
    exception_days: int | None = Field(default=None, ge=1, le=730)
    max_findings: int | None = Field(default=None, ge=1, le=50000)
    interval_hours: Literal[1, 6, 24, 168] | None = None
    state: Literal["active", "paused", "retired"] | None = None
    reason: str = Field(default="", max_length=500)


@router.patch("/rules/{sys_id}", dependencies=[Depends(require_csrf)])
def update_rule(sys_id: str, body: RulePatch, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Taslak veya duraklatılmış kural düzenlenir; durum: etkinleştir, duraklat, emekliye ayır (gerekçeli).
    Emekliye ayrılan kuralın bulguları açık kalır ama yeni görev açılmaz ve bulgu güncellenmez."""
    ctx.require("governance", "task_admin")
    if not _SYSID.fullmatch(sys_id):
        raise ApiError(404, "bulunamadi", "Kural bulunamadı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("""SELECT state, check_kind, params, scope_class, scope_query FROM insight.quality_rule
                        WHERE sys_id = %s FOR UPDATE""", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kural bulunamadı.")
        state, kind, params, scope_class, scope_query = row
        edits = {k: v for k, v in body.model_dump(exclude={"state", "reason"}).items() if v is not None}
        if edits and state not in ("draft", "paused"):
            raise ApiError(409, "duzenlenemez", "Yalnızca taslak veya duraklatılmış kural düzenlenir; önce duraklatın.")
        _validate(ctx, kind, edits.get("params", params), edits.get("scope_class", scope_class),
                  edits.get("scope_query", scope_query))
        new_state = state
        if body.state and body.state != state:
            if body.state not in TRANSITIONS[state]:
                raise ApiError(409, "gecis_yok", f"{STATE_TR[state]} kural {STATE_TR[body.state].lower()} yapılamaz.")
            if len(body.reason.strip()) < MIN_TEXT:
                raise ApiError(422, "gerekce_gerekli", f"Durum değişikliği için en az {MIN_TEXT} karakterlik gerekçe yazın.")
            new_state = body.state
        if not edits and new_state == state:
            return {"sys_id": sys_id, "changed": [], "message": "Değişiklik yok."}
        if "params" in edits:
            edits["params"] = Jsonb(edits["params"])
        sets = [f"{k} = %s" for k in edits] + ["state = %s", "updated_by = %s", "updated_at = now()"]
        values = [*edits.values(), new_state, actor]
        if new_state != state:
            sets.append("next_run_at = CASE WHEN %s = 'active' THEN now() END")
            values.append(new_state)
        try:
            with ctx.conn.transaction():
                cur.execute(f"UPDATE insight.quality_rule SET {', '.join(sets)} WHERE sys_id = %s",  # noqa: S608 - sütunlar modelden
                            (*values, sys_id))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "ad_var", "Bu adla bir kural zaten var.") from None
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'quality_rule_update', %s, 'allow', %s)",
                    (actor, f"quality_rule:{sys_id}", Jsonb({"changes": sorted(edits), "state": new_state,
                                                             "reason": body.reason.strip() or None})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
    msg = {"active": "Kural etkin; görev çalışanı birkaç saniye içinde değerlendirir.",
           "paused": "Kural duraklatıldı; bulgular ve açık görevler kalır, değerlendirme durur.",
           "retired": "Kural emekliye ayrıldı; bir daha değerlendirilmez."}.get(new_state, "Kaydedildi.")
    return {"sys_id": sys_id, "state": new_state, "changed": sorted(edits), "message": msg}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/rules/{sys_id}/run", dependencies=[Depends(require_csrf)], status_code=202)
def run_rule(sys_id: str, body: ReasonIn, ctx: Ctx = Depends(context)) -> JSONResponse:
    ctx.require("governance", "task_admin")
    if len(body.reason.strip()) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"Gerekçe en az {MIN_TEXT} karakter olmalı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("SELECT state FROM insight.quality_rule WHERE sys_id = %s FOR UPDATE", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kural bulunamadı.")
        if row[0] != "active":
            raise ApiError(409, "etkin_degil", "Yalnızca etkin kural çalıştırılır.")
        cur.execute("SELECT 1 FROM insight.quality_rule_run WHERE rule = %s AND status IN ('requested', 'running')",
                    (sys_id,))
        if cur.fetchone():
            raise ApiError(409, "calisiyor", "Bu kural zaten çalışıyor veya çalışma isteği bekliyor.")
        cur.execute("""INSERT INTO insight.quality_rule_run (rule, trigger, requested_by, status)
                       VALUES (%s, 'manual', %s, 'requested') RETURNING id""", (sys_id, actor))
        run = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'quality_rule_run', %s, 'allow', %s)",
                    (actor, f"quality_rule:{sys_id}", Jsonb({"run": run, "reason": body.reason.strip()})))
        cur.execute("SELECT pg_notify('evren_task', %s)", (sys_id,))
    return JSONResponse({"run": run, "message": "Değerlendirme istendi; görev çalışanı birkaç saniye içinde sonuçlandırır."},
                        status_code=202)


# ---------------------------------------------------------------------------------------------- bulgular
_FINDING_SELECT = """
SELECT f.sys_id, f.state, f.triage, f.details, f.first_seen_at, f.last_seen_at, f.resolved_at, f.exception_until,
       f.exception_by, f.exception_note, q.sys_id, q.name, q.dimension, q.severity, q.check_kind, q.description,
       c.sys_id, c.name, c.sys_class_name,
       (SELECT jsonb_build_object('sys_id', t.sys_id, 'number', t.number, 'state', t.state, 'outcome', t.outcome,
                                  'assignee_basis', t.assignee_basis,
                                  'assignee', coalesce(u.name, u.user_name, g.name))
          FROM gov.task t LEFT JOIN ref.sys_user u ON u.sys_id = t.assignee_user
          LEFT JOIN ref.sys_user_group g ON g.sys_id = t.assignee_group
         WHERE t.source = 'rule' AND t.source_ref = f.sys_id ORDER BY t.created_at DESC LIMIT 1)
  FROM insight.finding f JOIN insight.quality_rule q ON q.sys_id = f.rule JOIN cmdb.cmdb_ci c ON c.sys_id = f.ci_sys_id"""


def _finding(r: tuple[Any, ...], ctx: Ctx) -> dict[str, Any]:
    details = r[3] or {}
    return {
        "sys_id": r[0], "state": r[1], "state_tr": FINDING_STATE_TR[r[1]], "triage": r[2],
        "text": details.get("text") or details.get("summary") or "", "details": details,
        "first_seen_at": r[4].isoformat(), "last_seen_at": r[5].isoformat(),
        "resolved_at": r[6].isoformat() if r[6] else None,
        "exception": ({"until": r[7].isoformat(), "by": r[8], "note": r[9]} if r[1] == "excepted" and r[7] else None),
        "rule": {"sys_id": r[10], "name": r[11], "dimension": r[12], "dimension_tr": DIMENSION_TR.get(r[12], r[12]),
                 "severity": r[13], "severity_tr": SEVERITY_TR.get(r[13], r[13]), "check_kind": r[14], "why": r[15]},
        "ci": {"sys_id": r[16], "name": r[17], "class": r[18], "class_label": ctx.state.presenter.class_label(r[18])},
        "task": r[19],
    }


@router.get("/findings")
def list_findings(ctx: Ctx = Depends(context), rule: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
                  ci: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
                  state: Literal["current", "open", "excepted", "resolved", "all"] = "current",
                  triage: bool = False, dimension: Literal["completeness", "correctness", "compliance", "coverage"] | None = None,
                  q: str = Query("", max_length=120), limit: int = Query(100, ge=1, le=500),
                  offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """Bulgular. ``current`` = açık + istisna. CI okuma izni gerekir; okunamayan sınıftaki CI listelenmez."""
    ctx.require("cmdb_ci", "read")
    conds, params = [], []
    if state == "current":
        conds.append("f.state <> 'resolved'")
    elif state != "all":
        conds.append("f.state = %s")
        params.append(state)
    for col, val in (("f.rule", rule), ("f.ci_sys_id", ci), ("q.dimension", dimension)):
        if val:
            conds.append(f"{col} = %s")
            params.append(val)
    if triage:
        conds.append("f.state = 'open' AND f.triage")
    if q.strip():
        conds.append("c.name ILIKE %s")
        params.append("%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    where = " AND ".join(conds) or "true"
    with ctx.conn.cursor() as cur:
        cur.execute(f"""{_FINDING_SELECT} WHERE {where}
                        ORDER BY (f.state = 'open') DESC, array_position(ARRAY['critical','high','medium','low'], q.severity),
                                 f.first_seen_at LIMIT %s OFFSET %s""", (*params, limit + 1, offset))
        rows = cur.fetchall()
        cur.execute(f"""SELECT count(*) FILTER (WHERE f.state = 'open'), count(*) FILTER (WHERE f.state = 'excepted'),
                               count(*) FILTER (WHERE f.state = 'open' AND f.triage)
                          FROM insight.finding f JOIN insight.quality_rule q ON q.sys_id = f.rule
                         WHERE f.state <> 'resolved' {"AND f.rule = %s" if rule else ""}""",  # noqa: S608 - sabit
                    [rule] if rule else [])
        n_open, n_exc, n_tri = cur.fetchone()  # type: ignore[misc]
    items = [_finding(r, ctx) for r in rows[:limit]]
    items = [i for i in items if ctx.grant(i["ci"]["class"], "read").allowed]
    return {"items": items, "more": len(rows) > limit, "counts": {"open": n_open, "excepted": n_exc, "triage": n_tri},
            "can_admin": _admin(ctx)}


@router.get("/findings/{sys_id}")
def get_finding(sys_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    if not _SYSID.fullmatch(sys_id):
        raise ApiError(404, "bulunamadi", "Bulgu bulunamadı.")
    with ctx.conn.cursor() as cur:
        cur.execute(f"{_FINDING_SELECT} WHERE f.sys_id = %s", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Bulgu bulunamadı.")
        item = _finding(row, ctx)
        if not ctx.grant(item["ci"]["class"], "read").allowed:
            raise ApiError(404, "bulunamadi", "Bulgu bulunamadı.")
        cur.execute("""SELECT t.sys_id, t.number, t.state, t.outcome, t.outcome_note, t.created_at, t.closed_at,
                              coalesce(u.name, u.user_name, g.name), t.assignee_basis
                         FROM gov.task t LEFT JOIN ref.sys_user u ON u.sys_id = t.assignee_user
                         LEFT JOIN ref.sys_user_group g ON g.sys_id = t.assignee_group
                        WHERE t.source = 'rule' AND t.source_ref = %s ORDER BY t.created_at DESC""", (sys_id,))
        item["tasks"] = [{"sys_id": a, "number": b, "state": c, "outcome": d, "note": e, "created_at": f.isoformat(),
                          "closed_at": g.isoformat() if g else None, "assignee": h, "assignee_basis": i}
                         for a, b, c, d, e, f, g, h, i in cur.fetchall()]
    admin = _admin(ctx)
    item["can_except"] = admin and item["state"] == "open"
    item["can_reopen"] = admin and (item["state"] == "excepted" or (item["state"] == "open" and item["triage"]))
    return item


class ExceptionIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    days: int = Field(default=180, ge=1, le=730)


@router.post("/findings/{sys_id}/exception", dependencies=[Depends(require_csrf)])
def except_finding(sys_id: str, body: ExceptionIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Konfigürasyon yöneticisi açık bulguyu süreli istisna yapar (bilinçli kabul edilen durum); açık görevi iptal
    edilir. Süre dolunca bulgu yeniden açılır."""
    ctx.require("governance", "task_admin")
    if len(body.reason.strip()) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"İstisna gerekçesi en az {MIN_TEXT} karakter olmalı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("""UPDATE insight.finding SET state = 'excepted', triage = false,
                              exception_until = now() + make_interval(days => %s), exception_by = %s, exception_note = %s
                        WHERE sys_id = %s AND state = 'open' RETURNING exception_until""",
                    (body.days, actor, body.reason.strip(), sys_id))
        row = cur.fetchone()
        if row is None:
            raise ApiError(409, "acik_degil", "Yalnızca açık bulgu istisna yapılır.")
        cur.execute("""UPDATE gov.task SET state = 'done', outcome = 'cancelled', closed_by = %s, closed_at = now(),
                              outcome_note = %s
                        WHERE source = 'rule' AND source_ref = %s AND state = 'open' RETURNING sys_id""",
                    (actor, f"Bulgu istisna yapıldı: {body.reason.strip()}"[:2000], sys_id))
        for (task,) in cur.fetchall():
            cur.execute("INSERT INTO gov.task_event (task, actor, action, detail) VALUES (%s, %s, 'cancelled', %s)",
                        (task, actor, Jsonb({"via": "finding_exception"})))
            cur.execute("SELECT pg_notify('evren_task', %s)", (task,))
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'finding_exception', %s, 'allow', %s)",
                    (actor, f"finding:{sys_id}", Jsonb({"days": body.days, "reason": body.reason.strip()})))
    until = row[0].strftime("%d.%m.%Y")
    return {"sys_id": sys_id, "state": "excepted", "message": f"Bulgu {until} tarihine kadar istisna; süre dolunca yeniden açılır."}


@router.post("/findings/{sys_id}/reopen", dependencies=[Depends(require_csrf)])
def reopen_finding(sys_id: str, body: ReasonIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """İstisnayı kaldırır veya ayıklamayı bitirir (sorumlu görevi reddetmişti): bulgu bir sonraki değerlendirmede
    yeniden görev alır."""
    ctx.require("governance", "task_admin")
    if len(body.reason.strip()) < MIN_TEXT:
        raise ApiError(422, "gerekce_gerekli", f"Gerekçe en az {MIN_TEXT} karakter olmalı.")
    actor = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("""UPDATE insight.finding SET state = 'open', triage = false
                        WHERE sys_id = %s AND (state = 'excepted' OR (state = 'open' AND triage)) RETURNING rule""",
                    (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(409, "acilamaz", "Yalnızca istisnadaki veya sorumlusu reddetmiş bulgu yeniden açılır.")
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'finding_reopen', %s, 'allow', %s)",
                    (actor, f"finding:{sys_id}", Jsonb({"reason": body.reason.strip()})))
    return {"sys_id": sys_id, "state": "open", "message": "Bulgu yeniden açık; kuralın bir sonraki değerlendirmesinde görev açılır."}


def ci_findings(ctx: Ctx, ci: str) -> list[dict[str, Any]]:
    """CI ayrıntısı için açık ve istisnalı bulgular (kural adı, neden, görev)."""
    rows = ctx.conn.execute(f"{_FINDING_SELECT} WHERE f.ci_sys_id = %s AND f.state <> 'resolved' "
                            "ORDER BY f.first_seen_at", (ci,)).fetchall()
    return [_finding(r, ctx) for r in rows]
