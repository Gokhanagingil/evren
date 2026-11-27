"""Alım geçidi (U2, ADR-0017) — bağlayıcıların tek giriş noktası.

Doğrulama zinciri: belirteç (bağlayıcı kimliği) → kaynak yetkisi (kimlik yalnızca kendi veri kaynağına yazar; emekli
kaynak yazamaz) → zarf şeması (bilinmeyen alan reddedilir) → çalışma durumu → boyut ve hız sınırı → Kafka teslimi.
Geçit eşleştirme, uzlaştırma veya CMDB yazımı yapmaz; kaynak kaydına erişimi de yoktur (göç 0012).

| Uç nokta | Sonuç |
| --- | --- |
| ``POST /ingest/v1/runs`` | Çalışma açar (201; aynı kimlikle tekrar: 200) |
| ``POST /ingest/v1/runs/{run_id}/records`` | Toplu kayıt; Kafka onayından sonra 202 |
| ``POST /ingest/v1/runs/{run_id}/complete`` | Kapanış ister (bildirilen sayı); uzlaştırıcı tümünü işleyince kapatır |
| ``GET /ingest/v1/runs/{run_id}`` | Çalışma durumu (açık, kapanıyor, kapandı + devre kesici) |

Hatalar: 400 şema/kayıt, 401 kimlik, 403 kaynak yetkisi, 404 çalışma, 409 çalışma durumu, 411/413 gövde, 429 hız,
503 Kafka. Hata gövdesi girdiyi yansıtmaz.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from evren_core.credentials import token_sha256, well_formed
from evren_core.envelope import IngestEnvelope, RunEnvelope
from evren_core.kafka_client import KafkaPublisher, Publisher, PublishError
from evren_core.messaging import TOPIC_RECORD, TOPIC_RUN, MessageError, encode_record, record_key
from evren_core.metadata import Metadata, load_metadata
from evren_core.sources import SourceRegistryError
from evren_core.web import SECURITY_HEADERS

from .config import GatewaySettings

log = logging.getLogger("evren.gateway")
FUTURE_SKEW = timedelta(minutes=10)


class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list[Any] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details or [], headers


class Credential(NamedTuple):
    id: UUID
    name: str
    data_source: str
    per_minute: int

    @property
    def principal(self) -> str:
        return f"connector:{self.name}"


class RecordBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[IngestEnvelope] = Field(min_length=1)


class RateLimiter:
    """Kimlik başına kova: dakikada ``per_minute`` kayıt, kısa patlamalara izin verir. Süreç içi (örnek başına)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._buckets: dict[UUID, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def take(self, cred: Credential, n: int) -> float | None:
        """Kayıtları düşer; yetmiyorsa kaç saniye sonra deneneceğini döndürür."""
        rate = cred.per_minute / 60.0
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(cred.id, (float(cred.per_minute), now))
            tokens = min(float(cred.per_minute), tokens + (now - last) * rate)
            if tokens >= n:
                self._buckets[cred.id] = (tokens - n, now)
                return None
            self._buckets[cred.id] = (tokens, now)
            return max(1.0, (n - tokens) / rate)


def make_pool(settings: GatewaySettings) -> ConnectionPool:
    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.autocommit = True
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(settings.db_role)))
        conn.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(settings.statement_timeout_ms)))
        conn.execute("SET application_name = 'evren-gateway'")

    return ConnectionPool(settings.db_conninfo, min_size=1, max_size=settings.db_pool_size, configure=configure,
                          open=False, name="evren-gateway", timeout=10)


def _err(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or []}}


def create_app(settings: GatewaySettings, *, pool: Any | None = None, publisher: Publisher | None = None,
               metadata: Metadata | None = None, clock: Callable[[], datetime] | None = None) -> FastAPI:
    md = metadata or load_metadata(settings.metadata_dir)
    registry = md.sources
    own_pool = pool is None
    pool = pool or make_pool(settings)
    publisher = publisher or KafkaPublisher(settings.kafka, "evren-gateway")
    limiter = RateLimiter()
    now = clock or (lambda: datetime.now(UTC))
    # Nabız (sistem durumu, U8.1): örnek başına kabul edilen kayıt ve Kafka teslim hatası; Kafka bağlantısı ayrıntıda
    stats = {"accepted": 0, "publish_failed": 0}
    started = time.time()
    worker_id = f"gateway-{socket.gethostname()}-{os.getpid()}"
    stop = threading.Event()

    def beat(*, stopped: bool = False) -> None:
        """Tek nabız yazar; veritabanına ulaşılamazsa sessizce bir sonrakini bekler (geçit isteği etkilenmez)."""
        details = {"kafka": {"ok": bool(publisher.ready(timeout=2.0))}}
        try:
            with pool.connection(timeout=5) as conn:
                conn.execute(
                    """INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, stopped_at, processed, failed,
                                                           poll_seconds, kind, details)
                       VALUES (%(id)s, to_timestamp(%(started)s), now(), CASE WHEN %(stopped)s THEN now() END,
                               %(done)s, %(failed)s, %(poll)s, 'gateway', %(details)s)
                       ON CONFLICT (worker_id) DO UPDATE SET seen_at = now(),
                         stopped_at = CASE WHEN %(stopped)s THEN now() END, processed = EXCLUDED.processed,
                         failed = EXCLUDED.failed, details = EXCLUDED.details""",
                    {"id": worker_id, "started": started, "stopped": stopped, "done": stats["accepted"],
                     "failed": stats["publish_failed"], "poll": max(settings.heartbeat_seconds, 1),
                     "details": Jsonb(details)})
        except Exception as exc:  # nabız yazılamazsa geçit çalışmayı sürdürür
            log.warning("Geçit nabzı yazılamadı (%s)", type(exc).__name__)

    def beat_loop() -> None:
        while not stop.wait(settings.heartbeat_seconds):
            beat()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if own_pool:
            pool.open(wait=False)
        thread = None
        if settings.heartbeat_seconds > 0 and own_pool:
            beat()
            thread = threading.Thread(target=beat_loop, name="gateway-heartbeat", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=5)
            beat(stopped=True)
        publisher.close()
        if own_pool:
            pool.close()

    app = FastAPI(title="Evren alım geçidi", version="1", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    # ------------------------------------------------------------------ ara katmanlar ve hata sözleşmesi
    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Response:
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                response: Response = JSONResponse(_err("uzunluk_gerekli", "Content-Length başlığı gerekli."), 411)
            elif int(length) > settings.max_body_bytes:
                response = JSONResponse(_err("govde_buyuk", "İstek gövdesi çok büyük.",
                                             [{"max_bytes": settings.max_body_bytes}]), 413)
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(_err(exc.code, exc.message, exc.details), exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e.get("loc", ())), "type": e.get("type")}
                   for e in exc.errors()[:50]]
        return JSONResponse(_err("gecersiz_zarf", "Zarf şemaya uymuyor.", details), 400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "bulunamadi" if exc.status_code == 404 else "istek_hatasi"
        return JSONResponse(_err(code, "Kaynak bulunamadı." if exc.status_code == 404 else "İstek işlenemedi."),
                            exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        error_id = f"{int(time.time() * 1000):x}"
        log.error("Beklenmeyen hata %s: %s", error_id, type(exc).__name__, exc_info=exc)
        return JSONResponse(_err("ic_hata", f"Beklenmeyen hata (hata kimliği {error_id})."), 500)

    # ------------------------------------------------------------------ kimlik ve yetki
    def audit(principal: str, action: str, resource: str, decision: str, detail: dict[str, Any]) -> None:
        with pool.connection() as conn:
            conn.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                         "VALUES (%s, %s, %s, %s, %s)", (principal, action, resource, decision, Jsonb(detail)))

    def credential(request: Request) -> Credential:
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not well_formed(token.strip()):
            raise GatewayError(401, "kimlik_gerekli", "Geçerli bir bağlayıcı belirteci gerekli.",
                               headers={"WWW-Authenticate": "Bearer"})
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT id, name, data_source, max_records_per_minute, last_used_at
                             FROM ingest.connector_credential
                            WHERE token_sha256 = %s AND revoked_at IS NULL AND expires_at > now()""",
                        (token_sha256(token.strip()),))
            row = cur.fetchone()
            if row is None:
                raise GatewayError(401, "kimlik_gecersiz", "Belirteç geçersiz, süresi dolmuş veya iptal edilmiş.",
                                   headers={"WWW-Authenticate": "Bearer"})
            if row[4] is None or row[4] < now() - timedelta(minutes=1):
                cur.execute("UPDATE ingest.connector_credential SET last_used_at = now() WHERE id = %s", (row[0],))
        return Credential(row[0], row[1], row[2], row[3])

    def require_source(cred: Credential, data_source: str, action: str) -> None:
        if data_source != cred.data_source:
            audit(cred.principal, action, f"data_source:{data_source}", "deny",
                  {"reason": "kimlik başka kaynağa bağlı", "credential_source": cred.data_source})
            raise GatewayError(403, "kaynak_yetkisi_yok", "Bu belirteç yalnızca kendi veri kaynağı adına yazabilir.")
        try:
            src = registry.source(data_source)
        except SourceRegistryError:
            raise GatewayError(403, "kaynak_tanimsiz", "Veri kaynağı tanımlı değil.") from None
        if src.lifecycle == "retired":
            audit(cred.principal, action, f"data_source:{data_source}", "deny", {"reason": "emekli kaynak"})
            raise GatewayError(403, "kaynak_emekli", "Emekliye ayrılmış veri kaynağı veri gönderemez.")

    def load_run(cur: psycopg.Cursor[Any], run_id: UUID, cred: Credential, *, lock: bool = False) -> dict[str, Any]:
        query = sql.SQL("""SELECT data_source, opened_by, accepted_count, reported_count, reported_completed,
                                  reported_error_count, close_requested_at, finished_at, completed, record_count,
                                  breaker_state, breaker_reasons
                             FROM ingest.source_run WHERE run_id = %s {}""").format(
            sql.SQL("FOR UPDATE") if lock else sql.SQL(""))
        cur.execute(query, (run_id,))
        row = cur.fetchone()
        # Başka kaynağın çalışması da "bulunamadı" döner: kimlik, varlığını öğrenemez.
        if row is None or row[0] != cred.data_source:
            raise GatewayError(404, "calisma_yok", "Çalışma bulunamadı.")
        keys = ("data_source", "opened_by", "accepted", "reported", "reported_completed", "reported_errors",
                "close_requested_at", "finished_at", "completed", "record_count", "breaker_state", "breaker_reasons")
        return dict(zip(keys, row, strict=True))

    def run_status(run_id: UUID, run: dict[str, Any]) -> dict[str, Any]:
        state = "finished" if run["finished_at"] else "closing" if run["close_requested_at"] else "open"
        return {"run_id": str(run_id), "state": state,
                "state_label": {"open": "Açık", "closing": "Kapanıyor (kayıtlar işleniyor)", "finished": "Kapandı"}[state],
                "accepted": run["accepted"], "reported": run["reported"], "processed": run["record_count"],
                "completed": run["completed"] if run["finished_at"] else None,
                "breaker_state": run["breaker_state"], "breaker_reasons": list(run["breaker_reasons"] or [])}

    def publish_run_event(env: RunEnvelope, cred: Credential) -> None:
        """Çalışma olayları bilgilendirme amaçlıdır (izleme); çalışmanın kendisi veritabanında yönetilir."""
        try:
            publisher.publish([(TOPIC_RUN, cred.data_source.encode(), env.model_dump_json().encode(),
                                [("evren-connector", cred.name.encode())])], timeout=5)
        except PublishError:
            log.warning("Çalışma olayı Kafka'ya yazılamadı (%s %s)", env.type, env.data.run_id)

    # ------------------------------------------------------------------ uç noktalar
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        try:
            with pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
        except Exception:
            return JSONResponse({"status": "unavailable", "database": False}, 503)
        if not publisher.ready():
            return JSONResponse({"status": "unavailable", "kafka": False}, 503)
        return JSONResponse({"status": "ok"})

    @app.post("/ingest/v1/runs", status_code=201)
    def open_run(body: RunEnvelope, cred: Credential = Depends(credential)) -> JSONResponse:
        if body.type != "evren.ingest.run.started.v1":
            raise GatewayError(400, "olay_turu", "Çalışma açmak için evren.ingest.run.started.v1 gönderin.")
        d = body.data
        require_source(cred, d.data_source, "ingest_run_open")
        with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT data_source, opened_by FROM ingest.source_run WHERE run_id = %s", (d.run_id,))
            prior = cur.fetchone()
            if prior is not None:
                if tuple(prior) != (cred.data_source, cred.name):
                    raise GatewayError(409, "calisma_kimligi_kullanilmis", "Bu çalışma kimliği daha önce kullanılmış.")
                return JSONResponse(run_status(d.run_id, load_run(cur, d.run_id, cred)) | {"replayed": True}, 200)
            cur.execute("""INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, opened_by, notes)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (d.run_id, d.data_source, d.mode, d.started_at, cred.name, d.notes))
            cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                        "VALUES (%s, 'ingest_run_open', %s, 'allow', %s)",
                        (cred.principal, f"source_run:{d.run_id}", Jsonb({"mode": d.mode, "data_source": d.data_source})))
        publish_run_event(body, cred)
        return JSONResponse({"run_id": str(d.run_id), "state": "open", "state_label": "Açık", "replayed": False}, 201)

    @app.post("/ingest/v1/runs/{run_id}/records", status_code=202)
    def post_records(run_id: UUID, body: RecordBatch, cred: Credential = Depends(credential)) -> dict[str, Any]:
        if len(body.records) > settings.max_batch:
            raise GatewayError(413, "toplu_buyuk", f"Toplu gönderimde en fazla {settings.max_batch} kayıt olabilir.")
        with pool.connection() as conn, conn.cursor() as cur:
            run = load_run(cur, run_id, cred)
        if run["close_requested_at"] or run["finished_at"]:
            raise GatewayError(409, "calisma_kapali", "Kapanışı istenmiş veya kapanmış çalışmaya kayıt eklenemez.")
        require_source(cred, cred.data_source, "ingest_records")
        errors: list[dict[str, Any]] = []
        messages = []
        horizon = now() + FUTURE_SKEW
        ingested = now().isoformat().encode()
        for i, env in enumerate(body.records):
            r = env.data
            if r.data_source != cred.data_source:
                errors.append({"index": i, "field": "data.data_source", "type": "kaynak_uyumsuz"})
            elif r.run_id != run_id:
                errors.append({"index": i, "field": "data.run_id", "type": "calisma_uyumsuz"})
            elif r.observed_at > horizon:
                errors.append({"index": i, "field": "data.observed_at", "type": "gelecek_tarih"})
            else:
                try:
                    value = encode_record(env)
                except MessageError:
                    errors.append({"index": i, "field": "data", "type": "kayit_buyuk"})
                    continue
                messages.append((TOPIC_RECORD, record_key(r.data_source, r.source_key), value,
                                 [("evren-connector", cred.name.encode()), ("evren-ingested-at", ingested)]))
        if errors:
            raise GatewayError(400, "gecersiz_kayit", "Toplu gönderimdeki kayıtlar reddedildi; hiçbiri alınmadı.",
                               errors[:50])
        if (wait := limiter.take(cred, len(messages))) is not None:
            raise GatewayError(429, "hiz_siniri", "Dakikalık kayıt sınırı aşıldı.",
                               [{"per_minute": cred.per_minute}], headers={"Retry-After": str(int(wait + 0.999))})
        try:
            publisher.publish(messages, timeout=settings.publish_timeout_s)
        except PublishError:
            stats["publish_failed"] += 1
            raise GatewayError(503, "kafka_kullanilamaz", "Kayıtlar olay omurgasına teslim edilemedi; yeniden deneyin.",
                               headers={"Retry-After": "5"}) from None
        stats["accepted"] += len(messages)
        with pool.connection() as conn:
            conn.execute("UPDATE ingest.source_run SET accepted_count = accepted_count + %s WHERE run_id = %s",
                         (len(messages), run_id))
        return {"run_id": str(run_id), "accepted": len(messages)}

    @app.post("/ingest/v1/runs/{run_id}/complete", status_code=202)
    def complete_run(run_id: UUID, body: RunEnvelope, cred: Credential = Depends(credential)) -> JSONResponse:
        if body.type != "evren.ingest.run.completed.v1":
            raise GatewayError(400, "olay_turu", "Kapanış için evren.ingest.run.completed.v1 gönderin.")
        d = body.data
        if d.run_id != run_id:
            raise GatewayError(400, "calisma_uyumsuz", "Zarftaki çalışma kimliği adresle uyuşmuyor.")
        require_source(cred, d.data_source, "ingest_run_close")
        with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            run = load_run(cur, run_id, cred, lock=True)
            reported = (d.record_count, d.completed, d.error_count)
            if run["close_requested_at"] or run["finished_at"]:
                if (run["reported"], run["reported_completed"], run["reported_errors"]) == reported:
                    return JSONResponse(run_status(run_id, run) | {"replayed": True}, 200)
                raise GatewayError(409, "calisma_kapali", "Çalışmanın kapanışı farklı değerlerle zaten istenmiş.")
            if d.record_count > run["accepted"]:
                raise GatewayError(409, "sayi_uyumsuz", "Bildirilen kayıt sayısı geçide ulaşan kayıt sayısından fazla.",
                                   [{"reported": d.record_count, "accepted": run["accepted"]}])
            cur.execute("""UPDATE ingest.source_run SET reported_count = %s, reported_completed = %s,
                                  reported_error_count = %s, close_requested_at = now() WHERE run_id = %s""",
                        (*reported, run_id))
            cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                        "VALUES (%s, 'ingest_run_close', %s, 'allow', %s)",
                        (cred.principal, f"source_run:{run_id}",
                         Jsonb({"reported": d.record_count, "accepted": run["accepted"], "completed": d.completed,
                                "errors": d.error_count})))
            run = load_run(cur, run_id, cred)
        publish_run_event(body, cred)
        return JSONResponse(run_status(run_id, run) | {"replayed": False}, 202)

    @app.get("/ingest/v1/runs/{run_id}")
    def get_run(run_id: UUID, cred: Credential = Depends(credential)) -> dict[str, Any]:
        with pool.connection() as conn, conn.cursor() as cur:
            return run_status(run_id, load_run(cur, run_id, cred))

    app.state.pool, app.state.publisher, app.state.limiter = pool, publisher, limiter
    app.state.beat, app.state.worker_id = beat, worker_id
    return app
