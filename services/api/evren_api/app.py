"""Uygulama fabrikası: API + statik arayüz, tek güvenlik başlığı kaynağı (``evren_core.web``)."""
from __future__ import annotations

import ipaddress
import json
import logging
import posixpath
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from evren_core.changeset import load_change_files
from evren_core.metadata import load_metadata
from evren_core.opensearch_client import OpenSearch
from evren_core.web import SECURITY_HEADERS, STATIC_TYPES

from .authz import Authorizer
from .config import REPO_ROOT, Settings
from .db import make_pool
from .editing import EditPolicy
from .entra import Entra
from .errors import install_handlers
from .present import Presenter
from .routers import (
    admin,
    attestation,
    auth,
    ci,
    connectors,
    graph,
    impact,
    insight,
    manual,
    platform,
    queue,
    refs,
    reports,
    schema,
    search,
    staleness,
    system,
    timeline,
)
from .rules_view import build_rules_view
from .security import SessionCodec

__all__ = ["create_app"]


def _private_client(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("testclient", "localhost")
    return ip.is_loopback or ip.is_private
log = logging.getLogger("evren.api")

MAX_BODY_BYTES = 5 * 1024 * 1024  # CSV içe aktarma (2 MB metin) JSON kaçışlarıyla birlikte sığar


class _TooLarge(Exception):
    pass


class BodyLimit:
    """İstek gövdesi üst sınırı (ASVS V12/V13): ``Content-Length`` sınırı aşıyorsa veya akış sırasında aşılırsa 413.
    Ayrıştırmadan önce uygulanır; büyük gövde belleğe alınmaz."""

    def __init__(self, app: Any, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def _reject(self, send: Any) -> None:
        body = json.dumps({"error": {"code": "istek_cok_buyuk", "details": [],
                                     "message": f"İstek gövdesi çok büyük (en çok {self.max_bytes // 1048576} MB)."}},
                          ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                   (b"cache-control", b"no-store")]
        headers += [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            await self._reject(send)
            return
        received = 0
        started = False

        async def limited_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if started:
                raise
            await self._reject(send)


def create_app(settings: Settings, *, pool: Any | None = None, entra: Entra | None = None,
               search_client: Any | None = None) -> FastAPI:
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
    app.state.applied_change_files = frozenset(n for n, _ in load_change_files(REPO_ROOT / "metadata" / "changes"))
    app.state.authorizer = Authorizer(md.hierarchy)
    app.state.presenter = Presenter(md)
    app.state.sessions = SessionCodec(settings.session_key, settings.session_hours)
    app.state.rules_view = build_rules_view(md)
    app.state.edit_policy = EditPolicy(md)
    app.state.entra = entra or (Entra(settings.entra) if settings.entra else None)
    app.state.search = search_client if search_client is not None else (
        OpenSearch(settings.opensearch) if settings.opensearch else None)
    install_handlers(app)
    app.add_middleware(BodyLimit, max_bytes=MAX_BODY_BYTES)

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Response:
        if settings.dev_container and not _private_client(request):
            # Geliştirme konteyneri kimlik doğrulamasızdır: yalnızca ana makine/özel ağdan gelen istekler.
            return JSONResponse({"error": {"code": "erisim_yok", "message": "Geliştirme yığınına yalnızca yerel "
                                           "ağdan erişilebilir.", "details": []}}, status_code=403)
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

    for r in (platform.router, ci.router, queue.router, refs.router, reports.router, schema.router, search.router,
              connectors.router, insight.router, manual.router, attestation.router, staleness.router, impact.router,
              timeline.router, graph.router, system.router, admin.router, auth.router):
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
