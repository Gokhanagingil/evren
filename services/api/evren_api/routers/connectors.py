"""Bağlayıcı yönetimi (U5.3b, ADR-0021): şimdi çalıştır, iptal, bağlayıcı kimliği verme ve iptal.

API bağlayıcıyı çalıştırmaz ve kaynak sırlarını görmez: çalıştırma isteğini ``ingest.connector_run_request``'e yazar,
ayrı çalıştırıcı servisi yürütür. Kimlik verme ve iptal ``SECURITY DEFINER`` işlevleriyledir (göç 0019): düz
belirteç veritabanına gitmez (yalnızca SHA-256 özeti), yanıtta BİR KEZ görünür, tekrar güvenliği kaydına yazılmaz.
Tüm uçlar ``data_source/connector_admin`` izni ister.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evren_core.credentials import new_token, token_hint, token_sha256

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash
from .platform import REQUEST_STATUS

router = APIRouter(prefix="/api/v1", tags=["connectors"])

_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$"
_KEY = r"^[A-Za-z0-9._:-]{8,128}$"


class RunRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance: str = Field(pattern=_NAME)
    mode: Literal["snapshot", "delta"] = "snapshot"
    since: datetime | None = None
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(pattern=_KEY)

    @model_validator(mode="after")
    def _since(self) -> RunRequestBody:
        if self.since is not None and (self.mode != "delta" or self.since.tzinfo is None):
            raise ValueError("since yalnızca delta ile ve saat dilimiyle verilir")
        return self


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=_NAME)
    expires_days: int = Field(default=365, ge=1, le=730)
    per_minute: int = Field(default=60000, ge=1, le=10_000_000)
    idempotency_key: str = Field(pattern=_KEY)


def _source(ctx: Ctx, name: str) -> Any:
    reg = ctx.state.metadata.sources
    if name not in reg.sources:
        raise ApiError(404, "bulunamadi", "Veri kaynağı bulunamadı.")
    return reg.source(name)


@router.post("/sources/{name}/run-requests", dependencies=[Depends(require_csrf)], status_code=202)
def request_run(name: str, body: RunRequestBody, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Şimdi çalıştır: istek yazılır, çalıştırıcı servisi yürütür (202)."""
    ctx.require("data_source", "connector_admin")
    src = _source(ctx, name)
    p = ctx.principal
    digest = request_hash(name, body.model_dump(mode="json", exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        if src.lifecycle == "retired":
            raise ApiError(422, "kaynak_emekli", "Emekliye ayrılmış veri kaynağı çalıştırılamaz.")
        cur.execute("""SELECT i.data_source, i.enabled,
                              coalesce(i.seen_at > now() - 3 * h.poll_seconds * interval '1 second'
                                       AND h.seen_at > now() - 3 * h.poll_seconds * interval '1 second'
                                       AND h.stopped_at IS NULL, false)
                         FROM ingest.connector_instance i
                         LEFT JOIN ingest.worker_heartbeat h ON h.worker_id = i.runner_id
                        WHERE i.name = %s""", (body.instance,))
        inst = cur.fetchone()
        if inst is None or inst[0] != name:
            raise ApiError(422, "ornek_yok", "Bu veri kaynağında böyle bir bağlayıcı örneği yok.")
        if not inst[1]:
            raise ApiError(422, "ornek_devre_disi", "Bağlayıcı örneği çalıştırıcıda devre dışı.")
        if not inst[2]:
            raise ApiError(422, "calistirici_yok", "Bu örneği çalıştıran çalıştırıcı servisi şu an görülmüyor.")
        try:
            with ctx.conn.transaction():
                cur.execute("""INSERT INTO ingest.connector_run_request (instance, data_source, mode, since,
                                                                        requested_by, note)
                               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                            (body.instance, name, body.mode, body.since, p.audit_id, body.note))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "etkin_istek", "Bu örnek için bekleyen veya yürüyen bir çalışma var.") from None
        rid = cur.fetchone()[0]  # type: ignore[index]
        detail = {"id": str(rid), "instance": body.instance, "mode": body.mode,
                  "since": body.since.isoformat() if body.since else None}
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'connector_run_request', %s, 'allow', %s)",
                    (p.audit_id, f"data_source:{name}", Jsonb(detail)))
        response = {"id": str(rid), "status": "pending", "status_label": REQUEST_STATUS["pending"],
                    "instance": body.instance, "data_source": name, "mode": body.mode, "replayed": False,
                    "message": "Çalıştırma isteği kaydedildi; çalıştırıcı servisi birkaç saniye içinde başlatır."}
        remember(cur, p.audit_id, body.idempotency_key, f"/api/v1/sources/{name}/run-requests", digest, 202, response)
    return JSONResponse(response, status_code=202)


@router.post("/run-requests/{request_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel_run(request_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("data_source", "connector_admin")
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise ApiError(404, "bulunamadi", "Çalıştırma isteği bulunamadı.") from None
    status = ctx.conn.execute("SELECT ingest.cancel_connector_run_request(%s, %s)",
                              (rid, ctx.principal.audit_id)).fetchone()[0]  # type: ignore[index]
    if status is None:
        raise ApiError(404, "bulunamadi", "Çalıştırma isteği bulunamadı.")
    if status not in ("cancelled", "running"):
        raise ApiError(409, "sonuclanmis", "İstek zaten sonuçlanmış; iptal edilemez.")
    message = ("İstek iptal edildi." if status == "cancelled"
               else "İptal istendi; çalıştırıcı bağlayıcı sürecini birkaç saniye içinde durdurur.")
    return {"id": str(rid), "status": status, "status_label": REQUEST_STATUS[status], "message": message}


@router.post("/sources/{name}/credentials", dependencies=[Depends(require_csrf)], status_code=201)
def issue_credential(name: str, body: CredentialBody, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Bağlayıcı kimliği ver. Belirteç yalnızca bu yanıtta görünür; tekrarda döndürülmez."""
    ctx.require("data_source", "connector_admin")
    src = _source(ctx, name)
    if not src.connector:
        raise ApiError(422, "baglayicisiz_kaynak", "Bu veri kaynağı bağlayıcıyla beslenmez; kimlik verilmez.")
    p = ctx.principal
    digest = request_hash(name, body.model_dump(exclude={"idempotency_key"}))
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior  # belirteçsiz kayıt: bir kez gösterim ilkesi
        token = new_token()
        try:
            with ctx.conn.transaction():
                cur.execute("SELECT ingest.issue_connector_credential(%s, %s, %s, %s, %s, %s, %s)",
                            (body.name, name, token_sha256(token), token_hint(token), body.per_minute,
                             body.expires_days, p.audit_id))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "kimlik_adi_var", "Bu adla bir kimlik zaten var (iptal edilmiş olsa da); başka ad "
                                                  "seçin.") from None
        except (psycopg.errors.InvalidParameterValue, psycopg.errors.CheckViolation):
            raise ApiError(422, "kimlik_verilemedi", "Kimlik verilemedi: veri kaynağı, süre veya sınır geçersiz.") from None
        expires = cur.fetchone()[0]  # type: ignore[index]
        meta = {"name": body.name, "data_source": name, "token_hint": token_hint(token),
                "expires_at": expires.isoformat(), "per_minute": body.per_minute}
        remember(cur, p.audit_id, body.idempotency_key, f"/api/v1/sources/{name}/credentials", digest, 201,
                 {**meta, "token": None,
                  "message": "Belirteç yalnızca ilk yanıtta gösterildi. Kaybedildiyse kimliği iptal edip yenisini "
                             "verin."})
    response = {**meta, "token": token, "replayed": False,
                "message": "Belirteci şimdi kurum sır kasasına koyun; bu pencere kapanınca bir daha gösterilmez."}
    return JSONResponse(response, status_code=201, headers={"Cache-Control": "no-store"})


@router.post("/credentials/{name}/revoke", dependencies=[Depends(require_csrf)])
def revoke_credential(name: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("data_source", "connector_admin")
    row = ctx.conn.execute("SELECT state FROM ingest.connector_credential_status WHERE name = %s", (name,)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "Bağlayıcı kimliği bulunamadı.")
    if row[0] == "revoked":
        raise ApiError(409, "zaten_iptal", "Kimlik zaten iptal edilmiş.")
    source = ctx.conn.execute("SELECT ingest.revoke_connector_credential(%s, %s)",
                              (name, ctx.principal.audit_id)).fetchone()[0]  # type: ignore[index]
    if source is None:  # pragma: no cover - aynı anda iptal
        raise ApiError(409, "zaten_iptal", "Kimlik zaten iptal edilmiş.")
    return {"name": name, "data_source": source, "state": "revoked",
            "message": "Kimlik iptal edildi; alım geçidi bu belirteci artık kabul etmez."}
