"""Referans verisi araması (grup, kişi, konum…): düzenleme penceresindeki seçim listeleri için.

Tablo adı yalnızca sabit listeden (``REF_TABLES``) çözülür; arama metni LIKE özel karakterlerinden arındırılır.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg import sql

from ..deps import Ctx, context
from ..errors import ApiError
from ..present import REF_TABLES

router = APIRouter(prefix="/api/v1", tags=["referans"])


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/ref/{table}")
def search_ref(table: str, ctx: Ctx = Depends(context), q: str = Query("", max_length=100),
               limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    if table not in REF_TABLES:
        raise ApiError(404, "bulunamadi", "Referans tablosu tanımsız.")
    ctx.require("cmdb_ci", "read")
    where = sql.SQL("lower(name) LIKE lower(%s)") if q.strip() else sql.SQL("true")
    params: list[Any] = [_like(q.strip())] if q.strip() else []
    rows = ctx.conn.execute(sql.SQL("SELECT sys_id, name FROM {} WHERE {} ORDER BY lower(name), sys_id LIMIT %s").format(
        sql.Identifier("ref", table), where), (*params, limit + 1)).fetchall()
    return {"items": [{"sys_id": s, "name": n or "(adsız)"} for s, n in rows[:limit]], "more": len(rows) > limit}
