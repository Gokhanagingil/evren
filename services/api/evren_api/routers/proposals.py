"""Yapay zekâ önerileri (U9.2, ADR-0043; kural 10, ISO/IEC 42001).

MCP öneri araçları ve kurum MCP uygulamasının REST adaptörü (``POST /api/v1/proposals``) öneri ekler; golden record'a
yazmaz. Yetkili bir kişi öneriyi kabul edince Evren'in her zamanki yolu **kararı verenin yetkisiyle** çalışır:

* ``ci_update``      → elle düzenleme isteği (Manual kaynak kaydı → uzlaştırıcı); ``cmdb_ci`` yazma izni gerekir.
* ``match_decision`` → eşleştirme kuyruğu kararı; ``review_match`` izni gerekir.

Öneriyi yapan kabul edemez (veritabanı kısıtı). Öneri silinmez, karar değişmez. Her öneride model, istem sürümü,
girdi özeti ve gerekçe saklanır; öneri ve karar denetim izine yazılır.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["öneri"])

KIND_TR = {"ci_update": "CI değişikliği", "match_decision": "Eşleştirme kararı"}
STATUS_TR = {"pending": "Karar bekliyor", "accepted": "Kabul edildi", "rejected": "Reddedildi",
             "failed": "Uygulanamadı"}
DECISION_TR = {"merge": "Birleştir", "new_ci": "Yeni CI", "fix_at_source": "Kaynakta düzelt"}
#: Bir önericinin aynı anda karar bekleyen en çok öneri sayısı (sel önleme)
MAX_PENDING = 500


def _audit(ctx: Ctx, action: str, resource: str, detail: dict[str, Any]) -> None:
    ctx.conn.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                     "VALUES (%s, %s, %s, 'allow', %s)", (ctx.principal.audit_id, action, resource, Jsonb(detail)))


def create_proposal(ctx: Ctx, *, kind: str, target: str, payload: dict[str, Any], rationale: str, model: str | None,
                    prompt_version: str | None, channel: str, idempotency_key: str | None,
                    input_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """Öneriyi doğrulayıp ekler. Alanlar sözlükten doğrulanır (elle düzenlemeyle aynı denetim); hedef var olmalı."""
    me = ctx.principal.audit_id
    if idempotency_key:
        prior = ctx.conn.execute("SELECT sys_id, number, status FROM ai.proposal WHERE proposed_by = %s "
                                 "AND idempotency_key = %s", (me, idempotency_key)).fetchone()
        if prior:
            return {"sys_id": prior[0], "number": prior[1], "status": prior[2], "replayed": True,
                    "message": f"Öneri zaten kayıtlı: {prior[1]}."}
    if kind == "ci_update":
        row = ctx.conn.execute("SELECT sys_class_name, name FROM cmdb.cmdb_ci WHERE sys_id = %s", (target,)).fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "CI bulunamadı.")
        grant = ctx.require(row[0], "read")
        values = ctx.state.edit_policy.validate(ctx.conn, row[0], grant, payload.get("changes") or {})
        payload, label = {"changes": values}, row[1]
    elif kind == "match_decision":
        ctx.require("cmdb_ci", "read")
        row = ctx.conn.execute("SELECT status, source_key FROM ingest.match_candidate WHERE sys_id = %s",
                               (target,)).fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kuyruk kaydı bulunamadı.")
        if row[0] != "open":
            raise ApiError(409, "karar_verilmis", "Bu kuyruk kaydına zaten karar verilmiş.")
        if payload.get("decision") == "merge" and not ctx.conn.execute(
                "SELECT 1 FROM cmdb.cmdb_ci WHERE sys_id = %s", (payload.get("ci_sys_id"),)).fetchone():
            raise ApiError(404, "bulunamadi", "Birleştirilecek CI bulunamadı.")
        label = str(row[1])[:255]
    else:
        raise ApiError(422, "gecersiz_tur", "Öneri türü ci_update veya match_decision olmalı.")
    pending = ctx.conn.execute("SELECT count(*) FROM ai.proposal WHERE proposed_by = %s AND status = 'pending'",
                               (me,)).fetchone()[0]
    if pending >= MAX_PENDING:
        raise ApiError(429, "cok_fazla_oneri", f"Karar bekleyen {MAX_PENDING} öneriniz var; önce karar verilmesini "
                                               "bekleyin.")
    with ctx.conn.transaction():
        sys_id, number = ctx.conn.execute(
            """INSERT INTO ai.proposal (kind, target, target_label, payload, rationale, model, prompt_version,
                                        input_summary, channel, proposed_by, idempotency_key)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING sys_id, number""",
            (kind, target, label, Jsonb(payload), rationale, model, prompt_version, Jsonb(input_summary or {}),
             channel, me, idempotency_key)).fetchone()
        _audit(ctx, "proposal_create", f"proposal:{sys_id}", {"number": number, "kind": kind, "target": target,
                                                              "channel": channel, "model": model})
    return {"sys_id": sys_id, "number": number, "status": "pending", "replayed": False,
            "message": f"Öneri kaydedildi ({number}); yetkili bir kişi onaylayınca uygulanır. Evren'e henüz bir şey "
                       "yazılmadı."}


def _can_decide(ctx: Ctx, kind: str, cls: str | None) -> bool:
    if kind == "ci_update":
        return ctx.grant(cls or "cmdb_ci", "write").allowed
    return ctx.grant("match_queue", "review_match").allowed


@router.get("/proposals")
def list_proposals(ctx: Ctx = Depends(context), status: Literal["pending", "decided"] = Query("pending"),
                   limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    cond = "p.status = 'pending'" if status == "pending" else "p.status <> 'pending'"
    rows = ctx.conn.execute(
        f"""SELECT p.sys_id, p.number, p.kind, p.target, p.target_label, p.payload, p.rationale, p.model,
                   p.prompt_version, p.channel, p.proposed_by, p.proposed_at, p.status, p.decided_by, p.decided_at,
                   p.decision_note, p.result, c.sys_class_name
              FROM ai.proposal p LEFT JOIN cmdb.cmdb_ci c ON p.kind = 'ci_update' AND c.sys_id = p.target
             WHERE {cond} ORDER BY p.proposed_at DESC LIMIT %s""", (limit,)).fetchall()  # nosec B608  # noqa: S608
    me = ctx.principal.audit_id
    items = []
    for r in rows:
        payload = r[5] or {}
        summary = (", ".join(f"{k} → {v}" for k, v in (payload.get("changes") or {}).items())[:300]
                   if r[2] == "ci_update" else DECISION_TR.get(payload.get("decision"), payload.get("decision")))
        items.append({"sys_id": r[0], "number": r[1], "kind": r[2], "kind_label": KIND_TR[r[2]], "target": r[3],
                      "target_label": r[4], "payload": payload, "summary": summary, "rationale": r[6], "model": r[7],
                      "prompt_version": r[8], "channel": r[9], "proposed_by": r[10], "proposed_at": r[11],
                      "status": r[12], "status_label": STATUS_TR[r[12]], "decided_by": r[13], "decided_at": r[14],
                      "decision_note": r[15], "result": r[16],
                      "can_decide": r[12] == "pending" and r[10] != me and _can_decide(ctx, r[2], r[17]),
                      "own": r[10] == me})
    return {"items": items, "status": status}


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ci_update", "match_decision"]
    target: str = Field(pattern=r"^[0-9a-f]{32}$")
    payload: dict[str, Any]
    rationale: str = Field(min_length=10, max_length=4000)
    model: str | None = Field(default=None, max_length=120)
    prompt_version: str | None = Field(default=None, max_length=60)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


@router.post("/proposals", dependencies=[Depends(require_csrf)], status_code=201)
def post_proposal(body: ProposalIn, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Kurum MCP uygulamasının REST adaptörü için öneri (``docs/09-mcp-sozlesmesi.md``); ``idempotency_key`` zorunlu."""
    payload = body.payload
    if body.kind == "match_decision" and payload.get("decision") not in ("merge", "new_ci", "fix_at_source"):
        raise ApiError(422, "gecersiz_karar", "decision merge, new_ci veya fix_at_source olmalı.")
    if body.kind == "ci_update" and not isinstance(payload.get("changes"), dict):
        raise ApiError(422, "gecersiz_oneri", "payload.changes {alan: değer} nesnesi olmalı.")
    res = create_proposal(ctx, kind=body.kind, target=body.target, payload=payload, rationale=body.rationale.strip(),
                          model=body.model, prompt_version=body.prompt_version, channel="api",
                          idempotency_key=body.idempotency_key, input_summary=body.input_summary)
    return JSONResponse(res, status_code=200 if res["replayed"] else 201)


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "reject"]
    note: str = Field(min_length=10, max_length=2000)


@router.post("/proposals/{sys_id}/decide", dependencies=[Depends(require_csrf)])
def decide_proposal(sys_id: str, body: DecideIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    from .ci import ManualEditRequest, edit_ci
    from .queue import Decision, decide

    me = ctx.principal.audit_id
    row = ctx.conn.execute("SELECT number, kind, target, payload, proposed_by, status FROM ai.proposal "
                           "WHERE sys_id = %s", (sys_id,)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "Öneri bulunamadı.")
    number, kind, target, payload, proposer, status = row
    if status != "pending":
        raise ApiError(409, "karar_verilmis", "Bu öneriye zaten karar verilmiş.")
    if proposer == me:
        raise ApiError(409, "kendi_oneriniz", "Kendi önerinize karar veremezsiniz.")
    note = body.note.strip()
    if body.decision == "reject":
        _finish(ctx, sys_id, "rejected", note, None)
        return {"status": "rejected", "message": f"{number} reddedildi."}
    key = f"oneri-{sys_id}"
    try:
        if kind == "ci_update":
            res = edit_ci(target, ManualEditRequest(attributes=payload["changes"], idempotency_key=key,
                                                    note=f"{number} önerisi: {note}"[:2000]), ctx)
        else:
            res = decide(target, Decision(decision=payload["decision"], ci_sys_id=payload.get("ci_sys_id"),
                                          note=f"{number} önerisi: {note}"[:2000], idempotency_key=key), ctx)
    except ApiError as exc:
        if exc.status == 403:
            raise
        _finish(ctx, sys_id, "failed", note, {"code": exc.code, "message": exc.message})
        raise ApiError(409, "uygulanamadi", f"Öneri uygulanamadı: {exc.message}") from None
    import json
    result = json.loads(bytes(res.body))
    _finish(ctx, sys_id, "accepted", note, {"request": result.get("id") or result.get("status"),
                                            "message": result.get("message")})
    return {"status": "accepted", "message": f"{number} kabul edildi: {result.get('message') or 'uygulandı.'}"}


def _finish(ctx: Ctx, sys_id: str, status: str, note: str, result: dict[str, Any] | None) -> None:
    with ctx.conn.transaction():
        ctx.conn.execute("""UPDATE ai.proposal SET status = %s, decided_by = %s, decided_at = now(), decision_note = %s,
                                   result = %s WHERE sys_id = %s AND status = 'pending'""",
                         (status, ctx.principal.audit_id, note, Jsonb(result) if result else None, sys_id))
        _audit(ctx, "proposal_decide", f"proposal:{sys_id}", {"status": status, "note": note})
