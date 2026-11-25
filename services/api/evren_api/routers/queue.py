"""Eşleştirme kuyruğu: liste, ayrıntı, karar (``idempotency_key`` ile tekrar güvenli).

API kimlik bağına ve golden record'a yazmaz: kararı ``ingest.match_candidate``'e ve ``events.outbox``'a yazar,
uzlaştırıcı uygular (``ReconcilerStore.apply_decisions``). Veritabanı yetkisi de buna izin vermez (göç 0006).
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1", tags=["eşleştirme"])

KINDS = ("review", "insufficient", "unmatched_shadow", "unmatched_retiring")
STATUS_FOR = {"merge": "accepted", "new_ci": "created_new", "fix_at_source": "rejected", "defer": "open"}
LABELS = {"merge": "Birleştirildi", "new_ci": "Yeni CI olarak açıldı", "fix_at_source": "Kaynakta düzeltilecek",
          "defer": "Ertelendi"}
COMPARE = ("name", "fqdn", "serial_number", "ip_address", "used_for")


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["merge", "new_ci", "defer", "fix_at_source"]
    ci_sys_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


def _items(ctx: Ctx, where: sql.Composable, params: list[Any], limit: int) -> list[dict[str, Any]]:
    pres = ctx.state.presenter
    reg = ctx.state.metadata.sources
    with ctx.conn.cursor() as cur:
        cur.execute(sql.SQL(
            """SELECT mc.sys_id, mc.kind, mc.status, mc.data_source, mc.source_key, mc.reason, mc.probability,
                       mc.features, mc.candidates, mc.priority, mc.created_at, mc.decision, mc.decision_ci,
                       mc.decided_by, mc.decided_at, mc.decision_note, mc.applied_at, mc.apply_error,
                       sr.class_hint, sr.attributes, sr.lookup, sr.last_seen
                  FROM ingest.match_candidate mc
                  JOIN ingest.source_record sr USING (data_source, source_key)
                 WHERE {} ORDER BY mc.priority, mc.created_at, mc.sys_id LIMIT %s""").format(where),
            (*params, limit))
        rows = cur.fetchall()
        cand_ids = sorted({c["sys_id"] for r in rows for c in r[8]})
        cands: dict[str, dict[str, Any]] = {}
        if cand_ids:
            cur.execute(
                """SELECT ci.sys_id, ci.name, ci.sys_class_name, to_jsonb(ci),
                          ARRAY(SELECT DISTINCT l.data_source FROM ingest.identity_link l
                                 WHERE l.ci_sys_id = ci.sys_id AND l.active ORDER BY 1)
                     FROM cmdb.cmdb_ci ci WHERE ci.sys_id = ANY(%s)""", (cand_ids,))
            for sid, name, cls, doc, srcs in cur.fetchall():
                if not ctx.grant(cls, "read").allowed:
                    continue
                attrs = {k: doc.get(k) or doc["class_attributes"].get(k) for k in COMPARE}
                attrs["os"] = doc["class_attributes"].get("os")
                attrs["bios_uuid"] = doc["class_attributes"].get("bios_uuid")
                cands[sid] = {"name": name or "(adsız)", "class_label": pres.class_label(cls), "sources": srcs,
                              "attributes": {k: v for k, v in attrs.items() if v is not None}}
    out = []
    for (sid, kind, status, ds, key, reason, _prob, features, candidates, prio, created, decision, dci, by, at, note,
         applied, apply_error, cls, attrs, lookup, seen) in rows:
        out.append({
            "id": sid, "code": f"EQ-{sid[:6].upper()}", "kind": kind, "status": status, "data_source": ds,
            "role": reg.role_of(ds) if ds in reg.sources else None, "source_key": key,
            "class_label": pres.class_label(cls), "observed_at": seen.isoformat(), "created_at": created.isoformat(),
            "summary": reason.split(";")[0], "rule": None, "reasons": [p.strip() for p in reason.split(";")[1:] if p.strip()],
            "record": {k: str(v) for k, v in attrs.items()}, "priority": prio,
            "lookup": {"ip": lookup.get("ip_addresses", []), "mac": lookup.get("mac_addresses", [])},
            "features": features,
            "candidates": [{"sys_id": c["sys_id"], "probability": c["probability"], **cands[c["sys_id"]]}
                           for c in candidates if c["sys_id"] in cands],
            "decision": None if decision is None else {"decision": decision, "label": LABELS[decision], "ci_sys_id": dci,
                                                       "by": by, "at": at.isoformat() if at else None, "note": note,
                                                       "applied": applied is not None and apply_error is None,
                                                       "error": apply_error},
        })
    return out


@router.get("/match-queue")
def queue(ctx: Ctx = Depends(context), kind: str | None = Query(None), status: str = Query("open"),
          limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    where: list[sql.Composable] = [sql.SQL("true")]
    params: list[Any] = []
    if kind:
        if kind not in KINDS:
            raise ApiError(400, "gecersiz_tur", "Kuyruk türü tanımsız.", [{"izinli": list(KINDS)}])
        where.append(sql.SQL("mc.kind = %s"))
        params.append(kind)
    if status == "decided":
        where.append(sql.SQL("mc.decided_at IS NOT NULL AND mc.status <> 'expired'"))
    elif status in ("open", "accepted", "rejected", "created_new", "expired"):
        where.append(sql.SQL("mc.status = %s"))
        params.append(status)
    else:
        raise ApiError(400, "gecersiz_durum", "Durum filtresi tanımsız.")
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT kind, count(*) FROM ingest.match_candidate WHERE status = 'open' GROUP BY 1")
        counts = dict(cur.fetchall())
    return {"items": _items(ctx, sql.SQL(" AND ").join(where), params, limit), "open_counts": counts,
            "can_decide": ctx.grant("match_queue", "review_match").allowed}


@router.get("/match-queue/{item_id}")
def queue_item(item_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    items = _items(ctx, sql.SQL("mc.sys_id = %s"), [item_id], 1)
    if not items:
        raise ApiError(404, "bulunamadi", "Kuyruk kaydı bulunamadı.")
    return items[0]


@router.post("/match-queue/{item_id}/decision", dependencies=[Depends(require_csrf)])
def decide(item_id: str, body: Decision, ctx: Ctx = Depends(context)) -> JSONResponse:
    ctx.require("match_queue", "review_match")
    p = ctx.principal
    digest = request_hash(item_id, body.model_dump(exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""SELECT mc.status, mc.candidate_ci, mc.candidates, sr.class_hint
                         FROM ingest.match_candidate mc JOIN ingest.source_record sr USING (data_source, source_key)
                        WHERE mc.sys_id = %s FOR UPDATE OF mc""", (item_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Kuyruk kaydı bulunamadı.")
        status, candidate_ci, candidates, class_hint = row
        if status != "open":
            raise ApiError(409, "karar_verilmis", "Bu kayıt için karar zaten verilmiş.", [{"status": status}])
        target = None
        if body.decision == "merge":
            target = body.ci_sys_id or candidate_ci
            if not target:
                raise ApiError(422, "hedef_gerekli", "Birleştirme için hedef CI seçin.")
            cur.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s", (target,))
            trow = cur.fetchone()
            if trow is None:
                raise ApiError(422, "hedef_yok", "Hedef CI bulunamadı.")
            h = ctx.state.metadata.hierarchy
            if class_hint in h and trow[0] in h and not h.compatible(class_hint, trow[0]):
                raise ApiError(422, "sinif_uyumsuz", "Kaynak kaydının sınıfı hedef CI ile uyumsuz.",
                               [{"kayit": class_hint, "hedef": trow[0]}])
        new_status = STATUS_FOR[body.decision]
        cur.execute(
            """UPDATE ingest.match_candidate SET status = %s, decision = %s, decision_ci = %s, decided_by = %s,
                      decided_at = now(), decision_note = %s
                WHERE sys_id = %s""",
            (new_status, body.decision, target, p.audit_id, body.note, item_id))
        cur.execute("INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload) "
                    "VALUES ('match_candidate', %s, 'evren.match.decided.v1', %s)",
                    (item_id, Jsonb({"id": item_id, "decision": body.decision, "ci_sys_id": target,
                                     "by": p.audit_id, "known_candidates": [c["sys_id"] for c in candidates]})))
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'match_decision', %s, 'allow', %s)",
                    (p.audit_id, f"match_candidate:{item_id}", Jsonb({"decision": body.decision, "ci_sys_id": target})))
        response = {"id": item_id, "status": new_status, "decision": body.decision, "label": LABELS[body.decision],
                    "ci_sys_id": target, "replayed": False,
                    "message": "Karar kaydedildi; uzlaştırıcı uyguladığında kimlik bağı ve CI güncellenir."}
        remember(cur, p.audit_id, body.idempotency_key, f"/api/v1/match-queue/{item_id}/decision", digest, 200,
                 response)
    return JSONResponse(response)
