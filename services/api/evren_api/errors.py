"""Hata sözleşmesi: ``{"error": {"code", "message" (Türkçe), "details"}}``. İç ayrıntı (SQL, yığın) sızdırılmaz."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("evren.api")

__all__ = ["ApiError", "install_handlers"]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list[Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or []


def _body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or []}}


def install_handlers(app: Any) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Girdi değerleri yanıta yansıtılmaz; yalnızca alan konumu ve hata türü.
        details = [{"field": ".".join(str(p) for p in e.get("loc", ())), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(_body("gecersiz_istek", "İstek doğrulanamadı.", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        messages = {404: ("bulunamadi", "Kaynak bulunamadı."), 405: ("yontem_yok", "Bu yöntem desteklenmiyor.")}
        code, message = messages.get(exc.status_code, ("http_hatasi", "İstek işlenemedi."))
        return JSONResponse(_body(code, message), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        ref = uuid.uuid4().hex[:12]
        log.exception("Beklenmeyen hata ref=%s tür=%s", ref, type(exc).__name__)
        return JSONResponse(_body("beklenmeyen_hata", f"Beklenmeyen bir hata oluştu (başvuru: {ref}).", []),
                            status_code=500)
