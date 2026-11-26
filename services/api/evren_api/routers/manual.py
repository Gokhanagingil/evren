"""Elle girdi (U6, ADR-0026): arayüzden CI oluşturma, ilişki ekleme/kaldırma ve CSV ile toplu içe aktarma istekleri.

API CI açmaz (kural 1): isteği ``ingest.manual_create``'e yazar, uzlaştırıcı kimlik çözümlemesinden geçirip uygular.
Sonuç: ``created`` (CI açıldı), ``duplicate`` (kesin eşleşme; var olan CI), ``needs_decision`` (belirsiz eşleşme;
adaylar, gerekçeli ``force_new`` ile yeniden istenebilir), ``rejected``.

Alan adları yalnızca sözlükten çözülür ve elle düzenlemeyle aynı politikayla doğrulanır (``EditPolicy``); ``name``
zorunludur. Sınıfta ``create`` izni gerekir (sınıf kalıtımıyla); kök sınıfta CI açılmaz.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash
from ..importing import MAX_CHARS, parse_import

router = APIRouter(prefix="/api/v1", tags=["manual"])

CREATE_STATUS = {"pending": "Uygulanmayı bekliyor", "created": "CI açıldı", "duplicate": "Zaten var",
                 "needs_decision": "Karar gerekiyor", "rejected": "Reddedildi"}
RELATION_STATUS = {"pending": "Uygulanmayı bekliyor", "applied": "Uygulandı", "rejected": "Reddedildi"}
_IDENT = r"^[a-z_][a-z0-9_]{0,79}$"
_SYSID = r"^[0-9a-f]{32}$"
_KEY = r"^[A-Za-z0-9._:-]{8,128}$"


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


# ---------------------------------------------------------------------------------------------- elle ilişki (U6.2)
class RelationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["add", "remove"]
    type: str = Field(pattern=r"^[^:]{1,80}::[^:]{1,80}$")
    direction: Literal["parent", "child"]  # bu CI'ın ilişkideki rolü
    target: str = Field(pattern=_SYSID)
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(pattern=_KEY)


def _type_labels(ctx: Ctx) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for t in ctx.state.metadata.relationship_types:
        parts = str(t.get("label_tr") or t["name"]).split("::")
        out[t["name"]] = (parts[0], parts[-1])
    return out


def _ci_class(ctx: Ctx, sys_id: str) -> str:
    if len(sys_id) != 32 or any(ch not in "0123456789abcdef" for ch in sys_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    row = ctx.conn.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s", (sys_id,)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "CI bulunamadı.")
    return str(row[0])


def manual_relation_keys(conn: Any, ci: str) -> set[tuple[str, str, str]]:
    """CI'ın elle girilmiş ilişkileri (tip, bu CI'ın yönü, karşı CI); iki uçtaki Manual kayıtlardan."""
    out: set[tuple[str, str, str]] = set()
    rows = conn.execute("""SELECT source_key, relations FROM ingest.source_record
                            WHERE data_source = 'Manual'
                              AND (source_key = %s OR relations @> %s::jsonb)""",
                        (ci, Jsonb([{"target_source_key": ci}]))).fetchall()
    for holder, relations in rows:
        for r in relations or []:
            if holder == ci:
                out.add((r["type"], r["direction"], r["target_source_key"]))
            elif r.get("target_source_key") == ci:
                out.add((r["type"], "child" if r["direction"] == "parent" else "parent", holder))
    return out


@router.get("/ci/{sys_id}/relation-options")
def relation_options(sys_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Bu CI için izinli ilişkiler (ilişki kuralları, ADR-0019; sınıf kalıtımıyla) ve kişinin ekleme/kaldırma izni."""
    cls = _ci_class(ctx, sys_id)
    ctx.require(cls, "read")
    md = ctx.state.metadata
    pres = ctx.state.presenter
    labels = _type_labels(ctx)
    seen: set[tuple[str, str, str]] = set()
    options = []
    for o in md.relations.suggestions(cls):
        key = (o["direction"], o["type"], o["other_class"])
        if key in seen or o["type"] not in labels:
            continue
        seen.add(key)
        verb = labels[o["type"]][0 if o["direction"] == "parent" else 1]
        options.append({"direction": o["direction"], "type": o["type"], "label": verb,
                        "other_class": o["other_class"], "other_class_label": pres.class_label(o["other_class"]),
                        "description": o["rule"].get("description")})
    options.sort(key=lambda x: (x["label"].casefold(), x["other_class_label"].casefold()))
    return {"can_add": ctx.grant("cmdb_rel_ci", "create").allowed,
            "can_remove": ctx.grant("cmdb_rel_ci", "delete").allowed, "options": options}


@router.post("/ci/{sys_id}/relations", dependencies=[Depends(require_csrf)], status_code=202)
def relation_request(sys_id: str, body: RelationRequest, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Elle ilişki ekleme/kaldırma isteği (202: uzlaştırıcı uygular). Ekleme izinli ilişki kuralına uymalı; kaldırma
    yalnızca elle girilmiş ilişkiye uygulanır."""
    md = ctx.state.metadata
    cls = _ci_class(ctx, sys_id)
    if body.target == sys_id:
        raise ApiError(422, "ayni_ci", "CI kendisiyle ilişkilendirilemez.")
    other = _ci_class(ctx, body.target)
    ctx.require(cls, "read")
    ctx.require(other, "read")
    if body.type not in _type_labels(ctx):
        raise ApiError(422, "iliski_tipi_yok", "Sözlükte böyle bir ilişki tipi yok.")
    parent_cls, child_cls = (cls, other) if body.direction == "parent" else (other, cls)
    existing = manual_relation_keys(ctx.conn, sys_id)
    if body.action == "add":
        ctx.require("cmdb_rel_ci", "create")
        verdict = md.relations.check(parent_cls, body.type, child_cls)
        if not verdict.allowed:
            raise ApiError(422, "iliski_kurali", verdict.message(parent_cls, body.type, child_cls))
        if (body.type, body.direction, body.target) in existing:
            raise ApiError(409, "iliski_var", "Bu ilişki zaten elle girilmiş.")
    else:
        ctx.require("cmdb_rel_ci", "delete")
        if (body.type, body.direction, body.target) not in existing:
            raise ApiError(409, "elle_iliski_degil",
                           "Bu ilişki elle girilmemiş; keşfin bildirdiği ilişki elle kaldırılamaz (kaynağından kalkar).")
    p = ctx.principal
    note = (body.note or "").strip() or None
    digest = request_hash(sys_id, "relation", body.action, body.type, body.direction, body.target, note)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""SELECT 1 FROM ingest.manual_relation WHERE status = 'pending' AND ci_sys_id = %s
                         AND target_sys_id = %s AND type = %s AND direction = %s""",
                    (sys_id, body.target, body.type, body.direction))
        if cur.fetchone():
            raise ApiError(409, "bekleyen_istek", "Bu ilişki için uygulanmayı bekleyen bir istek var.")
        cur.execute("""INSERT INTO ingest.manual_relation (ci_sys_id, target_sys_id, type, direction, action, note,
                                                           requested_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                    (sys_id, body.target, body.type, body.direction, body.action, note, p.audit_id))
        req_id = cur.fetchone()[0]  # type: ignore[index]
        detail = {"id": req_id, "action": body.action, "type": body.type, "direction": body.direction,
                  "target": body.target}
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'ci_relation', %s, 'allow', %s)", (p.audit_id, f"cmdb_ci:{sys_id}", Jsonb(detail)))
        response = {**detail, "status": "pending", "status_label": RELATION_STATUS["pending"], "replayed": False,
                    "message": "İlişki isteği kaydedildi; uzlaştırıcı uyguladığında ilişki "
                               + ("eklenir." if body.action == "add" else "kaldırılır.")}
        remember(cur, p.audit_id, body.idempotency_key, f"/api/v1/ci/{sys_id}/relations", digest, 202, response)
    return JSONResponse(response, status_code=202)


@router.get("/relation-requests/{req_id}")
def relation_status(req_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    if len(req_id) != 32 or any(ch not in "0123456789abcdef" for ch in req_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    row = ctx.conn.execute("""SELECT r.ci_sys_id, ci.sys_class_name, r.target_sys_id, r.type, r.direction, r.action,
                                     r.status, r.result_note, r.requested_by, r.requested_at, r.applied_at
                                FROM ingest.manual_relation r JOIN cmdb.cmdb_ci ci ON ci.sys_id = r.ci_sys_id
                               WHERE r.sys_id = %s""", (req_id,)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "İstek bulunamadı.")
    ci, cls, target, rel_type, direction, action, status, note, by, at, applied = row
    ctx.require(cls, "read")
    return {"id": req_id, "ci_sys_id": ci, "target": target, "type": rel_type, "direction": direction,
            "action": action, "status": status, "status_label": RELATION_STATUS[status], "result_note": note,
            "requested_by": by, "requested_at": at.isoformat(), "applied_at": applied.isoformat() if applied else None}


# ---------------------------------------------------------------------------------------------- CSV içe aktarma (U6.3)
class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sys_class_name: str = Field(pattern=_IDENT)  # sys_class_name sütunu olmayan satırların sınıfı
    csv: str = Field(min_length=1, max_length=MAX_CHARS)
    file_name: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, pattern=_KEY)  # onayda zorunlu


def _parse(ctx: Ctx, body: ImportBody) -> Any:
    md = ctx.state.metadata
    if body.sys_class_name not in md.hierarchy or body.sys_class_name == "cmdb_ci":
        raise ApiError(422, "sinif_yok", "Varsayılan sınıf sözlükte tanımlı, kök dışı bir CMDB sınıfı olmalı.")
    ctx.require("cmdb_ci", "create")
    return parse_import(ctx.conn, md, ctx.state.edit_policy, body.csv, body.sys_class_name,
                        lambda c: ctx.grant(c, "create"))


@router.post("/imports/preview", dependencies=[Depends(require_csrf)])
def import_preview(body: ImportBody, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """CSV önizlemesi: ayraç, sütun eşlemesi, satır başına doğrulama. Hiçbir şey yazmaz."""
    doc = _parse(ctx, body).as_json()
    pres = ctx.state.presenter
    for row in doc["rows"]:
        row["class_label"] = pres.class_label(row["class"]) if row["class"] in ctx.state.metadata.hierarchy \
            else row["class"]
    return doc


@router.post("/imports", dependencies=[Depends(require_csrf)], status_code=202)
def import_commit(body: ImportBody, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Onay: dosya yeniden doğrulanır (önizlemeye güvenilmez); geçerli satırlar toplu iş altında satır başına oluşturma
    isteği olur, hatalı satırlar atlanır ve sayısı kaydedilir. Uzlaştırıcı satırları sırasıyla uygular."""
    if not body.idempotency_key:
        raise ApiError(422, "idempotency_key_yok", "Onay isteği idempotency_key ister.")
    preview = _parse(ctx, body)
    valid = preview.valid
    if not valid:
        raise ApiError(422, "gecerli_satir_yok", "İçe aktarılabilecek geçerli satır yok; önizlemedeki hataları düzeltin.")
    p = ctx.principal
    note = (body.note or "").strip() or None
    digest = request_hash("import", body.sys_class_name, body.csv, note)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""INSERT INTO ingest.import_batch (file_name, default_class, row_count, skipped_count, note,
                                                        requested_by)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                    (body.file_name, body.sys_class_name, len(valid), len(preview.rows) - len(valid), note,
                     p.audit_id))
        batch = cur.fetchone()[0]  # type: ignore[index]
        cur.executemany("""INSERT INTO ingest.manual_create (sys_class_name, attributes, note, requested_by, batch_id,
                                                             row_no)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        [(r.cls, Jsonb(r.values), note, p.audit_id, batch, r.row_no) for r in valid])
        detail = {"id": batch, "file_name": body.file_name, "rows": len(valid),
                  "skipped": len(preview.rows) - len(valid)}
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'ci_import', %s, 'allow', %s)", (p.audit_id, f"import_batch:{batch}", Jsonb(detail)))
        response = {"id": batch, "row_count": len(valid), "skipped_count": len(preview.rows) - len(valid),
                    "status": "pending", "replayed": False,
                    "message": f"{len(valid)} satır içe aktarma kuyruğuna alındı; uzlaştırıcı her satırı kimlik "
                               "çözümlemesinden geçirir."}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/imports", digest, 202, response)
    return JSONResponse(response, status_code=202)


@router.get("/imports/{batch_id}")
def import_status(batch_id: str, ctx: Ctx = Depends(context),
                  status: Literal["pending", "created", "duplicate", "needs_decision", "rejected"] | None = None,
                  limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
    """Toplu işin özeti (duruma göre satır sayıları) ve satır sonuçları (isteğe bağlı durum süzgeciyle)."""
    if len(batch_id) != 32 or any(ch not in "0123456789abcdef" for ch in batch_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    ctx.require("cmdb_ci", "read")
    pres = ctx.state.presenter
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT file_name, default_class, row_count, skipped_count, note, requested_by, requested_at
                         FROM ingest.import_batch WHERE sys_id = %s""", (batch_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "İçe aktarma bulunamadı.")
        file_name, default_class, row_count, skipped, note, by, at = row
        cur.execute("SELECT status, count(*) FROM ingest.manual_create WHERE batch_id = %s GROUP BY 1", (batch_id,))
        counts = {k: 0 for k in CREATE_STATUS} | dict(cur.fetchall())
        cur.execute("""SELECT m.row_no, m.attributes->>'name', m.sys_class_name, m.status, m.result_ci, c.name,
                              c.sys_class_name, m.result_note, m.result->'candidates'
                         FROM ingest.manual_create m LEFT JOIN cmdb.cmdb_ci c ON c.sys_id = m.result_ci
                        WHERE m.batch_id = %s AND (%s::text IS NULL OR m.status = %s)
                        ORDER BY m.row_no LIMIT %s""", (batch_id, status, status, limit))
        fetched = cur.fetchall()
    readable: dict[str, bool] = {}

    def can_read(cls: str | None) -> bool:
        if cls is None:
            return False
        if cls not in readable:
            readable[cls] = cls in ctx.state.metadata.hierarchy and ctx.grant(cls, "read").allowed
        return readable[cls]

    rows = []
    for n, name, cls, st, ci, ci_name, ci_cls, rn, cands in fetched:
        if not can_read(cls):  # okuyamadığı sınıfın satırı: yalnızca numara ve durum
            rows.append({"row_no": n, "name": None, "class": None, "class_label": None, "status": st,
                         "status_label": CREATE_STATUS[st], "result_ci": None, "result_ci_name": None,
                         "result_note": None, "candidates": []})
            continue
        shown = ci if ci and can_read(ci_cls) else None
        rows.append({"row_no": n, "name": name, "class": cls, "class_label": pres.class_label(cls), "status": st,
                     "status_label": CREATE_STATUS[st], "result_ci": shown, "result_ci_name": ci_name if shown else None,
                     "result_note": rn,
                     "candidates": [{"sys_id": c["sys_id"], "name": c.get("name"), "probability": c.get("probability")}
                                    for c in (cands or [])[:5] if can_read(c.get("class"))]})
    return {"id": batch_id, "file_name": file_name, "default_class": default_class, "row_count": row_count,
            "skipped_count": skipped, "note": note, "requested_by": by, "requested_at": at.isoformat(),
            "counts": counts, "done": counts["pending"] == 0, "rows": rows}
