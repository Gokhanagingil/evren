"""Uygulama fabrikası: API + statik arayüz, tek güvenlik başlığı kaynağı (``evren_core.web``)."""
from __future__ import annotations

import logging
import posixpath
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from evren_core.metadata import load_metadata
from evren_core.web import SECURITY_HEADERS, STATIC_TYPES

from .authz import Authorizer
from .config import REPO_ROOT, Settings
from .db import make_pool
from .editing import EditPolicy
from .entra import Entra
from .errors import install_handlers
from .present import Presenter
from .routers import auth, ci, platform, queue, refs, reports
from .rules_view import build_rules_view
from .security import SessionCodec

__all__ = ["create_app"]
log = logging.getLogger("evren.api")


def create_app(settings: Settings, *, pool: Any | None = None, entra: Entra | None = None) -> FastAPI:
    md = load_metadata(REPO_ROOT / "metadata")
    own_pool = pool is None
    pool = pool or make_pool(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        if own_pool:
            pool.open(wait=True)
        try:
            yield
        finally:
            if own_pool:
                pool.close()

    # Swagger/ReDoc arayüzleri dış CDN'den yüklenir; CSP ile çelişir. Yalnızca OpenAPI belgesi sunulur.
    app = FastAPI(title="Evren CMDB API", version="0.2.0", docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json", lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = pool
    app.state.metadata = md
    app.state.authorizer = Authorizer(md.hierarchy)
    app.state.presenter = Presenter(md)
    app.state.sessions = SessionCodec(settings.session_key, settings.session_hours)
    app.state.rules_view = build_rules_view(md)
    app.state.edit_policy = EditPolicy(md)
    app.state.entra = entra or (Entra(settings.entra) if settings.entra else None)
    install_handlers(app)

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith(("/api/", "/auth/")) or request.url.path in ("/healthz", "/readyz"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        try:
            with app.state.pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
        except Exception:  # hazır değil; ayrıntı sızdırılmaz
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    for r in (platform.router, ci.router, queue.router, refs.router, reports.router, auth.router):
        app.include_router(r)

    @app.get("/content/backend.json", include_in_schema=False)
    def backend_marker() -> JSONResponse:
        # Arayüz bu işaretle API modunda çalışır (statik sunucuda aynı dosya "demo" der).
        return JSONResponse({"mode": "api", "auth_mode": settings.auth_mode}, headers={"Cache-Control": "no-store"})

    web_root = settings.web_root.resolve()

    def _static(path: str) -> Path | None:
        clean = posixpath.normpath(unquote(path))
        if clean in ("/", "."):
            clean = "/index.html"
        candidate = (web_root / clean.lstrip("/")).resolve()
        if web_root not in candidate.parents or candidate.suffix not in STATIC_TYPES or not candidate.is_file():
            return None
        if "tests" in candidate.relative_to(web_root).parts:
            return None
        return candidate

    @app.get("/{path:path}", include_in_schema=False)
    def static(path: str) -> Response:
        if path.startswith(("api/", "auth/")):
            return JSONResponse({"error": {"code": "bulunamadi", "message": "Kaynak bulunamadı.", "details": []}},
                                status_code=404)
        target = _static("/" + path)
        if target is None:
            return Response("Bulunamadı", status_code=404, media_type="text/plain; charset=utf-8")
        cache = "no-cache" if target.suffix in (".html", ".json") else "public, max-age=300"
        return Response(target.read_bytes(), media_type=STATIC_TYPES[target.suffix], headers={"Cache-Control": cache})

    return app
