"""Eskime işletimi (U7.1, F1-06, ADR-0028): eskimiş ve emekliye ayırma adayı CI önerileri, Veri sorumlusunun kararı.

Öneriyi uzlaştırıcı açar ve kapatır (``insight.staleness_case``); API yalnızca okur ve karar isteğini yazar
(``ingest.retirement_request``). Uzlaştırıcı kararı uygulama anında yeniden denetler (aday mı, kaynaklar sağlıklı mı)
ve emekliye ayırmada yaşam döngüsü alanlarını CI'ın Manual kaydına yazar. Okuma sınıf iznine tabidir.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from evren_core.staleness import STAGE_TR

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1", tags=["staleness"])

CASE_STATUS = {"open": "Açık", "retired": "Emekliye ayrıldı", "dismissed": "Korundu", "recovered": "Yeniden görüldü"}
REQUEST_STATUS = {"pending": "Uygulanmayı bekliyor", "applied": "Uygulandı", "rejected": "Uygulanmadı"}
ACTION_TR = {"retire": "Emekliye ayır", "dismiss": "Koru"}
VIEWS = {"candidate": "c.status = 'open' AND c.stage = 'candidate'", "stale": "c.status = 'open' AND c.stage = 'stale'",
         "retired": "c.status = 'retired'", "dismissed": "c.status = 'dismissed'"}
_KEY = r"^[A-Za-z0-9._:-]{8,128}$"


def _sysid(value: str) -> str:
    if len(value) != 32 or any(ch not in "0123456789abcdef" for ch in value):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    return value


def _days_since(when: datetime | None) -> int | None:
    return (datetime.now(UTC) - when).days if when else None


def _source_health(ctx: Ctx) -> list[dict[str, Any]]:
    reg = ctx.state.metadata.sources
    observers = sorted(n for n in reg.sources if reg.counts_for_staleness(n))
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (data_source) data_source, completed, breaker_state, started_at
                         FROM ingest.source_run WHERE data_source = ANY(%s) ORDER BY data_source, started_at DESC""",
                    (observers,))
        runs = {ds: (done, state, at) for ds, done, state, at in cur.fetchall()}
    out = []
    for name in observers:
        done, state, at = runs.get(name, (None, None, None))
        ok = bool(done) and state == "ok"
        out.append({"name": name, "label": reg.source(name).label_tr, "role": reg.role_of(name), "ok": ok,
                    "last_run": at.isoformat() if at else None,
                    "state": "ok" if ok else ("none" if at is None else "unhealthy")})
    return out


def _case_json(ctx: Ctx, row: dict[str, Any], pres: Any) -> dict[str, Any]:
    return {"id": row["sys_id"], "ci": row["ci_sys_id"], "name": row["name"] or "(adsız)", "class": row["cls"],
            "class_label": pres.class_label(row["cls"]), "stage": row["stage"], "stage_label": STAGE_TR[row["stage"]],
            "status": row["status"], "status_label": CASE_STATUS[row["status"]], "rule_class": row["rule_class"],
            "rule_label": pres.class_label(row["rule_class"]), "last_seen": row["last_seen"].isoformat(),
            "days_since": _days_since(row["last_seen"]),
            "observations": [{**o, "label": ctx.state.metadata.sources.source(o["data_source"]).label_tr
                              if o["data_source"] in ctx.state.metadata.sources.sources else o["data_source"]}
                             for o in row["observations"]],
            "opened_at": row["opened_at"].isoformat(), "closed_at": row["closed_at"].isoformat() if row["closed_at"] else None,
            "decided_by": row["decided_by"], "decision_note": row["decision_note"],
            "reappeared_at": row["reappeared_at"].isoformat() if row["reappeared_at"] else None,
            "pending": row.get("pending", False)}


_CASE_SQL = """SELECT c.sys_id, c.ci_sys_id, ci.name, ci.sys_class_name AS cls, c.stage, c.status, c.rule_class,
                      c.last_seen, c.observations, c.opened_at, c.closed_at, c.decided_by, c.decision_note,
                      c.reappeared_at,
                      EXISTS (SELECT 1 FROM ingest.retirement_request r WHERE r.case_sys_id = c.sys_id
                                 AND r.status = 'pending') AS pending
                 FROM insight.staleness_case c JOIN cmdb.cmdb_ci ci ON ci.sys_id = c.ci_sys_id"""


def ci_staleness(ctx: Ctx, sys_id: str, cls: str) -> dict[str, Any] | None:
    """CI ayrıntısındaki "Eskime" bölümü: açık öneri veya son karar; kişinin karar verip veremeyeceği."""
    with ctx.conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_CASE_SQL + " WHERE c.ci_sys_id = %s ORDER BY c.opened_at DESC LIMIT 1", (sys_id,))
        row = cur.fetchone()
    rule = ctx.state.metadata.staleness_policy.rule_for(cls, ctx.state.metadata.hierarchy)
    if row is None:
        return {"case": None, "rule": rule.as_json() if rule else None} if rule else None
    case = _case_json(ctx, row, ctx.state.presenter)
    can = ctx.grant(cls, "retire").allowed and case["status"] == "open" and not case["pending"]
    return {"case": case, "rule": rule.as_json() if rule else None, "can_decide": can,
            "can_retire": can and case["stage"] == "candidate"}


@router.get("/staleness/summary")
def summary(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Öneri sayıları, kurallar ve gözlemci kaynakların sağlığı (kararı bastıran kaynak görünür)."""
    ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    pres = ctx.state.presenter
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT count(*) FILTER (WHERE status = 'open' AND stage = 'candidate'),
                              count(*) FILTER (WHERE status = 'open' AND stage = 'stale'),
                              count(*) FILTER (WHERE status = 'retired'),
                              count(*) FILTER (WHERE status = 'dismissed'),
                              count(*) FILTER (WHERE status = 'recovered'),
                              count(*) FILTER (WHERE status = 'retired' AND reappeared_at IS NOT NULL)
                         FROM insight.staleness_case""")
        cand, stale, retired, dismissed, recovered, reappeared = cur.fetchone()  # type: ignore[misc]
    rules = [{**r.as_json(), "label": pres.class_label(r.applies_to)} for r in md.staleness_policy.rules.values()]
    return {"counts": {"candidate": cand, "stale": stale, "retired": retired, "dismissed": dismissed,
                       "recovered": recovered, "reappeared": reappeared},
            "rules": sorted(rules, key=lambda r: r["label"]), "sources": _source_health(ctx),
            "can_decide": ctx.grant("cmdb_ci", "retire").allowed}


@router.get("/staleness/cases")
def cases(ctx: Ctx = Depends(context), view: Literal["candidate", "stale", "retired", "dismissed"] = "candidate",
          q: str | None = Query(None, max_length=100), limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    """Görünüme göre öneriler (en uzun süredir görülmeyen önce). Okuma izni olmayan sınıfın CI'ı listelenmez."""
    ctx.require("cmdb_ci", "read")
    params: list[Any] = []
    search = ""
    if q and q.strip():
        search = " AND lower(ci.name) LIKE %s"
        params.append("%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    order = "c.last_seen, ci.name" if view in ("candidate", "stale") else "c.closed_at DESC, ci.name"
    with ctx.conn.cursor(row_factory=dict_row) as cur:
        # Koşul ve sıra sabit sözlükten; kullanıcı değeri yalnızca parametre
        cur.execute(sql.SQL("{base} WHERE {cond}{search} ORDER BY {order} LIMIT %s").format(
            base=sql.SQL(_CASE_SQL), cond=sql.SQL(VIEWS[view]), search=sql.SQL(search), order=sql.SQL(order)),
            [*params, limit])
        rows = cur.fetchall()
    pres = ctx.state.presenter
    readable: dict[str, bool] = {}
    out = []
    for r in rows:
        if r["cls"] not in readable:
            readable[r["cls"]] = ctx.grant(r["cls"], "read").allowed
        if readable[r["cls"]]:
            case = _case_json(ctx, r, pres)
            case["can_decide"] = (case["status"] == "open" and not case["pending"]
                                  and ctx.grant(r["cls"], "retire").allowed)
            out.append(case)
    return {"view": view, "rows": out}


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["retire", "dismiss"]
    note: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(pattern=_KEY)


@router.post("/staleness/{case_id}/decision", dependencies=[Depends(require_csrf)], status_code=202)
def decide(case_id: str, body: DecisionBody, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Emekliye ayır (yalnızca aday aşamasında) veya koru. Gerekçe zorunlu; uzlaştırıcı yeniden denetleyip uygular."""
    _sysid(case_id)
    note = body.note.strip()
    if not note:
        raise ApiError(422, "gerekce_zorunlu", "Gerekçe zorunludur.")
    p = ctx.principal
    digest = request_hash("staleness", case_id, body.action, note)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""SELECT c.ci_sys_id, ci.sys_class_name, c.stage, c.status,
                              EXISTS (SELECT 1 FROM ingest.retirement_request r WHERE r.ci_sys_id = c.ci_sys_id
                                         AND r.status = 'pending')
                         FROM insight.staleness_case c JOIN cmdb.cmdb_ci ci ON ci.sys_id = c.ci_sys_id
                        WHERE c.sys_id = %s""", (case_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Öneri bulunamadı.")
        ci, cls, stage, status, pending = row
        ctx.require(cls, "read")
        ctx.require(cls, "retire")
        if status != "open":
            raise ApiError(409, "oneri_kapali", "Öneri artık açık değil.")
        if pending:
            raise ApiError(409, "bekleyen_istek", "Bu CI için bekleyen bir karar var.")
        if body.action == "retire" and stage != "candidate":
            raise ApiError(409, "aday_degil", "CI henüz emekliye ayırma adayı değil (eskimiş aşamasında).")
        cur.execute("""INSERT INTO ingest.retirement_request (case_sys_id, ci_sys_id, action, note, requested_by)
                       VALUES (%s, %s, %s, %s, %s) RETURNING sys_id""", (case_id, ci, body.action, note, p.audit_id))
        rid = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'ci_retire_decision', %s, 'allow', %s)",
                    (p.audit_id, f"staleness_case:{case_id}", Jsonb({"ci": ci, "action": body.action, "request": rid})))
        response = {"id": rid, "status": "pending", "action": body.action, "replayed": False,
                    "message": f"{ACTION_TR[body.action]} kararı kaydedildi; uzlaştırıcı yeniden denetleyip uygular."}
        remember(cur, p.audit_id, body.idempotency_key, f"/api/v1/staleness/{case_id}/decision", digest, 202, response)
    return JSONResponse(response, status_code=202)


@router.get("/retirement-requests/{req_id}")
def request_status(req_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    _sysid(req_id)
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT r.ci_sys_id, ci.sys_class_name, r.action, r.status, r.result_note, r.requested_by,
                              r.requested_at, r.applied_at
                         FROM ingest.retirement_request r JOIN cmdb.cmdb_ci ci ON ci.sys_id = r.ci_sys_id
                        WHERE r.sys_id = %s""", (req_id,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "İstek bulunamadı.")
    ci, cls, action, status, note, by, at, applied = row
    ctx.require(cls, "read")
    return {"id": req_id, "ci": ci, "action": action, "action_label": ACTION_TR[action], "status": status,
            "status_label": REQUEST_STATUS[status], "result_note": note, "requested_by": by,
            "requested_at": at.isoformat(), "applied_at": applied.isoformat() if applied else None}
