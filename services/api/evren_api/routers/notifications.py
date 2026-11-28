"""Bildirim merkezi ve platform uyarıları (U8.9, ADR-0040; F1-15).

Bildirimler:

* Herkes kendi bildirimlerini görür: kendisine (``ref.sys_user``), üyesi olduğu gruba veya sahip olduğu role
  adreslenenler. Okundu bilgisi kişi başınadır (rol bildirimini her alıcı ayrı okur).
* Yönlendirme (rol veya grup → e-posta adresi / Teams hedef adı), sınama bildirimi ve teslim geçmişi: ``platform``
  okuma izniyle görülür, ``platform/ops_admin`` (Platform operatörü, Platform yöneticisi) ile değiştirilir.

Uyarılar: ``platform`` okuma izniyle görülür; ateşlenmiş uyarıyı ``ops_admin`` gerekçeyle onaylar (bir kez). Uyarıyı
API açmaz, çözmez: görev çalışanı kurallarla değerlendirir.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from evren_core.notify import KINDS, valid_email
from evren_core.ops import SEVERITY_TR, SIGNALS, load_rules

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["bildirim"])

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
STATE_TR = {"pending": "Bekliyor", "firing": "Etkin", "resolved": "Çözüldü"}
DELIVERY_TR = {"pending": "Gönderilecek", "sent": "Gönderildi", "failed": "Gönderilemedi", "skipped": "Atlandı"}
_SYSID_LEN = 32


def _audience(ctx: Ctx) -> tuple[str | None, list[str], list[str]]:
    user = ctx.sys_user()
    uid = user["sys_id"] if user else None
    groups = [r[0] for r in ctx.conn.execute('SELECT "group" FROM ref.sys_user_grmember WHERE "user" = %s',
                                             (uid,)).fetchall()] if uid else []
    return uid, groups, sorted(ctx.principal.roles)


_MINE = """((n.audience_kind = 'user' AND n.audience = %(uid)s) OR (n.audience_kind = 'group' AND n.audience = ANY(%(groups)s))
            OR (n.audience_kind = 'role' AND n.audience = ANY(%(roles)s)))"""


def _params(ctx: Ctx) -> dict[str, Any]:
    uid, groups, roles = _audience(ctx)
    return {"uid": uid or "", "groups": groups, "roles": roles, "me": ctx.principal.audit_id}


def _unread(ctx: Ctx, p: dict[str, Any]) -> tuple[int, int]:
    row = ctx.conn.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE n.severity = 'critical') FROM notify.notification n
             WHERE {_MINE} AND n.created_at > now() - interval '90 days'
               AND NOT EXISTS (SELECT 1 FROM notify.receipt r WHERE r.notification = n.sys_id AND r.principal = %(me)s)""",  # noqa: S608
        p).fetchone()
    return row[0], row[1]


# ---------------------------------------------------------------------------------------------- gelen kutusu
@router.get("/notifications")
def notifications(ctx: Ctx = Depends(context), unread_only: bool = Query(False),
                  before: int | None = Query(None, ge=1), limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    """Bana, grubuma ve rollerime gelen bildirimler (son 90 gün), yeniden eskiye."""
    p = {**_params(ctx), "before": before or 2**62, "limit": limit + 1}
    cond = "AND r.principal IS NULL" if unread_only else ""
    rows = ctx.conn.execute(
        f"""SELECT n.seq, n.sys_id, n.created_at, n.kind, n.severity, n.title, n.body, n.link, n.audience_kind,
                   r.read_at
              FROM notify.notification n
              LEFT JOIN notify.receipt r ON r.notification = n.sys_id AND r.principal = %(me)s
             WHERE {_MINE} AND n.created_at > now() - interval '90 days' AND n.seq < %(before)s {cond}
             ORDER BY n.seq DESC LIMIT %(limit)s""", p).fetchall()  # noqa: S608 — sabit parçalar
    items = [{"seq": seq, "sys_id": sid, "created_at": at.isoformat(), "kind": kind,
              "kind_label": KINDS.get(kind, (kind, ""))[0], "severity": sev, "severity_label": SEVERITY_TR[sev],
              "title": title, "body": body, "link": link, "via": aud, "read": read_at is not None}
             for seq, sid, at, kind, sev, title, body, link, aud, read_at in rows[:limit]]
    unread, critical = _unread(ctx, p)
    return {"items": items, "next": rows[limit - 1][0] if len(rows) > limit else None, "unread": unread,
            "critical_unread": critical}


@router.get("/notifications/count")
def notification_count(ctx: Ctx = Depends(context)) -> dict[str, int]:
    """Üst çubuktaki zil için okunmamış sayısı."""
    unread, critical = _unread(ctx, _params(ctx))
    return {"unread": unread, "critical_unread": critical}


class ReadIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/notifications/read", dependencies=[Depends(require_csrf)])
def mark_read(body: ReadIn, ctx: Ctx = Depends(context)) -> dict[str, int]:
    """Yalnızca bana görünen bildirimler okundu işaretlenir (başkasınınki sessizce atlanır)."""
    ids = [i for i in body.ids if len(i) == _SYSID_LEN and i.isalnum()]
    p = {**_params(ctx), "ids": ids}
    n = ctx.conn.execute(
        f"""INSERT INTO notify.receipt (notification, principal)
            SELECT n.sys_id, %(me)s FROM notify.notification n WHERE n.sys_id = ANY(%(ids)s) AND {_MINE}
            ON CONFLICT DO NOTHING""", p).rowcount  # noqa: S608
    return {"marked": n, "unread": _unread(ctx, p)[0]}


@router.post("/notifications/read-all", dependencies=[Depends(require_csrf)])
def mark_all_read(ctx: Ctx = Depends(context)) -> dict[str, int]:
    p = _params(ctx)
    n = ctx.conn.execute(
        f"""INSERT INTO notify.receipt (notification, principal)
            SELECT n.sys_id, %(me)s FROM notify.notification n
             WHERE {_MINE} AND n.created_at > now() - interval '90 days' ON CONFLICT DO NOTHING""", p).rowcount  # noqa: S608
    return {"marked": n, "unread": 0}


# ---------------------------------------------------------------------------------------------- yönlendirme
def _channels(ctx: Ctx) -> dict[str, Any]:
    """Görev çalışanının bildirdiği kanal durumu (sır yok: Teams'in yalnızca hedef adları)."""
    row = ctx.conn.execute("""SELECT details FROM ingest.worker_heartbeat
                               WHERE kind = 'governance' AND stopped_at IS NULL
                                 AND seen_at > now() - greatest(3 * poll_seconds, 120) * interval '1 second'
                               ORDER BY seen_at DESC LIMIT 1""").fetchone()
    d = (row[0] or {}).get("notify") if row else None
    if not d:
        return {"live": False, "email": "bilinmiyor", "teams": "bilinmiyor", "teams_targets": [], "pending": None,
                "failed_24h": None}
    return {"live": True, "email": d.get("email"), "teams": d.get("teams"), "teams_targets": d.get("teams_targets") or [],
            "pending": d.get("pending"), "failed_24h": d.get("failed_24h")}


@router.get("/notifications/routes")
def routes(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Yönlendirmeler, kanal durumu, seçilebilecek roller ve gruplar, son başarısız teslimler."""
    ctx.require("platform", "read")
    rows = ctx.conn.execute("""SELECT audience_kind, audience, channel, target, min_severity, active, updated_by,
                                      updated_at FROM notify.route ORDER BY audience_kind, audience, channel, target"""
                            ).fetchall()
    roles = dict(ctx.conn.execute("SELECT name, label_tr FROM iam.role ORDER BY label_tr").fetchall())
    groups = dict(ctx.conn.execute("SELECT sys_id, name FROM ref.sys_user_group WHERE active ORDER BY name LIMIT 500"
                                   ).fetchall())
    failed = ctx.conn.execute(
        """SELECT d.channel, d.target, d.attempts, d.last_error, d.created_at, n.title FROM notify.delivery d
             JOIN notify.notification n ON n.sys_id = d.notification
            WHERE d.status = 'failed' OR (d.status = 'pending' AND d.attempts > 0)
            ORDER BY d.created_at DESC LIMIT 20""").fetchall()
    return {"routes": [{"audience_kind": k, "audience": a,
                        "audience_label": roles.get(a, a) if k == "role" else groups.get(a, a), "channel": c,
                        "target": t, "min_severity": m, "min_severity_label": SEVERITY_TR[m], "active": act,
                        "updated_by": by, "updated_at": at.isoformat()}
                       for k, a, c, t, m, act, by, at in rows],
            "channels": _channels(ctx),
            "roles": [{"value": k, "label": v} for k, v in roles.items()],
            "groups": [{"value": k, "label": v} for k, v in groups.items()],
            "problems": [{"channel": c, "target": t, "attempts": n, "error": e, "at": at.isoformat(), "title": title}
                         for c, t, n, e, at, title in failed],
            "can_admin": ctx.grant("platform", "ops_admin").allowed}


class RouteIn(BaseModel):
    audience_kind: Literal["role", "group"]
    audience: str = Field(min_length=1, max_length=80)
    channel: Literal["email", "teams"]
    target: str = Field(min_length=3, max_length=254)
    min_severity: Literal["info", "warning", "critical"] = "info"
    active: bool = True


def _check_audience(ctx: Ctx, kind: str, audience: str) -> None:
    if kind == "role":
        ok = ctx.conn.execute("SELECT 1 FROM iam.role WHERE name = %s", (audience,)).fetchone()
    else:
        ok = ctx.conn.execute("SELECT 1 FROM ref.sys_user_group WHERE sys_id = %s AND active", (audience,)).fetchone()
    if not ok:
        raise ApiError(422, "alici_yok", "Seçilen rol veya grup bulunamadı.")


@router.put("/notifications/routes", dependencies=[Depends(require_csrf)])
def put_route(body: RouteIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Yönlendirme ekler veya günceller (önem eşiği, etkinlik). Silme yok: etkinliği kapatılır (geçmiş kalır)."""
    ctx.require("platform", "ops_admin")
    _check_audience(ctx, body.audience_kind, body.audience)
    target = body.target.strip()
    if body.channel == "email" and not valid_email(target):
        raise ApiError(422, "eposta_gecersiz", "Geçerli bir e-posta adresi yazın (ör. platform-ekibi@kurum.local).")
    if body.channel == "teams":
        target = target.lower()
        known = _channels(ctx)["teams_targets"]
        if body.active and known and target not in known:
            raise ApiError(422, "teams_hedef_yok", f"Teams hedefi tanımsız. Görev çalışanının bildirdiği hedefler: "
                                                   f"{', '.join(known)}.")
    me = ctx.principal.audit_id
    with ctx.conn.transaction():
        ctx.conn.execute(
            """INSERT INTO notify.route (audience_kind, audience, channel, target, min_severity, active, updated_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (audience_kind, audience, channel, target) DO UPDATE
                 SET min_severity = EXCLUDED.min_severity, active = EXCLUDED.active, updated_by = EXCLUDED.updated_by,
                     updated_at = now()""",
            (body.audience_kind, body.audience, body.channel, target, body.min_severity, body.active, me))
        ctx.conn.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                         "VALUES (%s, 'notify_route_update', %s, 'allow', %s)",
                         (me, f"{body.audience_kind}:{body.audience}",
                          Jsonb({"channel": body.channel, "target": target, "min_severity": body.min_severity,
                                 "active": body.active})))
    return {"message": "Yönlendirme kaydedildi; görev çalışanı bir sonraki bildirimden itibaren uygular."
            if body.active else "Yönlendirme kapatıldı."}


class TestIn(BaseModel):
    audience_kind: Literal["role", "group"]
    audience: str = Field(min_length=1, max_length=80)


@router.post("/notifications/test", dependencies=[Depends(require_csrf)], status_code=202)
def send_test(body: TestIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Seçilen rol veya gruba sınama bildirimi: uygulama içinde ve yönlendirilmiş kanallarda görünmeli."""
    ctx.require("platform", "ops_admin")
    _check_audience(ctx, body.audience_kind, body.audience)
    me = ctx.principal.audit_id
    with ctx.conn.transaction():
        ctx.conn.execute(
            """INSERT INTO notify.notification (kind, severity, audience_kind, audience, title, body, link, source,
                                                dedup_key, created_by)
               VALUES ('test', 'info', %s, %s, 'Sınama bildirimi', %s, '#/sistem-durumu/bildirimler', 'test', %s, %s)""",
            (body.audience_kind, body.audience,
             f"{ctx.principal.name} bildirim kanallarını sınıyor. Bu iletiyi aldıysanız yönlendirme çalışıyor.",
             f"test:{uuid.uuid4().hex}", me))
        ctx.conn.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                         "VALUES (%s, 'notify_test', %s, 'allow', '{}')", (me, f"{body.audience_kind}:{body.audience}"))
    return {"message": "Sınama bildirimi oluşturuldu; görev çalışanı bir dakika içinde yönlendirilmiş kanallara "
                       "gönderir. Sonucu aşağıdaki teslim sorunları listesinden izleyin."}


# ---------------------------------------------------------------------------------------------- uyarılar
@router.get("/ops/alerts")
def alerts(ctx: Ctx = Depends(context), view: Literal["active", "history"] = Query("active"),
           limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Etkin (ateşlenmiş, çözülmemiş) uyarılar ya da son 30 günün geçmişi; kural kataloğu ve kanal durumu."""
    ctx.require("platform", "read")
    rules = {r.id: r for r in load_rules(ctx.state.metadata.alerts)}
    cond = ("state = 'firing'" if view == "active"
            else "fired_at IS NOT NULL AND fired_at > now() - interval '30 days'")
    rows = ctx.conn.execute(
        f"""SELECT sys_id, rule, subject, severity, state, title, detail, value, fired_at, last_seen_at, resolved_at,
                   acked_by, acked_at, ack_note
              FROM ops.alert WHERE {cond}
             ORDER BY CASE state WHEN 'firing' THEN 0 ELSE 1 END, fired_at DESC LIMIT %s""",  # noqa: S608
        (limit,)).fetchall()
    items = []
    for sid, rule, subject, sev, state, title, detail, value, fired, seen, resolved, by, at, note in rows:
        r = rules.get(rule)
        items.append({"sys_id": sid, "rule": rule, "subject": subject, "severity": sev,
                      "severity_label": SEVERITY_TR[sev], "state": state, "state_label": STATE_TR[state],
                      "title": title, "detail": detail, "value": float(value) if value is not None else None,
                      "advice": r.advice if r else "", "fired_at": fired.isoformat() if fired else None,
                      "last_seen_at": seen.isoformat(), "resolved_at": resolved.isoformat() if resolved else None,
                      "ack": {"by": by, "at": at.isoformat(), "note": note} if by else None})
    items.sort(key=lambda a: (a["state"] != "firing", SEVERITY_ORDER[a["severity"]]))
    return {"items": items, "can_ack": ctx.grant("platform", "ops_admin").allowed, "channels": _channels(ctx),
            "rules": [{"id": r.id, "signal": SIGNALS[r.signal][0], "title": r.title, "severity": r.severity,
                       "severity_label": SEVERITY_TR[r.severity], "for_minutes": r.for_minutes,
                       "threshold": r.threshold if SIGNALS[r.signal][1] else None, "unit": SIGNALS[r.signal][1],
                       "audience": list(r.audience)} for r in rules.values()]}


class AckIn(BaseModel):
    note: str = Field(min_length=10, max_length=1000)


@router.post("/ops/alerts/{sys_id}/ack", dependencies=[Depends(require_csrf)])
def ack_alert(sys_id: str, body: AckIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Ateşlenmiş uyarıyı gerekçeyle onaylar ("biliyoruz, ilgileniyoruz"); onay uyarıyı kapatmaz, koşul kalkınca
    görev çalışanı çözer."""
    ctx.require("platform", "ops_admin")
    note = body.note.strip()
    if len(note) < 10:
        raise ApiError(422, "gerekce_gerekli", "Onay notu en az 10 karakter olmalı: ne yapılıyor, kim ilgileniyor.")
    me = ctx.principal.audit_id
    with ctx.conn.transaction():
        row = ctx.conn.execute(
            """UPDATE ops.alert SET acked_by = %s, acked_at = now(), ack_note = %s
                WHERE sys_id = %s AND fired_at IS NOT NULL AND acked_by IS NULL RETURNING title""",
            (me, note, sys_id)).fetchone()
        if row is None:
            raise ApiError(409, "onaylanamaz", "Uyarı bulunamadı, ateşlenmemiş veya zaten onaylanmış.")
        ctx.conn.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                         "VALUES (%s, 'alert_ack', %s, 'allow', %s)", (me, f"alert:{sys_id}", Jsonb({"note": note})))
    return {"message": f"Uyarı onaylandı: {row[0]}. Koşul kalkınca kendiliğinden çözülür."}
