"""Metadata değişiklik setleri (F1-11, ADR-0015): taslak → gönderildi → onaylandı → yayınlandı.

Yetki: hazırlama ``schema/schema_draft`` (Şema tasarımcısı), onay/ret/yayın ``schema/schema_approve`` (Şema
onaylayıcı). Hazırlayan onaylayamaz ve reddedemez (API + veritabanı kısıtı + tetikleyici, göç 0011).

Doğrulama her geçişte, çalışan metadata'nın ÜZERİNE yayınlanmış ama henüz dağıtılmamış setler uygulanarak yapılır;
böylece çakışan iki set aynı anda yayınlanamaz. Yayın, ``metadata/changes/`` altına PR ile eklenecek değişiklik
dosyasını üretir; dağıtımda ``load_metadata`` uygular (YAML doğruluk kaynağı kalır, kural 6).
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from evren_core.changeset import (
    FIELD_TYPES,
    LIFECYCLE_TR,
    OP_TR,
    OPERATIONS,
    SENSITIVITIES,
    TRANSITIONS,
    Change,
    ChangeError,
    apply_changes,
    describe,
    parse_changes,
    render_change_file,
)
from evren_core.normalize import ascii_fold

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1/schema", tags=["şema"])

STATE_TR = {"draft": "Taslak", "in_review": "Onay bekliyor", "approved": "Onaylandı", "published": "Yayınlandı",
            "rejected": "Reddedildi"}
_ID = re.compile(r"^[0-9a-f]{32}$")


class ChangeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str = Field(max_length=40)
    table: str = Field(min_length=1, max_length=80)
    element: str | None = Field(default=None, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


class ChangeSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    changes: list[ChangeItem] = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)


def _valid_id(set_id: str) -> None:
    if not _ID.match(set_id):
        raise ApiError(400, "gecersiz_kimlik", "Değişiklik seti kimliği 32 haneli küçük harf onaltılık olmalı.")


def _changes_of(cur: Any, set_id: str) -> list[Change]:
    cur.execute("""SELECT operation, target_table, target_element, payload FROM meta.schema_change
                    WHERE change_set = %s ORDER BY sequence""", (set_id,))
    return [Change(op, t, e, p) for op, t, e, p in cur.fetchall()]


def _effective_metadata(ctx: Ctx, cur: Any, *, exclude: str | None = None) -> Any:
    """Çalışan metadata + yayınlanmış ama bu sürümde henüz uygulanmamış setler (yayın sırasıyla)."""
    md = ctx.state.metadata
    applied = ctx.state.applied_change_files
    cur.execute("""SELECT sys_id, artifact_name FROM meta.schema_change_set
                    WHERE state = 'published' AND sys_id IS DISTINCT FROM %s ORDER BY published_on, sys_id""", (exclude,))
    for sid, name in cur.fetchall():
        if name not in applied:
            md = apply_changes(md, _changes_of(cur, sid))
    return md


def _validate(ctx: Ctx, cur: Any, changes: list[Change], *, exclude: str | None = None) -> tuple[Any, dict[str, Any]]:
    try:
        base = _effective_metadata(ctx, cur, exclude=exclude)
    except ChangeError as exc:  # pragma: no cover - yayınlanmış setler birbirini bozamaz (her yayında doğrulanır)
        return None, {"ok": False, "errors": [f"Bekleyen yayın uygulanamadı: {e}" for e in exc.errors]}
    try:
        result = apply_changes(base, changes)
    except ChangeError as exc:
        return None, {"ok": False, "errors": exc.errors, "checked_at": datetime.now(UTC).isoformat()}
    return result, {"ok": True, "errors": [], "checked_at": datetime.now(UTC).isoformat()}


def _impact(ctx: Ctx, cur: Any, changes: list[Change]) -> list[dict[str, Any]]:
    """İşlem başına etki: kaç CI, hangi kaynak, sahipsiz kalabilecek öznitelik (yaklaşık, yayın öncesi)."""
    md = ctx.state.metadata
    out: list[dict[str, Any]] = []
    for c in changes:
        item: dict[str, Any] = {"change": describe(c), "notes": []}
        if c.op in ("add_field", "modify_field", "deprecate_field", "add_choice") and c.table in md.class_map:
            classes = [n for n in md.class_map if c.table in md.hierarchy.ancestors(n)]
            cur.execute("SELECT count(*) FROM cmdb.cmdb_ci WHERE sys_class_name = ANY(%s)", (classes,))
            item["ci_count"] = cur.fetchone()[0]
            if c.op == "add_field":
                item["notes"].append("Yeni JSONB alanı: DDL yok, mevcut CI'lar değişmez; değer kaynak eşlemesine "
                                     "veya elle girişe eklenince dolar.")
            if c.op == "deprecate_field" and c.element:
                cur.execute("SELECT count(*) FROM cmdb.cmdb_ci WHERE sys_class_name = ANY(%s) AND class_attributes ? %s",
                            (classes, c.element))
                item["with_value"] = cur.fetchone()[0]
                item["notes"].append(f"{item['with_value']} CI'da değer var; değerler kalır, alan düzenlenemez olur.")
            if c.op == "modify_field" and "sensitivity" in c.payload:
                item["notes"].append("Hassasiyet değişir: okuma izni bu düzeyin altında kalan roller alanı göremez.")
        elif c.op in ("set_source_lifecycle", "add_data_source"):
            item["notes"].append("Gölge kaynak değer yazmaz ve CI açmaz; kapsam ve uyum Gölge Mod Raporu'nda ölçülür."
                                 if c.op == "add_data_source" else
                                 f"Dağıtımdan sonra: python -m evren_reconciler.worker --rereconcile {c.element}")
            if c.op == "set_source_lifecycle" and c.element:
                cur.execute("SELECT count(DISTINCT ci_sys_id) FROM ingest.identity_link WHERE data_source = %s AND active",
                            (c.element,))
                item["ci_count"] = cur.fetchone()[0]
                if c.payload.get("lifecycle") in ("retiring", "retired"):
                    cur.execute("""SELECT count(*) FROM cmdb.ci_reconciliation_state s, jsonb_each(s.winners) w
                                    WHERE w.value->>'source' = %s""", (c.element,))
                    item["sole_winner_attributes"] = cur.fetchone()[0]
                    item["notes"].append(f"{item['sole_winner_attributes']} öznitelik değerini bu kaynak veriyor; "
                                         "emeklilikte başka kaynak vermiyorsa sahipsiz kalır (önce tatbikat/önizleme).")
        out.append(item)
    return out


def _caps(ctx: Ctx, row: dict[str, Any]) -> dict[str, bool]:
    me = ctx.principal.audit_id
    can_draft = ctx.grant("schema", "schema_draft").allowed
    can_approve = ctx.grant("schema", "schema_approve").allowed
    mine = row["created_by"] == me
    return {"can_submit": can_draft and mine and row["state"] == "draft",
            "can_approve": can_approve and not mine and row["state"] == "in_review",
            "can_reject": can_approve and not mine and row["state"] in ("draft", "in_review", "approved"),
            "can_publish": can_approve and not mine and row["state"] == "approved"}


def _row(cur: Any, set_id: str) -> dict[str, Any]:
    cur.execute("""SELECT sys_id, name, description, state, created_by, created_on, submitted_on, approved_by,
                          approved_on, rejected_by, rejected_on, decision_note, published_by, published_on, validation,
                          impact, artifact_name, artifact_sha256
                     FROM meta.schema_change_set WHERE sys_id = %s""", (set_id,))
    r = cur.fetchone()
    if r is None:
        raise ApiError(404, "bulunamadi", "Değişiklik seti bulunamadı.")
    keys = ("id", "name", "description", "state", "created_by", "created_on", "submitted_on", "approved_by",
            "approved_on", "rejected_by", "rejected_on", "decision_note", "published_by", "published_on", "validation",
            "impact", "artifact_name", "artifact_sha256")
    return dict(zip(keys, r, strict=True))


def _present(ctx: Ctx, cur: Any, row: dict[str, Any]) -> dict[str, Any]:
    changes = _changes_of(cur, row["id"])
    out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
    return {**out, "state_label": STATE_TR[row["state"]],
            "changes": [{**c.as_dict(), "label": OP_TR.get(c.op, c.op), "description": describe(c)} for c in changes],
            **_caps(ctx, row)}


def _event(ctx: Ctx, cur: Any, action: str, set_id: str, detail: dict[str, Any]) -> None:
    cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                "VALUES (%s, %s, %s, 'allow', %s)",
                (ctx.principal.audit_id, action, f"schema_change_set:{set_id}", Jsonb(detail)))


@router.get("/catalog")
def catalog(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Değişiklik seti formu için sözlük: sınıflar (kendi alanlarıyla), referans tabloları, roller, kaynaklar."""
    ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    classes = []
    for c in md.classes:
        fields = [{"name": f["name"], "label_tr": f.get("label_tr", f["name"]), "type": f.get("type"),
                   "storage": f.get("storage", "jsonb"), "sensitivity": f.get("sensitivity", "internal"),
                   "choice": bool(f.get("choice")), "deprecated": bool(f.get("deprecated")),
                   "max_length": f.get("max_length")} for f in c.get("fields") or []]
        classes.append({"name": c["name"], "label_tr": c.get("label_tr", c["name"]), "parent": c.get("extends"),
                        "fields": fields})
    reg = md.sources
    return {
        "classes": sorted(classes, key=lambda c: ascii_fold(c["label_tr"])),
        "reference_tables": [{"name": t["name"], "label_tr": t.get("label_tr", t["name"])} for t in md.reference_tables],
        "roles": [{"name": r["name"], "label_tr": r["label_tr"]} for r in md.source_roles],
        "sources": [{"name": s.name, "label_tr": s.label_tr, "role": s.role, "lifecycle": s.lifecycle,
                     "lifecycle_label": LIFECYCLE_TR[s.lifecycle], "role_rank": s.role_rank,
                     "transitions": [{"value": t, "label": LIFECYCLE_TR[t]} for t in TRANSITIONS[s.lifecycle]]}
                    for s in sorted(reg.sources.values(), key=lambda s: (s.role, s.role_rank, s.name))],
        "operations": [{"op": op, "label": OP_TR[op]} for op in OPERATIONS],
        "field_types": list(FIELD_TYPES), "sensitivities": list(SENSITIVITIES),
        "can_draft": ctx.grant("schema", "schema_draft").allowed,
        "can_approve": ctx.grant("schema", "schema_approve").allowed,
        "applied_change_files": sorted(ctx.state.applied_change_files),
    }


@router.get("/change-sets")
def list_sets(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT s.sys_id, s.name, s.state, s.created_by, s.created_on, s.approved_by, s.published_on,
                              (SELECT count(*) FROM meta.schema_change c WHERE c.change_set = s.sys_id), s.artifact_name
                         FROM meta.schema_change_set s ORDER BY s.created_on DESC, s.sys_id LIMIT 200""")
        items = [{"id": i, "name": n, "state": st, "state_label": STATE_TR[st], "created_by": by,
                  "created_on": on.isoformat(), "approved_by": ab, "published_on": po.isoformat() if po else None,
                  "change_count": cnt, "artifact_name": an}
                 for i, n, st, by, on, ab, po, cnt, an in cur.fetchall()]
    return {"items": items, "can_draft": ctx.grant("schema", "schema_draft").allowed,
            "can_approve": ctx.grant("schema", "schema_approve").allowed}


@router.get("/change-sets/{set_id}")
def get_set(set_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    _valid_id(set_id)
    with ctx.conn.cursor() as cur:
        return _present(ctx, cur, _row(cur, set_id))


@router.post("/change-sets", dependencies=[Depends(require_csrf)], status_code=201)
def create_set(body: ChangeSetCreate, ctx: Ctx = Depends(context)) -> JSONResponse:
    ctx.require("schema", "schema_draft")
    p = ctx.principal
    try:
        changes = parse_changes([c.model_dump() for c in body.changes])
    except ChangeError as exc:
        raise ApiError(422, "gecersiz_degisiklik", "Değişiklik seti doğrulanamadı.", [{"hata": e} for e in exc.errors]) from None
    digest = request_hash("change-set", body.model_dump(exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        _result, validation = _validate(ctx, cur, changes)
        if not validation["ok"]:
            raise ApiError(422, "gecersiz_degisiklik", "Değişiklik seti doğrulanamadı.",
                           [{"hata": e} for e in validation["errors"]])
        cur.execute("""INSERT INTO meta.schema_change_set (name, description, created_by, validation)
                       VALUES (%s, %s, %s, %s) RETURNING sys_id""",
                    (body.name.strip(), body.description, p.audit_id, Jsonb(validation)))
        set_id = cur.fetchone()[0]  # type: ignore[index]
        for seq, c in enumerate(changes, 1):
            cur.execute("""INSERT INTO meta.schema_change (change_set, sequence, operation, target_table, target_element,
                                                          payload) VALUES (%s, %s, %s, %s, %s, %s)""",
                        (set_id, seq, c.op, c.table, c.element, Jsonb(dict(c.payload))))
        _event(ctx, cur, "schema_draft", set_id, {"changes": len(changes)})
        response = {"id": set_id, "state": "draft", "state_label": STATE_TR["draft"], "replayed": False,
                    "message": "Taslak kaydedildi. Onaya göndermek için 'Onaya gönder'."}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/schema/change-sets", digest, 201, response)
    return JSONResponse(response, status_code=201)


def _transition(ctx: Ctx, set_id: str, action: str, body: Decision | None = None) -> dict[str, Any]:
    _valid_id(set_id)
    me = ctx.principal.audit_id
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("SELECT 1 FROM meta.schema_change_set WHERE sys_id = %s FOR UPDATE", (set_id,))
        row = _row(cur, set_id)
        changes = _changes_of(cur, set_id)
        if action in ("approve", "reject", "publish") and row["created_by"] == me:
            raise ApiError(403, "hazirlayan_onaylayamaz", "Değişiklik setini hazırlayan kişi onaylayamaz, reddedemez veya "
                                                           "yayınlayamaz (görevler ayrılığı).")
        if action == "submit":
            if row["created_by"] != me:
                raise ApiError(403, "yalniz_hazirlayan", "Seti yalnızca hazırlayan onaya gönderebilir.")
            if row["state"] != "draft":
                raise ApiError(409, "durum_uygun_degil", "Yalnızca taslak onaya gönderilebilir.", [{"durum": row["state"]}])
            _r, validation = _validate(ctx, cur, changes)
            if not validation["ok"]:
                raise ApiError(422, "gecersiz_degisiklik", "Değişiklik seti güncel metadata ile doğrulanamadı.",
                               [{"hata": e} for e in validation["errors"]])
            cur.execute("""UPDATE meta.schema_change_set SET state = 'in_review', submitted_on = now(), validation = %s,
                                  impact = %s WHERE sys_id = %s""",
                        (Jsonb(validation), Jsonb(_impact(ctx, cur, changes)), set_id))
        elif action == "approve":
            if row["state"] != "in_review":
                raise ApiError(409, "durum_uygun_degil", "Yalnızca onay bekleyen set onaylanabilir.", [{"durum": row["state"]}])
            _r, validation = _validate(ctx, cur, changes)
            if not validation["ok"]:
                raise ApiError(422, "gecersiz_degisiklik", "Değişiklik seti güncel metadata ile artık uyumsuz; reddedin.",
                               [{"hata": e} for e in validation["errors"]])
            cur.execute("""UPDATE meta.schema_change_set SET state = 'approved', approved_by = %s, approved_on = now(),
                                  decision_note = %s, validation = %s WHERE sys_id = %s""",
                        (me, body.note if body else None, Jsonb(validation), set_id))
        elif action == "reject":
            if row["state"] not in ("draft", "in_review", "approved"):
                raise ApiError(409, "durum_uygun_degil", "Bu set reddedilemez.", [{"durum": row["state"]}])
            cur.execute("""UPDATE meta.schema_change_set SET state = 'rejected', rejected_by = %s, rejected_on = now(),
                                  decision_note = %s WHERE sys_id = %s""", (me, body.note if body else None, set_id))
        elif action == "publish":
            if row["state"] != "approved":
                raise ApiError(409, "durum_uygun_degil", "Yalnızca onaylanmış set yayınlanabilir.", [{"durum": row["state"]}])
            _r, validation = _validate(ctx, cur, changes, exclude=set_id)
            if not validation["ok"]:
                raise ApiError(422, "gecersiz_degisiklik", "Değişiklik seti güncel metadata ile artık uyumsuz; reddedin.",
                               [{"hata": e} for e in validation["errors"]])
            now = datetime.now(UTC)
            slug = re.sub(r"[^a-z0-9]+", "-", ascii_fold(row["name"]).lower()).strip("-")[:40] or "degisiklik"
            name = f"{now:%Y%m%d%H%M}-{set_id[:8]}-{slug}.yaml"
            text = render_change_file({"id": set_id, "name": row["name"], "description": row["description"],
                                       "prepared_by": row["created_by"], "approved_by": row["approved_by"],
                                       "approved_on": row["approved_on"].isoformat() if row["approved_on"] else None,
                                       "published_on": now.isoformat()}, changes)
            sha = hashlib.sha256(text.encode()).hexdigest()
            cur.execute("""UPDATE meta.schema_change_set SET state = 'published', published_by = %s, published_on = %s,
                                  artifact_name = %s, artifact = %s, artifact_sha256 = %s WHERE sys_id = %s""",
                        (me, now, name, text, sha, set_id))
        _event(ctx, cur, f"schema_{action}", set_id, {"note": bool(body and body.note)})
        result = _present(ctx, cur, _row(cur, set_id))
    messages = {"submit": "Onaya gönderildi.", "approve": "Onaylandı. Yayınlamak için 'Yayınla'.",
                "reject": "Reddedildi.",
                "publish": "Yayınlandı. Değişiklik dosyasını metadata/changes/ altına PR ile ekleyin; dağıtımda "
                           "load_metadata uygular."}
    return {**result, "message": messages[action]}


@router.post("/change-sets/{set_id}/submit", dependencies=[Depends(require_csrf)])
def submit_set(set_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("schema", "schema_draft")
    return _transition(ctx, set_id, "submit")


@router.post("/change-sets/{set_id}/approve", dependencies=[Depends(require_csrf)])
def approve_set(set_id: str, body: Decision, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("schema", "schema_approve")
    return _transition(ctx, set_id, "approve", body)


@router.post("/change-sets/{set_id}/reject", dependencies=[Depends(require_csrf)])
def reject_set(set_id: str, body: Decision, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("schema", "schema_approve")
    return _transition(ctx, set_id, "reject", body)


@router.post("/change-sets/{set_id}/publish", dependencies=[Depends(require_csrf)])
def publish_set(set_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("schema", "schema_approve")
    return _transition(ctx, set_id, "publish")


@router.get("/change-sets/{set_id}/artifact")
def artifact(set_id: str, ctx: Ctx = Depends(context)) -> Response:
    ctx.require("cmdb_ci", "read")
    _valid_id(set_id)
    row = ctx.conn.execute("SELECT artifact_name, artifact, artifact_sha256 FROM meta.schema_change_set "
                           "WHERE sys_id = %s AND state = 'published'", (set_id,)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "Yayınlanmış değişiklik seti bulunamadı.")
    name, text, sha = row
    return Response(text.encode(), media_type="application/yaml", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "X-Evren-Content-SHA256": sha})
