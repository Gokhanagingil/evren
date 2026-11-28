"""Hata sözleşmesi: ``{"error": {"code", "message" (Türkçe), "details"}}``. İç ayrıntı (SQL, yığın) sızdırılmaz.

ServiceNow uyumlu uçlarda (``/api/now/``) hata ServiceNow biçimindedir: ``{"error": {"message", "detail"},
"status": "failure"}`` (ADR-0044); doğrulama hatası orada 400'dür."""
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


def _sn(request: Request) -> bool:
    return request.url.path.startswith("/api/now/")


def _respond(request: Request, status: int, code: str, message: str, details: list[Any] | None = None) -> JSONResponse:
    if _sn(request):
        detail = next((d for d in details or [] if isinstance(d, str)), "")
        return JSONResponse({"error": {"message": message, "detail": detail}, "status": "failure"}, status_code=status)
    return JSONResponse(_body(code, message, details), status_code=status)


def install_handlers(app: Any) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _respond(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Girdi değerleri yanıta yansıtılmaz; yalnızca alan konumu ve hata türü.
        details = [{"field": ".".join(str(p) for p in e.get("loc", ())), "type": e.get("type")} for e in exc.errors()]
        if _sn(request):
            fields = ", ".join(str(d["field"]).removeprefix("query.") for d in details)
            return _respond(request, 400, "gecersiz_istek", "İstek doğrulanamadı.", [f"Geçersiz parametre: {fields}"])
        return JSONResponse(_body("gecersiz_istek", "İstek doğrulanamadı.", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        messages = {404: ("bulunamadi", "Kaynak bulunamadı."), 405: ("yontem_yok", "Bu yöntem desteklenmiyor.")}
        code, message = messages.get(exc.status_code, ("http_hatasi", "İstek işlenemedi."))
        return _respond(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        ref = uuid.uuid4().hex[:12]
        log.exception("Beklenmeyen hata ref=%s tür=%s", ref, type(exc).__name__)
        return _respond(request, 500, "beklenmeyen_hata", f"Beklenmeyen bir hata oluştu (başvuru: {ref}).", [])
