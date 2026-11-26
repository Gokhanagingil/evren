"""Anomali incelemesi (ADR-0025, kural 10): yetkisiz değişiklik anomalisini incelendi / yanlış alarm olarak işaretleme.

Tespit belirlenimcidir, karar insanındır. Uç ``anomaly/anomaly_review`` izni ister (Veri sorumlusu). Karar veren, zaman
ve gerekçe anomali satırına; karar ayrıca ``audit.security_event``'e yazılır. ``resolved`` durumunu yalnızca uzlaştırıcı
verir (sonradan kaydedilen onaylı değişiklik); insan yanlış alarmı ve incelemeyi geri açabilir. Aynı kararı tekrar
göndermek durumu değiştirmez (doğal olarak tekrar güvenli).
"""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evren_core.change_control import ANOMALY_STATUS_TR

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["insight"])

ANOMALY_STATUS = ANOMALY_STATUS_TR
# hedef durum → hangi durumlardan geçilebilir
TRANSITIONS = {"acknowledged": {"open"}, "false_positive": {"open", "acknowledged"},
               "open": {"acknowledged", "false_positive"}}


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["acknowledged", "false_positive", "open"]
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _note_for_false_positive(self) -> ReviewBody:
        if self.status == "false_positive" and not (self.note or "").strip():
            raise ValueError("Yanlış alarm için gerekçe zorunludur")
        return self


@router.post("/anomalies/{sys_id}/review", dependencies=[Depends(require_csrf)])
def review_anomaly(sys_id: str, body: ReviewBody, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", sys_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    ctx.require("anomaly", "anomaly_review")
    note = (body.note or "").strip() or None
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("SELECT status, kind FROM insight.anomaly WHERE sys_id = %s FOR UPDATE", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Anomali bulunamadı.")
        current, kind = row
        if current == body.status:
            return {"sys_id": sys_id, "status": current, "status_label": ANOMALY_STATUS[current], "changed": False}
        if current not in TRANSITIONS[body.status]:
            raise ApiError(409, "durum_uygun_degil",
                           f"Anomali şu an '{ANOMALY_STATUS.get(current, current)}'; bu karar verilemez.")
        cur.execute("""UPDATE insight.anomaly SET status = %s, reviewed_by = %s, reviewed_at = now(), review_note = %s
                        WHERE sys_id = %s""", (body.status, ctx.principal.audit_id, note, sys_id))
    ctx.security_event("anomaly_review", "allow", f"anomaly:{sys_id}",
                       {"kind": kind, "from": current, "to": body.status})
    return {"sys_id": sys_id, "status": body.status, "status_label": ANOMALY_STATUS[body.status], "changed": True}
