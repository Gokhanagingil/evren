"""Denetim izi (U8.8, ADR-0039; F1-16).

Tek görünüm (``audit.trail``): güvenlik ve yönetim olayları, CI öznitelik değişiklikleri, görev olayları. İleride süreç
uygulamasının (olay, değişiklik, talep) olayları aynı görünüme yeni akış olarak eklenir; arayüz ve dışa aktarma
değişmez.

* Okuma ve dışa aktarma: ``audit/audit_read`` (Denetçi). Dışa aktarmanın kendisi de denetim olayıdır.
* Mühür doğrulama ve inceleme kaydı: ``audit/audit_review`` (Denetçi). Doğrulama sonucu ``audit.seal_check``'e,
  inceleme ``audit.review``'a yazılır; ikisi de salt eklemedir ve olay omurgasıyla SIEM'e gider.
* CI değişikliğinde eski/yeni değer, okuyanın o alanın hassasiyetini görme yetkisi yoksa gizlenir.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from evren_core.audit import (
    ACTIONS,
    CATEGORIES,
    SEAL_SQL,
    STREAMS,
    SealRecord,
    action_info,
    category_actions,
    safe_cell,
    verify_chain,
)

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash

router = APIRouter(prefix="/api/v1/audit", tags=["denetim"])

_KEY = r"^[A-Za-z0-9_.:-]{8,128}$"
_RID = re.compile(r"^[0-9a-f]{32}$")
_SYSID = re.compile(r"^[0-9a-f]{32}$")
MAX_RANGE = timedelta(days=366)
MAX_VERIFY_RANGE = timedelta(days=92)
MAX_EXPORT = 50_000
COUNT_CAP = 10_000
#: Son mühür bundan eskiyse mühürleme gecikmiş sayılır (görev çalışanı çalışmıyor olabilir)
SEAL_LAG = timedelta(hours=2)
#: Haftalık denetim izi incelemesi (CIS 8.11); bundan eskiyse Denetçiye "size düşen iş" olur
REVIEW_EVERY = timedelta(days=7)
OUTCOME_TR = {"allow": "İzin", "deny": "Ret"}
REVIEW_OUTCOME_TR = {"no_findings": "Bulgu yok", "findings": "Bulgu var"}
_COLUMNS = "source, id, at, actor, action, object, outcome, detail, client_ip, request_id"


# ---------------------------------------------------------------------------------------------- yardımcılar
def _now() -> datetime:
    return datetime.now(UTC)


def _range(since: datetime | None, until: datetime | None, *, default: timedelta = timedelta(days=7),
           limit: timedelta = MAX_RANGE) -> tuple[datetime, datetime]:
    until = (until or _now()).astimezone(UTC)
    since = (since or until - default).astimezone(UTC)
    if since >= until:
        raise ApiError(422, "gecersiz_donem", "Dönemin başlangıcı bitişinden önce olmalı.")
    if until - since > limit:
        raise ApiError(422, "donem_cok_uzun", f"Dönem en çok {limit.days} gün olabilir; daha kısa bir dönem seçin.")
    return since, until


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _category_cond(category: str) -> tuple[str, list[Any]]:
    known = sorted(ACTIONS)
    if category == "access":
        return "(source = 'security' AND (action = ANY(%s) OR action LIKE '%%:%%'))", [category_actions("access")]
    if category == "cmdb":
        return "(source = 'ci' OR (source = 'security' AND action = ANY(%s)))", [category_actions("cmdb")]
    if category == "governance":
        return "(source = 'task' OR (source = 'security' AND action = ANY(%s)))", [category_actions("governance")]
    if category == "other":
        return "(source = 'security' AND NOT action = ANY(%s) AND action NOT LIKE '%%:%%')", [known]
    return "(source = 'security' AND action = ANY(%s))", [category_actions(category)]


class Filters:
    """Olay süzgeci: sabit SQL parçaları + parametreler (kullanıcı metni yalnızca parametre olur)."""

    def __init__(self, since: datetime | None = Query(None, description="Başlangıç (ISO 8601); varsayılan 7 gün önce"),
                 until: datetime | None = Query(None, description="Bitiş (ISO 8601); varsayılan şimdi"),
                 source: Literal["security", "ci", "task"] | None = Query(None),
                 category: Literal["access", "iam", "cmdb", "schema", "integration", "governance", "audit",
                                   "other"] | None = Query(None),
                 action: str | None = Query(None, max_length=120),
                 actor: str | None = Query(None, max_length=200),
                 outcome: Literal["allow", "deny"] | None = Query(None),
                 object: str | None = Query(None, max_length=200),
                 request_id: str | None = Query(None, max_length=32),
                 ip: str | None = Query(None, max_length=45),
                 q: str | None = Query(None, max_length=200)) -> None:
        self.since, self.until = _range(since, until)
        if request_id and not _RID.match(request_id):
            raise ApiError(422, "gecersiz_istek_kimligi", "İstek kimliği 32 haneli onaltılık olmalı.")
        conds = ["at >= %s", "at < %s"]
        params: list[Any] = [self.since, self.until]
        if source:
            conds.append("source = %s")
            params.append(source)
        if category:
            cond, extra = _category_cond(category)
            conds.append(cond)
            params += extra
        if action:
            conds.append("action = %s")
            params.append(action)
        if actor:
            conds.append("actor ILIKE %s")
            params.append(_like(actor.strip()))
        if outcome:
            conds.append("outcome = %s")
            params.append(outcome)
        if object:
            conds.append("object ILIKE %s")
            params.append(_like(object.strip()))
        if request_id:
            conds.append("request_id = %s")
            params.append(request_id)
        if ip:
            conds.append("client_ip = %s")
            params.append(ip.strip())
        if q:
            conds.append("(actor ILIKE %s OR action ILIKE %s OR object ILIKE %s OR detail::text ILIKE %s)")
            params += [_like(q.strip())] * 4
        self.where = " AND ".join(conds)
        self.params = params
        self.echo = {k: v for k, v in {"source": source, "category": category, "action": action, "actor": actor,
                                        "outcome": outcome, "object": object, "request_id": request_id, "ip": ip,
                                        "q": q}.items() if v}


def _encode_cursor(at: datetime, source: str, ident: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([at.isoformat(), source, ident]).encode()).decode().rstrip("=")


def _decode_cursor(raw: str) -> tuple[datetime, str, int]:
    try:
        at, source, ident = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if source not in STREAMS or not isinstance(ident, int):
            raise ValueError
        return datetime.fromisoformat(at), source, ident
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ApiError(422, "gecersiz_imlec", "Sayfa imleci geçersiz; listeyi baştan yükleyin.") from None


def _object_link(obj: str | None) -> str | None:
    if not obj or ":" not in obj:
        return None
    kind, ref = obj.split(":", 1)
    if kind in ("ci", "cmdb_ci") and _SYSID.match(ref):
        return f"#/ci/{ref}"
    if kind == "task" and _SYSID.match(ref):
        return f"#/gorevler/{ref}"
    if kind == "change_set":
        return "#/sema"
    return None


class _Masker:
    """CI değişikliğinde okuyanın göremediği alanın değerini gizler (CI geçmişiyle aynı ölçüt)."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self._grants: dict[str, Any] = {}

    def visible(self, cls: str | None, attribute: str | None) -> bool:
        if not cls or attribute in (None, "sys_class_name", "relation"):
            return True
        md = self.ctx.state.metadata
        if cls not in md.class_map:
            return False
        if cls not in self._grants:
            self._grants[cls] = (self.ctx.grant(cls, "read"), md.fields_of(cls))
        grant, fields = self._grants[cls]
        return grant.can_see(fields.get(attribute, {}).get("sensitivity", "internal"))


def _event(row: Sequence[Any], masker: _Masker) -> dict[str, Any]:
    source, ident, at, actor, action, obj, outcome, detail, ip, rid = row
    label, category = action_info(source, action)
    detail = dict(detail or {})
    masked = False
    if source == "ci" and not masker.visible(detail.get("class"), detail.get("attribute")):
        detail["old"] = detail["new"] = None
        masked = True
    return {"key": f"{source}:{ident}", "source": source, "source_label": STREAMS[source], "id": ident,
            "at": at.isoformat(), "actor": actor, "action": action, "action_label": label, "category": category,
            "category_label": CATEGORIES[category], "object": obj, "object_link": _object_link(obj),
            "outcome": outcome, "outcome_label": OUTCOME_TR.get(outcome, outcome), "client_ip": ip,
            "request_id": rid, "detail": detail, "masked": masked}


def _record(ctx: Ctx, action: str, resource: str | None, detail: dict[str, Any]) -> None:
    ctx.security_event(action, "allow", resource, detail)


# ---------------------------------------------------------------------------------------------- katalog
@router.get("/catalog")
def catalog(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Süzgeç seçenekleri: akışlar, kategoriler, bilinen olay türleri."""
    ctx.require("audit", "audit_read")
    return {"streams": [{"value": k, "label": v} for k, v in STREAMS.items()],
            "categories": [{"value": k, "label": v} for k, v in CATEGORIES.items()],
            "actions": [{"value": a, "label": label, "category": cat} for a, (label, cat) in ACTIONS.items()],
            "outcomes": [{"value": k, "label": v} for k, v in OUTCOME_TR.items()],
            "can_review": ctx.grant("audit", "audit_review").allowed}


# ---------------------------------------------------------------------------------------------- olaylar
@router.get("/events")
def events(f: Filters = Depends(), ctx: Ctx = Depends(context), cursor: str | None = Query(None, max_length=200),
           limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Süzülmüş olaylar, yeniden eskiye; ``next`` ile sonraki sayfa."""
    ctx.require("audit", "audit_read")
    where, params = f.where, list(f.params)
    if cursor:
        at, source, ident = _decode_cursor(cursor)
        where += " AND (at, source, id) < (%s, %s, %s)"
        params += [at, source, ident]
    rows = ctx.conn.execute(f"SELECT {_COLUMNS} FROM audit.trail WHERE {where} "  # noqa: S608 — sabit parçalar
                            "ORDER BY at DESC, source DESC, id DESC LIMIT %s", (*params, limit + 1)).fetchall()
    masker = _Masker(ctx)
    items = [_event(r, masker) for r in rows[:limit]]
    nxt = _encode_cursor(rows[limit - 1][2], rows[limit - 1][0], rows[limit - 1][1]) if len(rows) > limit else None
    total = None
    if not cursor:
        total = ctx.conn.execute(f"SELECT count(*) FROM (SELECT 1 FROM audit.trail WHERE {f.where} "  # noqa: S608
                                 "LIMIT %s) s", (*f.params, COUNT_CAP + 1)).fetchone()[0]
    return {"items": items, "next": nxt, "total": total, "total_capped": total is not None and total > COUNT_CAP,
            "since": f.since.isoformat(), "until": f.until.isoformat()}


@router.get("/events/{source}/{ident}")
def event(source: Literal["security", "ci", "task"], ident: int = Path(ge=1, le=2**63 - 1),
          ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Tek olay ve aynı istekteki diğer olaylar (istek kimliğiyle)."""
    ctx.require("audit", "audit_read")
    row = ctx.conn.execute(f"SELECT {_COLUMNS} FROM audit.trail WHERE source = %s AND id = %s",  # noqa: S608
                           (source, ident)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "Olay bulunamadı.")
    masker = _Masker(ctx)
    out = _event(row, masker)
    related: list[dict[str, Any]] = []
    if out["request_id"]:
        related = [_event(r, masker) for r in ctx.conn.execute(
            f"SELECT {_COLUMNS} FROM audit.trail WHERE request_id = %s AND NOT (source = %s AND id = %s) "  # noqa: S608
            "ORDER BY at, id LIMIT 50", (out["request_id"], source, ident)).fetchall()]
    seal = ctx.conn.execute("""SELECT s.period_start, s.period_end, s.sealed_at,
                                      (SELECT c.ok FROM audit.seal_check c WHERE c.seal = s.id
                                        ORDER BY c.checked_at DESC LIMIT 1)
                                 FROM audit.seal s WHERE s.stream = %s AND s.period_start <= %s AND s.period_end > %s""",
                            (source, row[2], row[2])).fetchone()
    out["seal"] = ({"period_start": seal[0].isoformat(), "period_end": seal[1].isoformat(),
                    "sealed_at": seal[2].isoformat(), "last_check_ok": seal[3]} if seal else None)
    out["related"] = related
    return out


@router.get("/summary")
def summary(since: datetime | None = Query(None), until: datetime | None = Query(None),
            ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Dönem özeti: akış ve kategori başına sayı, ret sayısı, en çok ret alan ve en etkin kişiler."""
    ctx.require("audit", "audit_read")
    start, end = _range(since, until)
    return _summary(ctx, start, end)


def _summary(ctx: Ctx, start: datetime, end: datetime) -> dict[str, Any]:
    rows = ctx.conn.execute("""SELECT source, action, outcome, count(*) FROM audit.trail
                                WHERE at >= %s AND at < %s GROUP BY 1, 2, 3""", (start, end)).fetchall()
    by_source: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    denied = 0
    for source, action, outcome, n in rows:
        by_source[source] += n
        by_category[action_info(source, action)[1]] += n
        if outcome == "deny":
            denied += n
    denied_actors = ctx.conn.execute("""SELECT actor, count(*) FROM audit.trail
                                         WHERE at >= %s AND at < %s AND outcome = 'deny'
                                         GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5""", (start, end)).fetchall()
    actors = ctx.conn.execute("""SELECT actor, count(*) FROM audit.trail
                                  WHERE at >= %s AND at < %s AND source = 'security' GROUP BY 1 ORDER BY 2 DESC, 1
                                  LIMIT 5""", (start, end)).fetchall()
    return {"since": start.isoformat(), "until": end.isoformat(), "events": sum(by_source.values()), "denied": denied,
            "by_source": [{"value": k, "label": STREAMS[k], "count": by_source.get(k, 0)} for k in STREAMS],
            "by_category": [{"value": k, "label": v, "count": by_category.get(k, 0)} for k, v in CATEGORIES.items()
                            if by_category.get(k)],
            "denied_actors": [{"actor": a, "count": n} for a, n in denied_actors],
            "top_actors": [{"actor": a, "count": n} for a, n in actors]}


# ---------------------------------------------------------------------------------------------- dışa aktarma
EXPORT_COLUMNS = ("at", "source", "id", "actor", "action", "action_label", "category", "object", "outcome",
                  "client_ip", "request_id", "detail")


@router.get("/export")
def export(f: Filters = Depends(), ctx: Ctx = Depends(context),
           format: Literal["csv", "ndjson"] = Query("csv")) -> Response:
    """Süzülmüş olayları CSV (Excel'de formül çalışmaz) veya NDJSON (SIEM/arşiv) olarak indirir; en çok 50.000 kayıt.
    Dışa aktarmanın kendisi denetim olayıdır (kim, hangi süzgeçle, kaç kayıt)."""
    ctx.require("audit", "audit_read")
    rows = ctx.conn.execute(f"SELECT {_COLUMNS} FROM audit.trail WHERE {f.where} "  # noqa: S608 — sabit parçalar
                            "ORDER BY at, source, id LIMIT %s", (*f.params, MAX_EXPORT + 1)).fetchall()
    truncated = len(rows) > MAX_EXPORT
    masker = _Masker(ctx)
    items = [_event(r, masker) for r in rows[:MAX_EXPORT]]
    _record(ctx, "audit_export", "audit", {"format": format, "count": len(items), "truncated": truncated,
                                           "since": f.since.isoformat(), "until": f.until.isoformat(), **f.echo})
    stamp = f"{f.since:%Y%m%d}-{f.until:%Y%m%d}"
    headers = {"Content-Disposition": f'attachment; filename="evren-denetim-izi-{stamp}.{format}"',
               "Cache-Control": "no-store", "X-Evren-Truncated": "1" if truncated else "0"}
    if format == "ndjson":
        body = "".join(json.dumps({k: e[k] for k in EXPORT_COLUMNS}, ensure_ascii=False, sort_keys=True) + "\n"
                       for e in items)
        return Response(body, media_type="application/x-ndjson; charset=utf-8", headers=headers)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Zaman (UTC)", "Akış", "Kimlik", "Kim", "Olay türü", "Olay", "Kategori", "Nesne", "Sonuç", "Kaynak IP",
                "İstek kimliği", "Ayrıntı"])
    for e in items:
        w.writerow([safe_cell(v) for v in (e["at"], e["source_label"], e["id"], e["actor"], e["action"],
                                           e["action_label"], e["category_label"], e["object"], e["outcome_label"],
                                           e["client_ip"], e["request_id"],
                                           json.dumps(e["detail"], ensure_ascii=False, sort_keys=True))])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


# ---------------------------------------------------------------------------------------------- bütünlük
def _seal_out(s: SealRecord, check: Sequence[Any] | None) -> dict[str, Any]:
    return {"id": s.id, "stream": s.stream, "stream_label": STREAMS[s.stream], "period_start": s.period_start.isoformat(),
            "period_end": s.period_end.isoformat(), "row_count": s.row_count, "digest": s.digest, "chain": s.chain,
            "sealed_at": s.sealed_at.isoformat() if s.sealed_at else None,
            "last_check": ({"at": check[0].isoformat(), "by": check[1], "ok": check[2], "problem": check[3]}
                           if check else None)}


@router.get("/integrity")
def integrity(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Akış başına son mühür, mühürleme gecikmesi, son doğrulama ve son 30 günün sorunlu doğrulamaları."""
    ctx.require("audit", "audit_read")
    now = _now()
    streams = []
    for stream, label in STREAMS.items():
        row = ctx.conn.execute(f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s "  # noqa: S608
                               "ORDER BY period_start DESC LIMIT 1", (stream,)).fetchone()
        count = ctx.conn.execute("SELECT count(*) FROM audit.seal WHERE stream = %s", (stream,)).fetchone()[0]
        last = SealRecord.from_row(row) if row else None
        streams.append({"stream": stream, "label": label, "seals": count,
                        "sealed_until": last.period_end.isoformat() if last else None,
                        "lagging": last is None or now - last.period_end > SEAL_LAG,
                        "chain": last.chain if last else None})
    checks = ctx.conn.execute("""SELECT max(checked_at) FILTER (WHERE checked_by LIKE 'evren:%%'),
                                        max(checked_at) FILTER (WHERE checked_by NOT LIKE 'evren:%%'),
                                        count(*) FILTER (WHERE NOT ok AND checked_at > now() - interval '30 days')
                                   FROM audit.seal_check""").fetchone()
    failed = ctx.conn.execute("""SELECT c.checked_at, c.checked_by, c.problem, s.stream, s.period_start, s.period_end
                                   FROM audit.seal_check c JOIN audit.seal s ON s.id = c.seal
                                  WHERE NOT c.ok AND c.checked_at > now() - interval '30 days'
                                  ORDER BY c.checked_at DESC LIMIT 20""").fetchall()
    return {"streams": streams, "last_auto_check": checks[0].isoformat() if checks[0] else None,
            "last_manual_check": checks[1].isoformat() if checks[1] else None, "failed_30d": checks[2],
            "failed": [{"at": a.isoformat(), "by": by, "problem": p, "stream": st, "stream_label": STREAMS[st],
                        "period_start": ps.isoformat(), "period_end": pe.isoformat()}
                       for a, by, p, st, ps, pe in failed],
            "can_verify": ctx.grant("audit", "audit_review").allowed}


@router.get("/seals")
def seals(ctx: Ctx = Depends(context), stream: Literal["security", "ci", "task"] | None = Query(None),
          limit: int = Query(48, ge=1, le=500)) -> dict[str, Any]:
    """Son mühürler ve her birinin son doğrulama sonucu."""
    ctx.require("audit", "audit_read")
    cond, params = ("WHERE s.stream = %s", [stream]) if stream else ("", [])
    cols = ", ".join(f"s.{c.strip()}" for c in SealRecord.SQL_COLUMNS.split(","))
    rows = ctx.conn.execute(f"""SELECT {cols}, c.checked_at, c.checked_by, c.ok, c.problem
                                  FROM audit.seal s
                                  LEFT JOIN LATERAL (SELECT checked_at, checked_by, ok, problem FROM audit.seal_check
                                                      WHERE seal = s.id ORDER BY checked_at DESC LIMIT 1) c ON true
                                  {cond} ORDER BY s.period_start DESC, s.stream LIMIT %s""",  # noqa: S608
                            (*params, limit)).fetchall()
    return {"items": [_seal_out(SealRecord.from_row(r[:9]), r[9:] if r[9] else None) for r in rows]}


def _verify(ctx: Ctx, start: datetime, end: datetime, streams: Sequence[str]) -> list[tuple[SealRecord, str | None]]:
    """Dönemle kesişen mühürleri dönemin bugünkü kayıtlarıyla doğrular ve sonucu kanıt olarak yazar."""
    results: list[tuple[SealRecord, str | None]] = []
    actor = ctx.principal.audit_id

    def rows_for(seal: SealRecord) -> Iterator[Sequence[Any]]:
        yield from ctx.conn.cursor().stream(SEAL_SQL[seal.stream], (seal.period_start, seal.period_end))

    for stream in streams:
        items = [SealRecord.from_row(r) for r in ctx.conn.execute(
            f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s AND period_end > %s "  # noqa: S608
            "AND period_start < %s ORDER BY period_start", (stream, start, end)).fetchall()]
        if not items:
            continue
        prev_row = ctx.conn.execute(f"SELECT {SealRecord.SQL_COLUMNS} FROM audit.seal WHERE stream = %s "  # noqa: S608
                                    "AND period_start < %s ORDER BY period_start DESC LIMIT 1",
                                    (stream, items[0].period_start)).fetchone()
        previous = SealRecord.from_row(prev_row) if prev_row else None  # yoksa zincirin başı (GENESIS beklenir)
        for seal, problem in verify_chain(items, previous, rows_for):
            ctx.conn.execute("INSERT INTO audit.seal_check (seal, checked_by, ok, problem) VALUES (%s, %s, %s, %s)",
                             (seal.id, actor, problem is None, problem))
            results.append((seal, problem))
    return results


class VerifyIn(BaseModel):
    since: datetime
    until: datetime
    stream: Literal["security", "ci", "task"] | None = None


@router.post("/seals/verify", dependencies=[Depends(require_csrf)])
def verify(body: VerifyIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Dönemin mühürlerini yeniden hesaplayıp karşılaştırır; her mühür için sonuç kanıt olarak kaydedilir."""
    ctx.require("audit", "audit_review")
    start, end = _range(body.since, body.until, limit=MAX_VERIFY_RANGE)
    results = _verify(ctx, start, end, [body.stream] if body.stream else list(STREAMS))
    bad = [(s, p) for s, p in results if p]
    _record(ctx, "audit_verify", "audit", {"since": start.isoformat(), "until": end.isoformat(),
                                           "stream": body.stream, "checked": len(results), "failed": len(bad)})
    if not results:
        message = "Bu dönemde mühür yok: dönem henüz mühürlenmedi (son saat ve bekleme süresi) veya kayıt öncesi."
    elif bad:
        message = f"{len(results)} mühürden {len(bad)} tanesi doğrulanamadı; ayrıntı aşağıda. Bulgu kaydı açın."
    else:
        message = f"{len(results)} mühür doğrulandı; dönemin kayıtları mühürlendiği andaki gibi."
    return {"checked": len(results), "failed": len(bad), "ok": not bad and bool(results), "message": message,
            "problems": [{"stream": s.stream, "stream_label": STREAMS[s.stream], "period_start": s.period_start.isoformat(),
                          "period_end": s.period_end.isoformat(), "problem": p} for s, p in bad]}


# ---------------------------------------------------------------------------------------------- inceleme
class ReviewIn(BaseModel):
    period_start: datetime
    period_end: datetime
    outcome: Literal["no_findings", "findings"]
    note: str = Field(min_length=10, max_length=4000)
    idempotency_key: str = Field(pattern=_KEY)


@router.get("/reviews")
def reviews(ctx: Ctx = Depends(context), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Kayıtlı denetim izi incelemeleri (kanıt) ve bir sonraki incelemenin zamanı."""
    ctx.require("audit", "audit_read")
    rows = ctx.conn.execute("""SELECT id, reviewed_at, reviewer, period_start, period_end, events, denied, seals_checked,
                                      seals_ok, outcome, note
                                 FROM audit.review ORDER BY reviewed_at DESC LIMIT %s""", (limit,)).fetchall()
    items = [{"id": i, "reviewed_at": at.isoformat(), "reviewer": who, "period_start": ps.isoformat(),
              "period_end": pe.isoformat(), "events": ev, "denied": dn, "seals_checked": sc, "seals_ok": ok,
              "outcome": oc, "outcome_label": REVIEW_OUTCOME_TR[oc], "note": note}
             for i, at, who, ps, pe, ev, dn, sc, ok, oc, note in rows]
    last = rows[0][1] if rows else None
    due = last is None or _now() - last > REVIEW_EVERY
    suggest_start = rows[0][4] if rows else _now() - REVIEW_EVERY
    return {"items": items, "due": due, "every_days": REVIEW_EVERY.days,
            "suggested": {"period_start": suggest_start.isoformat(), "period_end": _now().isoformat()},
            "can_review": ctx.grant("audit", "audit_review").allowed}


@router.post("/reviews", dependencies=[Depends(require_csrf)], status_code=201)
def create_review(body: ReviewIn, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Denetçi bir dönemi inceler: önce dönemin mühürleri doğrulanır, olay ve ret sayıları sabitlenir, sonra karar ve not
    kaydedilir. Mühür sorunu varken "bulgu yok" kaydedilmez."""
    ctx.require("audit", "audit_review")
    start, end = _range(body.period_start, body.period_end, limit=MAX_VERIFY_RANGE)
    if end > _now() + timedelta(minutes=5):
        raise ApiError(422, "gelecek_donem", "İncelenen dönem gelecekte bitemez.")
    note = body.note.strip()
    if len(note) < 10:
        raise ApiError(422, "not_gerekli", "İnceleme notu en az 10 karakter olmalı: neye bakıldı, ne görüldü.")
    actor = ctx.principal.audit_id
    digest = request_hash("review", start.isoformat(), end.isoformat(), body.outcome, note)
    with ctx.conn.cursor() as cur:
        prior = replay(cur, actor, body.idempotency_key, digest)
    if prior is not None:
        return prior
    # Doğrulama kanıtı inceleme reddedilse de kalır (işlem dışında yazılır)
    results = _verify(ctx, start, end, list(STREAMS))
    seals_ok = all(p is None for _s, p in results)
    if not seals_ok and body.outcome == "no_findings":
        raise ApiError(422, "muhur_sorunu", "Dönemin mühürlerinden en az biri doğrulanamadı; inceleme sonucu "
                                            "\"Bulgu var\" olmalı ve notta sorunu açıklamalısınız.")
    s = _summary(ctx, start, end)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        cur.execute("""INSERT INTO audit.review (reviewer, period_start, period_end, events, denied, seals_checked,
                                                 seals_ok, outcome, note)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, reviewed_at""",
                    (actor, start, end, s["events"], s["denied"], len(results), seals_ok, body.outcome, note))
        rid, at = cur.fetchone()
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'audit_review', %s, 'allow', %s)",
                    (actor, f"audit_review:{rid}", Jsonb({"period_start": start.isoformat(),
                                                          "period_end": end.isoformat(), "outcome": body.outcome,
                                                          "events": s["events"], "denied": s["denied"],
                                                          "seals_checked": len(results), "seals_ok": seals_ok})))
        response = {"id": rid, "reviewed_at": at.isoformat(), "events": s["events"], "denied": s["denied"],
                    "seals_checked": len(results), "seals_ok": seals_ok, "outcome": body.outcome,
                    "message": "İnceleme kaydedildi; kanıt olarak denetim izinde ve SIEM'de."}
        remember(cur, actor, body.idempotency_key, "/api/v1/audit/reviews", digest, 201, response)
    return JSONResponse(response, status_code=201)

