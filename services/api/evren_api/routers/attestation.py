"""Sahiplik onayı (U6.4, F1-17, ADR-0027): CI sorumlusunun dönemsel beyanı, sahipsiz ve süresi dolan CI raporu.

API onay alanlarını yazmaz (kural 1): isteği ``ingest.attestation_request``'e yazar; uzlaştırıcı kişiyi ve kapsamı
yeniden denetleyip CI'ın Manual kaynak kaydına yazar. Onay verebilmek için üç koşul birlikte gerekir: ``attest`` izni
(CI sahibi rolü), oturumdaki kişinin ``ref.sys_user`` kaydına eşlenmesi ve o kaydın CI'ın ``owned_by``/``managed_by``
alanında olması. Kapsam ve süre ``metadata/attestation.yaml``'dan; SQL ifadeleri doğrulanmış adlarla kurulur.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evren_core.attestation import REASONS_TR, STATE_TR

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1", tags=["attestation"])

REQUEST_STATUS = {"pending": "Uygulanmayı bekliyor", "applied": "Uygulandı", "rejected": "Uygulanmadı"}
DECISION_TR = {"attested": "Onay", "rejected": "Ret"}
#: Görünümler: bende (sorumlusu olduğum), sahipsiz, reddedilen, onay bekleyen (hiç onaylanmamış veya süresi dolmuş)
VIEWS = ("mine", "orphan", "rejected", "invalid")
_URGENCY = ("expired", "not_reviewed", "rejected", "due_soon", "valid")
_SYSID = r"^[0-9a-f]{32}$"
_KEY = r"^[A-Za-z0-9._:-]{8,128}$"
MAX_ITEMS = 200


def _now() -> datetime:
    return datetime.now(UTC)


def _scope_sql(ctx: Ctx) -> tuple[sql.Composable, list[Any]]:
    """Kapsamdaki CI'lar: ``period_days`` (politika süresi) ve ``state`` (durum kodu) sütunlarıyla alt sorgu."""
    md = ctx.state.metadata
    pol = md.attestation_policy
    period, params = pol.period_sql(md.hierarchy)
    state = pol.state_sql("x")
    now = _now()
    query = sql.SQL("""(SELECT x.*, {state} AS state, {orphan} AS orphan
                          FROM (SELECT ci.*, {period} AS period_days FROM cmdb.cmdb_ci ci) x
                         WHERE x.period_days IS NOT NULL) a""").format(
        state=sql.SQL(state), orphan=sql.SQL(pol.orphan_sql("x")), period=sql.SQL(period))
    return query, [now, now, *params]  # metin sırası: durum (şimdi ×2), sonra politika süresi


def ci_attestation(ctx: Ctx, sys_id: str, cls: str, doc: dict[str, Any]) -> dict[str, Any]:
    """CI ayrıntısındaki "Sahiplik onayı" bölümü: durum, politika, son onay, sorumlular, kişinin yapabilecekleri."""
    md = ctx.state.metadata
    pol = md.attestation_policy
    attrs = dict(doc)
    if isinstance(attrs.get("attested_date"), str):
        attrs["attested_date"] = datetime.fromisoformat(attrs["attested_date"])
    st = pol.state(cls, attrs, md.hierarchy, _now())
    pres = ctx.state.presenter
    names = pres.resolve_refs(ctx.conn, [*st["owners"], *([attrs["attested_by"]] if attrs.get("attested_by") else [])])
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT sys_id, decision, reason, note, requested_by, requested_at, status, result_note
                         FROM ingest.attestation_request WHERE ci_sys_id = %s ORDER BY requested_at DESC LIMIT 5""",
                    (sys_id,))
        history = [{"id": i, "decision": d, "decision_label": DECISION_TR[d], "reason": r,
                    "reason_label": REASONS_TR.get(r or ""), "note": n, "requested_by": by,
                    "requested_at": at.isoformat(), "status": s, "status_label": REQUEST_STATUS[s], "result_note": rn}
                   for i, d, r, n, by, at, s, rn in cur.fetchall()]
        cur.execute("""SELECT sys_id, status FROM insight.anomaly WHERE ci_sys_id = %s AND kind = 'attestation_rejected'
                          AND status IN ('open', 'acknowledged') ORDER BY detected_at DESC LIMIT 1""", (sys_id,))
        anomaly = cur.fetchone()
    pending = any(h["status"] == "pending" for h in history)
    code = _blocker_code(ctx, cls, st, pending)
    when = attrs.get("attested_date")
    return {
        "enabled": pol.enabled, "state": st["state"], "state_label": st["state_label"],
        "policy": st["policy"], "orphan": st["orphan"],
        "valid_until": st["valid_until"].isoformat() if st["valid_until"] else None,
        "attestation_status": attrs.get("attestation_status"),
        "attested_by": names.get(attrs.get("attested_by") or "") if attrs.get("attested_by") else None,
        "attested_date": when.isoformat() if isinstance(when, datetime) else None,
        "owners": [{"sys_id": o, "name": names.get(o, o)} for o in st["owners"]],
        "pending": pending, "history": history,
        "open_rejection": {"id": anomaly[0], "status": anomaly[1]} if anomaly else None,
        "can_attest": code is None, "blocker": BLOCKERS[code] if code else None, "blocker_code": code,
        "can_review": ctx.grant("anomaly", "anomaly_review").allowed,
        "reasons": [{"value": k, "label": v} for k, v in REASONS_TR.items()],
    }


BLOCKERS = {"out_of_scope": "Bu CI sahiplik onayı kapsamında değil.",
            "role": "Onay için CI sahibi rolü gerekir.",
            "no_user": "Kullanıcı kaydınız (sys_user) bulunamadı; onay veremezsiniz.",
            "not_owner": "Yalnızca CI'ın sahibi veya yöneteni onay verebilir.",
            "pending": "Bekleyen bir onay isteği var."}


def _blocker_code(ctx: Ctx, cls: str, st: dict[str, Any], pending: bool) -> str | None:
    """Kişi bu CI için onay veremiyorsa neden kodu (``BLOCKERS``); verebiliyorsa ``None``."""
    if st["state"] == "out_of_scope":
        return "out_of_scope"
    if not ctx.grant(cls, "attest").allowed:
        return "role"
    me = ctx.sys_user()
    if me is None:
        return "no_user"
    if me["sys_id"] not in st["owners"]:
        return "not_owner"
    return "pending" if pending else None


def _blocker(ctx: Ctx, cls: str, st: dict[str, Any], pending: bool) -> str | None:
    code = _blocker_code(ctx, cls, st, pending)
    return BLOCKERS[code] if code else None


@router.get("/attestation/summary")
def summary(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Kapsamdaki CI'ların duruma göre sayıları, sahipsiz CI sayısı ve kişinin kendi CI'ları."""
    ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    pol = md.attestation_policy
    me = ctx.sys_user()
    counts = {k: 0 for k in STATE_TR if k != "out_of_scope"}
    mine = {k: 0 for k in counts} if me else None
    orphan = 0
    if pol.enabled:
        scope, params = _scope_sql(ctx)
        owners = pol.owners_sql("a")
        with ctx.conn.cursor() as cur:
            cur.execute(sql.SQL("""SELECT state, count(*), count(*) FILTER (WHERE orphan),
                                          count(*) FILTER (WHERE %s = ANY({owners}))
                                     FROM {scope} GROUP BY state""").format(scope=scope, owners=sql.SQL(owners)),
                        [me["sys_id"] if me else None, *params])
            for state, n, o, m in cur.fetchall():
                counts[state] = n
                orphan += o
                if mine is not None:
                    mine[state] = m
    total = sum(counts.values())
    valid = counts["valid"] + counts["due_soon"]
    base = md.fields_of("cmdb_ci")
    pres = ctx.state.presenter
    policies = [{**p.as_json(), "where_label": " · ".join(
        f"{base.get(f, {}).get('label_tr', f)}: {', '.join(pres.choice(f, v) or v for v in sorted(vals))}"
        for f, vals in sorted(p.where.items())) or None} for p in pol.policies]
    return {"enabled": pol.enabled, "policies": policies,
            "reminder_days": pol.reminder.days, "owner_fields": list(pol.owner_fields),
            "labels": STATE_TR, "counts": counts, "in_scope": total, "orphan": orphan,
            "valid_ratio": round(valid / total, 3) if total else None, "mine": mine,
            "user": {"mapped": me is not None, "name": me["name"] if me else None,
                     "mode": ctx.principal.mode},
            "can_attest": ctx.grant("cmdb_ci", "attest").allowed and me is not None}


@router.get("/attestation/items")
def items(ctx: Ctx = Depends(context), view: Literal["mine", "orphan", "rejected", "invalid"] = "mine",
          q: str | None = Query(None, max_length=100), limit: int = Query(200, ge=1, le=500),
          offset: int = Query(0, ge=0, le=100_000)) -> dict[str, Any]:
    """Görünüme göre CI listesi (aciliyet sırasıyla). Okuma izni olmayan sınıfın CI'ı listelenmez."""
    ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    pol = md.attestation_policy
    me = ctx.sys_user()
    if not pol.enabled or (view == "mine" and me is None):
        return {"view": view, "rows": [], "total": 0, "user_mapped": me is not None}
    scope, params = _scope_sql(ctx)
    owners = sql.SQL(pol.owners_sql("a"))
    where = {"mine": sql.SQL("%s = ANY({})").format(owners), "orphan": sql.SQL("a.orphan"),
             "rejected": sql.SQL("a.state = 'rejected'"),
             "invalid": sql.SQL("a.state IN ('not_reviewed', 'expired')")}[view]
    filt_params: list[Any] = [me["sys_id"]] if view == "mine" and me else []
    search = sql.SQL("")
    if q and q.strip():
        search = sql.SQL(" AND lower(a.name) LIKE %s")
        filt_params.append("%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    order = sql.SQL("array_position(%s::text[], a.state), lower(a.name), a.sys_id")
    with ctx.conn.cursor() as cur:
        cur.execute(sql.SQL("""SELECT a.sys_id, a.name, a.sys_class_name, a.used_for, a.state, a.period_days,
                                      a.attested_by, a.attested_date, a.owned_by, a.managed_by,
                                      EXISTS (SELECT 1 FROM ingest.attestation_request r WHERE r.ci_sys_id = a.sys_id
                                                 AND r.status = 'pending'),
                                      count(*) OVER ()
                                 FROM {scope} WHERE {where}{search} ORDER BY {order} LIMIT %s OFFSET %s""").format(
            scope=scope, where=where, search=search, order=order),
            [*params, *filt_params, list(_URGENCY), limit, offset])
        fetched = cur.fetchall()
    pres = ctx.state.presenter
    refs = pres.resolve_refs(ctx.conn, [v for r in fetched for v in (r[6], r[8], r[9]) if v])
    readable: dict[str, bool] = {}
    rows = []
    for sid, name, cls, used_for, state, period, by, when, owned, managed, pending, _total in fetched:
        if cls not in readable:
            readable[cls] = ctx.grant(cls, "read").allowed
        if not readable[cls]:
            continue
        until = when + timedelta(days=int(period)) if when is not None and state not in ("not_reviewed", "rejected") else None
        rows.append({"sys_id": sid, "name": name or "(adsız)", "class": cls, "class_label": pres.class_label(cls),
                     "used_for": used_for, "used_for_label": pres.choice("used_for", used_for), "state": state,
                     "state_label": STATE_TR[state], "valid_until": until.isoformat() if until else None,
                     "attested_by": refs.get(by, by) if by else None,
                     "attested_date": when.isoformat() if when else None,
                     "owned_by": refs.get(owned, owned) if owned else None,
                     "managed_by": refs.get(managed, managed) if managed else None,
                     "mine": bool(me and me["sys_id"] in (owned, managed)), "pending": pending})
    return {"view": view, "rows": rows, "total": fetched[0][-1] if fetched else 0, "user_mapped": me is not None,
            "limit": limit, "offset": offset}


class AttestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[str] = Field(min_length=1, max_length=MAX_ITEMS)
    decision: Literal["attested", "rejected"]
    reason: Literal["not_owner", "not_exists", "data_wrong"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(pattern=_KEY)

    @model_validator(mode="after")
    def _check(self) -> AttestBody:
        if any(len(i) != 32 or any(c not in "0123456789abcdef" for c in i) for i in self.items):
            raise ValueError("CI kimlikleri 32 haneli küçük harf onaltılık olmalı")
        if len(set(self.items)) != len(self.items):
            raise ValueError("Aynı CI birden çok kez gönderildi")
        if self.decision == "rejected" and (self.reason is None or not (self.note or "").strip()):
            raise ValueError("Ret için neden ve gerekçe zorunludur")
        if self.decision == "attested" and self.reason is not None:
            raise ValueError("Onayda neden kodu olmaz")
        return self


@router.post("/attestations", dependencies=[Depends(require_csrf)], status_code=202)
def attest(body: AttestBody, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Onay veya ret isteği (tek ya da toplu). Uygun olmayan CI atlanır ve nedeni döner; hiçbiri uygun değilse 409.
    Uzlaştırıcı uygulama anında kişiyi ve kapsamı yeniden denetler."""
    ctx.require("cmdb_ci", "attest")
    me = ctx.sys_user()
    if me is None:
        ctx.security_event("attest:cmdb_ci", "deny", None, {"reason": "sys_user_yok"})
        raise ApiError(403, "kullanici_kaydi_yok",
                       "Kullanıcı kaydınız (sys_user) bulunamadı; sahiplik onayı veremezsiniz.")
    md = ctx.state.metadata
    pol = md.attestation_policy
    p = ctx.principal
    note = (body.note or "").strip() or None
    digest = request_hash("attest", sorted(body.items), body.decision, body.reason, note)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""SELECT to_jsonb(ci), EXISTS (SELECT 1 FROM ingest.attestation_request r
                                                      WHERE r.ci_sys_id = ci.sys_id AND r.status = 'pending')
                         FROM cmdb.cmdb_ci ci WHERE ci.sys_id = ANY(%s::bpchar[])""", (body.items,))
        found = {d["sys_id"]: (d, pending) for d, pending in cur.fetchall()}
        accepted: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        now = _now()
        for ci in body.items:
            if ci not in found:
                skipped.append({"ci": ci, "reason": "CI bulunamadı."})
                continue
            doc, pending = found[ci]
            cls = doc["sys_class_name"]
            if not ctx.grant(cls, "read").allowed:
                skipped.append({"ci": ci, "reason": "CI bulunamadı."})  # varlığı sızdırılmaz
                continue
            attrs = {**doc, "attested_date": datetime.fromisoformat(doc["attested_date"])
                     if doc.get("attested_date") else None}
            why = _blocker(ctx, cls, pol.state(cls, attrs, md.hierarchy, now), pending)
            if why:
                skipped.append({"ci": ci, "name": doc.get("name") or "", "reason": why})
                continue
            cur.execute("""INSERT INTO ingest.attestation_request (ci_sys_id, decision, reason, note, attester_user,
                                                                   requested_by)
                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                        (ci, body.decision, body.reason, note, me["sys_id"], p.audit_id))
            accepted.append({"ci": ci, "request": cur.fetchone()[0]})  # type: ignore[index]
        if not accepted:
            raise ApiError(409, "onaylanacak_ci_yok", "Gönderilen CI'ların hiçbiri için onay verilemiyor.", skipped)
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'ci_attest', %s, 'allow', %s)",
                    (p.audit_id, f"attestation:{body.decision}",
                     Jsonb({"decision": body.decision, "reason": body.reason, "count": len(accepted),
                            "cis": [a["ci"] for a in accepted][:MAX_ITEMS], "skipped": len(skipped),
                            "sys_user": me["sys_id"]})))
        word = "onay" if body.decision == "attested" else "ret"
        response = {"accepted": accepted, "skipped": skipped, "replayed": False,
                    "message": f"{len(accepted)} CI için {word} isteği kaydedildi; uzlaştırıcı uygular."
                               + (f" {len(skipped)} CI atlandı." if skipped else "")}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/attestations", digest, 202, response)
    return JSONResponse(response, status_code=202)


@router.get("/attestation-requests")
def request_status(ids: str = Query(..., max_length=MAX_ITEMS * 33),
                   ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """İsteklerin durumu (virgülle ayrılmış kimlikler); arayüz toplu onaydan sonra bunu izler."""
    wanted = [i for i in ids.split(",") if i]
    if not wanted or len(wanted) > MAX_ITEMS or any(len(i) != 32 or any(c not in "0123456789abcdef" for c in i)
                                                    for i in wanted):
        raise ApiError(400, "gecersiz_sys_id", "Kimlikler 32 haneli küçük harf onaltılık olmalı.")
    ctx.require("cmdb_ci", "read")
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT r.sys_id, r.ci_sys_id, ci.sys_class_name, r.decision, r.status, r.result_note
                         FROM ingest.attestation_request r JOIN cmdb.cmdb_ci ci ON ci.sys_id = r.ci_sys_id
                        WHERE r.sys_id = ANY(%s::bpchar[])""", (wanted,))
        rows = [{"id": i, "ci": ci, "decision": d, "status": s, "status_label": REQUEST_STATUS[s], "result_note": n}
                for i, ci, cls, d, s, n in cur.fetchall() if ctx.grant(cls, "read").allowed]
    return {"requests": rows, "done": all(r["status"] != "pending" for r in rows)}
