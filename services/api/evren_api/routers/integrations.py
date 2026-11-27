"""Entegrasyon sihirbazı (U8.7, ADR-0038): yeni bağlayıcı örneğini panelden adım adım kurmak.

ADR-0021'e bağlı kalır: örnek tanımı (kaynak adresi, kullanıcı adı, sır dosya yolları) yalnızca çalıştırıcıda durur.
API tanımı **doğrular ve kurulum paketini üretir, saklamaz ve günlüğe yazmaz**; sır değeri hiç istenmez (yalnızca
kasadan bağlanan dosyanın yolu). Geçit kimliği ve deneme çalıştırması mevcut uçlarla yapılır (``/sources/...``).
Durum ucu kurulumun ilerleyişini türetir: veri kaynağı, kimlik, çalıştırıcının örneği bildirmesi, son çalışma.
Tüm uçlar ``data_source/connector_admin`` ister.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from evren_core.connectors.instances import (
    AUTH_FIELDS,
    URL_EXAMPLES,
    ConfigError,
    parse_instance,
    render_instance,
    secret_files,
)
from evren_core.connectors.sources import SOURCES
from evren_core.governance import next_run

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError

router = APIRouter(prefix="/api/v1/integrations", tags=["entegrasyon"])

_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$"
_TZ = "Europe/Istanbul"
LIFECYCLE_TR = {"active": "Etkin", "shadow": "Gölge", "retiring": "Devrediliyor", "retired": "Emekli"}
AUTH_TR = {"bearer": "API belirteci", "basic": "Kullanıcı adı ve parola", "login": "Kullanıcı adı ve parola (oturum)",
           "oauth": "Entra istemci kimliği (OAuth)"}
OPTION_TR = {"page_size": "Sayfa boyutu", "release": "API sürümü", "lookback_days": "Geriye bakış (gün)"}


def _admin(ctx: Ctx) -> None:
    ctx.require("data_source", "connector_admin")


@router.get("")
def catalog(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Bağlanabilecek kaynak türleri (istemci kataloğu), rolleri ve mevcut örnekleri; yarım kalan kurulumlar."""
    _admin(ctx)
    md = ctx.state.metadata
    reg = md.sources
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT name, data_source FROM ingest.connector_instance ORDER BY name")
        reported = cur.fetchall()
        cur.execute("""SELECT c.name, c.data_source, c.created_at, c.created_by
                         FROM ingest.connector_credential_status c
                        WHERE c.state = 'active' AND NOT EXISTS (SELECT 1 FROM ingest.connector_instance i
                                                                   WHERE i.name = c.name)
                        ORDER BY c.created_at DESC LIMIT 20""")
        pending = [{"name": a, "data_source": b, "issued_at": c.isoformat(), "issued_by": d} for a, b, c, d in cur.fetchall()]
    items = []
    for key, spec in SOURCES.items():
        if spec.data_source not in reg.sources:
            continue
        src = reg.sources[spec.data_source]
        role = reg.roles[src.role]
        ds = next((s for s in md.data_sources if s["name"] == spec.data_source), {})
        items.append({
            "id": key, "label": src.label_tr, "data_source": src.name, "role": role.name, "role_label": role.label_tr,
            "role_help": role.description_tr, "lifecycle": src.lifecycle, "lifecycle_tr": LIFECYCLE_TR[src.lifecycle],
            "auth": spec.auth, "auth_tr": AUTH_TR[spec.auth],
            "auth_fields": [{"key": k, "label": label, "secret": secret} for k, label, secret in AUTH_FIELDS[spec.auth]],
            "options": [{"key": "api_release" if o == "release" else o, "label": OPTION_TR.get(o, o)} for o in spec.options],
            "url_required": spec.default_url is None, "url_example": URL_EXAMPLES.get(key, spec.default_url or ""),
            "default_schedule": ds.get("schedule"), "instances": [n for n, d in reported if d == src.name],
            "available": src.lifecycle != "retired",
        })
    return {"connectors": items, "pending": pending, "timezone": _TZ}


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=_NAME)
    connector: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    url: str | None = Field(default=None, max_length=500)
    user: str | None = Field(default=None, max_length=200)
    token_file: str | None = Field(default=None, max_length=500)
    password_file: str | None = Field(default=None, max_length=500)
    tenant_id: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, max_length=100)
    client_secret_file: str | None = Field(default=None, max_length=500)
    scope: str | None = Field(default=None, max_length=300)
    ca_file: str | None = Field(default=None, max_length=500)
    gateway_token_file: str = Field(max_length=500)
    schedule: str | None = Field(default=None, max_length=100)
    schedule_choice: Literal["default", "custom", "manual"] = "default"
    schedule_mode: Literal["snapshot", "delta"] = "snapshot"
    page_size: int | None = Field(default=None, ge=1, le=10000)
    api_release: str | None = Field(default=None, max_length=20)
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    max_runtime_minutes: int = Field(default=120, ge=1, le=1440)


@router.post("/plan", dependencies=[Depends(require_csrf)])
def plan(body: PlanIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Tanımı çalıştırıcıyla aynı kurallarla doğrular ve örnek dosyasına eklenecek bloğu üretir. Saklanmaz."""
    _admin(ctx)
    md = ctx.state.metadata
    raw: dict[str, Any] = {k: v for k, v in body.model_dump(exclude={"schedule_choice", "schedule"}).items()
                           if v not in (None, "")}
    if isinstance(raw.get("lookback_days"), int):
        raw["lookback_days"] = str(raw["lookback_days"])  # ortam değişkeni olarak geçer
    for k, v in list(raw.items()):
        if isinstance(v, str):
            raw[k] = v.strip()
    if body.schedule_choice == "manual":
        raw["schedule"] = None
    elif body.schedule_choice == "custom":
        raw["schedule"] = (body.schedule or "").strip()
    if body.max_runtime_minutes == 120:
        raw.pop("max_runtime_minutes")
    if body.schedule_mode == "snapshot":
        raw.pop("schedule_mode")
    try:
        inst = parse_instance(raw, md, check_files=False)
    except ConfigError as exc:
        raise ApiError(422, "yapilandirma_gecersiz", str(exc)) from None
    src = md.sources.sources[inst.data_source]
    warnings: list[str] = []
    if ctx.conn.execute("SELECT 1 FROM ingest.connector_instance WHERE name = %s", (inst.name,)).fetchone():
        warnings.append("Bu adla bir örnek zaten çalışıyor; bu blok mevcut tanımın yerine geçer.")
    if src.lifecycle == "shadow":
        warnings.append("Veri kaynağı gölge modda: değer yazmaz, CI açmaz; sonuçlar Gölge Mod Raporu'nda görünür. "
                        "Etkinleştirmek ayrı bir değişiklik setidir.")
    if src.lifecycle == "retired":
        warnings.append("Veri kaynağı emekli: çalıştırma istekleri reddedilir.")
    upcoming: list[str] = []
    expr = inst.schedule.expression if inst.schedule is not None else None
    if expr:
        at = datetime.now(UTC)
        for _ in range(2):
            at = next_run(expr, _TZ, at)
            upcoming.append(at.isoformat())
    return {"name": inst.name, "data_source": inst.data_source, "lifecycle": src.lifecycle,
            "yaml": render_instance(raw), "secrets": secret_files(raw), "schedule": expr, "next_runs": upcoming,
            "schedule_mode": inst.schedule_mode, "warnings": warnings}


@router.get("/{name}")
def status(name: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Kurulumun ilerleyişi: veri kaynağı, geçit kimliği, çalıştırıcının örneği bildirmesi, son çalışma. Bu adla
    henüz kimlik veya örnek yoksa bütün adımlar açık döner (``started: false``)."""
    _admin(ctx)
    if not re.fullmatch(_NAME, name):
        raise ApiError(404, "bulunamadi", "Entegrasyon bulunamadı.")
    md = ctx.state.metadata
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT data_source, state, expires_at, created_at, created_by, token_hint, last_used_at
                         FROM ingest.connector_credential_status WHERE name = %s""", (name,))
        cred = cur.fetchone()
        cur.execute("""SELECT i.data_source, i.connector, i.schedule, i.next_run_at, i.enabled, i.seen_at,
                              coalesce(i.seen_at > now() - 3 * h.poll_seconds * interval '1 second'
                                       AND h.seen_at > now() - 3 * h.poll_seconds * interval '1 second'
                                       AND h.stopped_at IS NULL, false)
                         FROM ingest.connector_instance i
                         LEFT JOIN ingest.worker_heartbeat h ON h.worker_id = i.runner_id WHERE i.name = %s""", (name,))
        inst = cur.fetchone()
        cur.execute("""SELECT id, status, origin, requested_at, finished_at, summary, result_note
                         FROM ingest.connector_run_request WHERE instance = %s ORDER BY requested_at DESC LIMIT 1""",
                    (name,))
        run = cur.fetchone()
    ds_name = (inst or cred)[0] if (inst or cred) else None  # henüz hiçbir şey yok: kurulumun başı
    src = md.sources.sources.get(ds_name) if ds_name else None
    fmt = lambda d: d.astimezone(ZoneInfo(_TZ)).isoformat() if d else None  # noqa: E731
    steps = {
        "data_source": {"ok": src is not None and src.lifecycle != "retired", "name": ds_name,  # yoksa None
                        "lifecycle": src.lifecycle if src else None,
                        "lifecycle_tr": LIFECYCLE_TR.get(src.lifecycle) if src else None},
        "credential": ({"ok": cred[1] == "active", "state": cred[1], "expires_at": fmt(cred[2]),
                        "issued_at": fmt(cred[3]), "issued_by": cred[4], "hint": cred[5], "last_used_at": fmt(cred[6])}
                       if cred else {"ok": False, "state": "missing"}),
        "instance": ({"ok": bool(inst[6]), "reported": True, "available": bool(inst[6]), "connector": inst[1],
                      "schedule": inst[2], "next_run_at": fmt(inst[3]), "enabled": inst[4], "seen_at": fmt(inst[5])}
                     if inst else {"ok": False, "reported": False}),
        "run": ({"ok": run[1] == "succeeded", "id": run[0], "status": run[1], "origin": run[2],
                 "requested_at": fmt(run[3]), "finished_at": fmt(run[4]), "summary": run[5] or {}, "note": run[6]}
                if run else {"ok": False, "status": None}),
    }
    done = all(steps[k]["ok"] for k in ("data_source", "credential", "instance", "run"))
    return {"name": name, "steps": steps, "complete": done, "started": bool(cred or inst)}
