"""Elle girdi (U6, ADR-0026): arayüzden CI oluşturma isteği.

API CI açmaz (kural 1): isteği ``ingest.manual_create``'e yazar, uzlaştırıcı kimlik çözümlemesinden geçirip uygular.
Sonuç: ``created`` (CI açıldı), ``duplicate`` (kesin eşleşme; var olan CI), ``needs_decision`` (belirsiz eşleşme;
adaylar, gerekçeli ``force_new`` ile yeniden istenebilir), ``rejected``.

Alan adları yalnızca sözlükten çözülür ve elle düzenlemeyle aynı politikayla doğrulanır (``EditPolicy``); ``name``
zorunludur. Sınıfta ``create`` izni gerekir (sınıf kalıtımıyla); kök sınıfta CI açılmaz.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1", tags=["manual"])

CREATE_STATUS = {"pending": "Uygulanmayı bekliyor", "created": "CI açıldı", "duplicate": "Zaten var",
                 "needs_decision": "Karar gerekiyor", "rejected": "Reddedildi"}
_IDENT = r"^[a-z_][a-z0-9_]{0,79}$"


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sys_class_name: str = Field(pattern=_IDENT)
    attributes: dict[str, Any] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=2000)
    force_new: bool = False
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")

    @model_validator(mode="after")
    def _reason_for_force(self) -> CreateRequest:
        if self.force_new and not (self.note or "").strip():
            raise ValueError("Benzer CI varken yeni CI açmak için gerekçe zorunludur")
        return self


def _identity_attributes(ctx: Ctx, cls: str) -> list[str]:
    """Sınıfın (veya en yakın atasının) tanımlama kuralındaki öznitelikler, öncelik sırasıyla."""
    md = ctx.state.metadata
    rules = {r["applies_to"]: r for r in md.identification}
    for anc in md.hierarchy.ancestors(cls):
        rule = rules.get(anc)
        if rule:
            seen: list[str] = []
            for entry in sorted(rule.get("entries") or [], key=lambda e: e.get("priority", 0)):
                for a in entry.get("attributes") or []:
                    if a not in seen and a != "serial_number_type":
                        seen.append(a)
            return seen
    return []


def _creatable(ctx: Ctx, cls: str) -> bool:
    md = ctx.state.metadata
    return cls in md.hierarchy and cls != "cmdb_ci" and ctx.grant(cls, "create").allowed


@router.get("/meta/creatable-classes")
def creatable_classes(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Kişinin elle CI açabileceği sınıflar (kök hariç), Türkçe ada göre."""
    md = ctx.state.metadata
    pres = ctx.state.presenter
    items = [{"name": c["name"], "label_tr": pres.class_label(c["name"]), "parent": c.get("extends")}
             for c in md.classes if _creatable(ctx, c["name"])]
    items.sort(key=lambda x: x["label_tr"].casefold())
    return {"items": items}


@router.get("/meta/classes/{name}/form")
def create_form(name: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Elle oluşturma formu: sınıfın elle yazılabilen alanları, zorunlu alan ve kimlik öznitelikleri."""
    md = ctx.state.metadata
    if name not in md.hierarchy:
        raise ApiError(404, "bulunamadi", "Sınıf bulunamadı.")
    if not _creatable(ctx, name):
        ctx.require(name, "create")  # reddi denetim izine yazar
        raise ApiError(422, "kok_sinif", "Kök sınıfta CI açılamaz; daha özel bir sınıf seçin.")
    grant = ctx.grant(name, "create")
    fields = ctx.state.edit_policy.fields(name, grant)
    identity = _identity_attributes(ctx, name)
    return {"class": name, "label_tr": ctx.state.presenter.class_label(name), "required": ["name"],
            "identity": [a for a in identity if a in fields],
            "fields": [f.as_json() for f in fields.values()]}


@router.post("/ci", dependencies=[Depends(require_csrf)], status_code=202)
def create_ci(body: CreateRequest, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Elle CI oluşturma isteği (202: uzlaştırıcı kimlik çözümlemesinden geçirip uygular)."""
    md = ctx.state.metadata
    cls = body.sys_class_name
    if cls not in md.hierarchy:
        raise ApiError(422, "sinif_yok", "Sözlükte böyle bir CMDB sınıfı yok.")
    grant = ctx.require(cls, "create")
    if cls == "cmdb_ci":
        raise ApiError(422, "kok_sinif", "Kök sınıfta CI açılamaz; daha özel bir sınıf seçin.")
    values = {k: v for k, v in ctx.state.edit_policy.validate(ctx.conn, cls, grant, body.attributes).items()
              if v is not None}
    if not str(values.get("name") or "").strip():
        raise ApiError(422, "ad_zorunlu", "Ad (name) zorunludur.", [{"alan": "name", "hata": "Zorunlu"}])
    p = ctx.principal
    note = (body.note or "").strip() or None
    digest = request_hash(cls, "create", values, note, body.force_new)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""INSERT INTO ingest.manual_create (sys_class_name, attributes, force_new, note, requested_by)
                       VALUES (%s, %s, %s, %s, %s) RETURNING sys_id""",
                    (cls, Jsonb(values), body.force_new, note, p.audit_id))
        req_id = cur.fetchone()[0]  # type: ignore[index]
        detail = {"id": req_id, "class": cls, "attributes": sorted(values), "force_new": body.force_new}
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'ci_create', %s, 'allow', %s)", (p.audit_id, f"manual_create:{req_id}", Jsonb(detail)))
        response = {"id": req_id, "status": "pending", "status_label": CREATE_STATUS["pending"], "class": cls,
                    "attributes": sorted(values), "force_new": body.force_new, "replayed": False,
                    "message": "Oluşturma isteği kaydedildi; uzlaştırıcı kimlik çözümlemesinden geçirip uygular."}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/ci", digest, 202, response)
    return JSONResponse(response, status_code=202)


@router.get("/ci-requests/{req_id}")
def create_status(req_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Oluşturma isteğinin durumu ve sonucu (sınıfta okuma izni gerekir)."""
    if len(req_id) != 32 or any(ch not in "0123456789abcdef" for ch in req_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT sys_class_name, attributes, force_new, note, requested_by, requested_at, status,
                              result_ci, result, result_note, applied_at
                         FROM ingest.manual_create WHERE sys_id = %s""", (req_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "İstek bulunamadı.")
        cls, attrs, force, note, by, at, status, ci, result, result_note, applied = row
        ctx.require(cls, "read")
        names: dict[str, str] = {}
        ids = [c["sys_id"] for c in (result or {}).get("candidates") or []] + ([ci] if ci else [])
        if ids:
            cur.execute("SELECT sys_id, name FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])", (ids,))
            names = dict(cur.fetchall())
    pres = ctx.state.presenter
    candidates = [{**c, "name": names.get(c["sys_id"], c.get("name")), "class_label": pres.class_label(c["class"])}
                  for c in (result or {}).get("candidates") or [] if ctx.grant(c["class"], "read").allowed]
    return {"id": req_id, "class": cls, "class_label": pres.class_label(cls), "name": attrs.get("name"),
            "attributes": sorted(attrs), "force_new": force, "note": note, "requested_by": by,
            "requested_at": at.isoformat(), "status": status, "status_label": CREATE_STATUS[status],
            "result_ci": ci, "result_ci_name": names.get(ci) if ci else None, "result_note": result_note,
            "outcome": (result or {}).get("outcome"), "candidates": candidates,
            "applied_at": applied.isoformat() if applied else None}
