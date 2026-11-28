"""ServiceNow replikası (U9.1, ADR-0042; F2-01).

Okuma: ``platform`` okuma izni veya ``platform/replica_admin`` (Konfigürasyon yöneticisi, Platform yöneticisi).
Yazma (yalnızca ``platform/replica_admin``, gerekçeyle, denetim izine):

* Mod: kapalı → gölge → etkin. Etkin moda geçmek için en az bir tamamlanmış gölge turu gerekir.
* Kapsam: sınıfı replikaya alma / çıkarma.
* Bekletilen toplu itmeyi onaylama (bir kez).
* Çelişki kararı: ServiceNow'un bulduğu kaydı kabul et (eşleme o kayda bağlanır), dışarıda tut veya yeniden dene.

ServiceNow'a API yazmaz; sn-sync çalışanı yazar. Değişiklikten sonra çalışan ``NOTIFY evren_replica`` ile uyandırılır.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from evren_core.replica import MODES, STATES

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["replika"])

SHADOW_TR = {"ok": "Uygun", "conflict": "Çelişki", "error": "Hata"}
OPERATION_TR = {"INSERT": "Yeni kayıt", "UPDATE": "Güncelleme", "NO_CHANGE": "Değişiklik yok",
                "UPDATE_WITH_UPGRADE": "Güncelleme (alt sınıfa)", "UPDATE_WITH_DOWNGRADE": "Güncelleme (üst sınıfa)",
                "UPDATE_WITH_SWITCH": "Güncelleme (sınıf değişti)"}
RUN_TR = {"running": "Sürüyor", "done": "Tamamlandı", "failed": "Başarısız", "held": "Onay bekliyor",
          "approved": "Onaylandı, gönderiliyor"}
VIEWS = ("attention", "conflict", "error", "shadow", "excluded", "in_sync")


def _can_view(ctx: Ctx) -> bool:
    return ctx.grant("platform", "read").allowed or ctx.grant("platform", "replica_admin").allowed


def _require_view(ctx: Ctx) -> None:
    if not _can_view(ctx):
        ctx.require("platform", "read")


def _note(note: str, what: str) -> str:
    text = note.strip()
    if len(text) < 10:
        raise ApiError(422, "gerekce_gerekli", f"{what} için en az 10 karakterlik bir gerekçe yazın.")
    return text


def _audit(ctx: Ctx, action: str, resource: str, detail: dict[str, Any]) -> None:
    ctx.conn.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                     "VALUES (%s, %s, %s, 'allow', %s)", (ctx.principal.audit_id, action, resource, Jsonb(detail)))


@router.get("/replica")
def replica(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    _require_view(ctx)
    c = ctx.conn
    mode, max_changes, by, at, note = c.execute(
        "SELECT mode, max_changes, updated_by, updated_at, note FROM sync.replica_setting WHERE id = 1").fetchone()
    states = dict(c.execute("SELECT state, count(*) FROM sync.sn_ci_map GROUP BY state").fetchall())
    shadow = dict(c.execute("SELECT shadow_verdict, count(*) FROM sync.sn_ci_map WHERE shadow_verdict IS NOT NULL "
                            "GROUP BY shadow_verdict").fetchall())
    rels = dict(c.execute("SELECT state, count(*) FROM sync.sn_rel_map GROUP BY state").fetchall())
    hb = c.execute("""SELECT details, seen_at, seen_at > now() - make_interval(secs => greatest(poll_seconds, 30) * 3)
                        FROM ingest.worker_heartbeat WHERE kind = 'sn_sync' AND stopped_at IS NULL
                       ORDER BY seen_at DESC LIMIT 1""").fetchone()
    runs = c.execute(
        """SELECT id, mode, status, started_at, finished_at, candidates, sent, inserted, updated, unchanged, conflicts,
                  errors, relations, held_reason, approved_by, approved_at, last_error
             FROM sync.replica_run ORDER BY id DESC LIMIT 10""").fetchall()
    keys = ("id", "mode", "status", "started_at", "finished_at", "candidates", "sent", "inserted", "updated",
            "unchanged", "conflicts", "errors", "relations", "held_reason", "approved_by", "approved_at", "last_error")
    run_list = [{**dict(zip(keys, r, strict=True)), "mode_label": MODES[r[1]], "status_label": RUN_TR[r[2]]}
                for r in runs]
    held = next((r for r in run_list if r["status"] in ("held", "approved")), None)
    scope = c.execute(
        """SELECT s.sys_class_name, coalesce(o.label_tr, s.sys_class_name), s.enabled, s.include_descendants,
                  coalesce(s.notes, ''),
                  (SELECT count(*) FROM cmdb.cmdb_ci ci JOIN meta.class_closure cc ON cc.descendant = ci.sys_class_name
                    WHERE cc.ancestor = s.sys_class_name AND (s.include_descendants OR cc.depth = 0)
                      AND ci.duplicate_of IS NULL)
             FROM sync.sn_sync_scope s LEFT JOIN meta.sys_db_object o ON o.name = s.sys_class_name
            ORDER BY 2""").fetchall()
    shadow_done = c.execute("SELECT EXISTS (SELECT 1 FROM sync.replica_run WHERE mode = 'shadow' AND status = 'done')"
                            ).fetchone()[0]
    return {
        "setting": {"mode": mode, "mode_label": MODES[mode], "max_changes": max_changes, "updated_by": by,
                    "updated_at": at, "note": note},
        "worker": {"live": bool(hb and hb[2]), "seen_at": hb[1] if hb else None,
                   "connection": (hb[0] or {}).get("connection") if hb else None,
                   "source": (hb[0] or {}).get("source") if hb else None},
        "counts": {k: int(states.get(k, 0)) for k in STATES},
        "shadow": {k: int(shadow.get(k, 0)) for k in SHADOW_TR},
        "relations": {k: int(rels.get(k, 0)) for k in ("in_sync", "error", "removed")},
        "open_run": held,
        "runs": run_list,
        "scope": [{"class": r[0], "label": r[1], "enabled": r[2], "include_descendants": r[3], "notes": r[4],
                   "ci_count": int(r[5])} for r in scope],
        "shadow_done": bool(shadow_done),
        "can_admin": ctx.grant("platform", "replica_admin").allowed,
        "labels": {"modes": MODES, "states": STATES, "shadow": SHADOW_TR},
    }


@router.get("/replica/items")
def replica_items(ctx: Ctx = Depends(context), view: Literal[VIEWS] = Query("attention"),  # type: ignore[valid-type]
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100000)) -> dict[str, Any]:
    """Karar bekleyen (çelişki, hata, gölgede çelişki/hata) veya duruma göre kayıtlar."""
    _require_view(ctx)
    cond = {
        "attention": "(m.state IN ('conflict', 'error') OR (m.state = 'pending_push' AND m.shadow_verdict IN "
                     "('conflict', 'error')))",
        "conflict": "m.state = 'conflict'", "error": "m.state = 'error'",
        "shadow": "m.shadow_verdict IN ('conflict', 'error')", "excluded": "m.state = 'excluded'",
        "in_sync": "m.state = 'in_sync'",
    }[view]
    rows = ctx.conn.execute(
        f"""SELECT m.evren_sys_id, c.name, c.sys_class_name, coalesce(o.label_tr, c.sys_class_name), m.state,
                   m.shadow_verdict, m.shadow_operation, m.last_operation, m.sn_sys_id, m.identified_sn_sys_id,
                   m.message, m.skipped_fields, m.attempts, m.next_try_at, m.updated_at, m.resolved_by, m.resolve_note,
                   count(*) OVER ()
              FROM sync.sn_ci_map m JOIN cmdb.cmdb_ci c ON c.sys_id = m.evren_sys_id
              LEFT JOIN meta.sys_db_object o ON o.name = c.sys_class_name
             WHERE {cond}
             ORDER BY m.updated_at DESC, m.evren_sys_id LIMIT %s OFFSET %s""",  # nosec B608  # noqa: S608 - sabit koşullar
        (limit, offset)).fetchall()
    items = [{"sys_id": r[0], "name": r[1] or "(adsız)", "class": r[2], "class_label": r[3], "state": r[4],
              "state_label": STATES.get(r[4], r[4]), "shadow_verdict": r[5],
              "shadow_label": SHADOW_TR.get(r[5] or "", ""), "shadow_operation": OPERATION_TR.get(r[6] or "", r[6]),
              "operation": OPERATION_TR.get(r[7] or "", r[7]), "sn_sys_id": r[8], "identified_sn_sys_id": r[9],
              "message": r[10], "skipped_fields": r[11] or [], "attempts": r[12], "next_try_at": r[13],
              "updated_at": r[14], "resolved_by": r[15], "resolve_note": r[16]} for r in rows]
    return {"items": items, "total": int(rows[0][17]) if rows else 0, "view": view}


class SettingIn(BaseModel):
    mode: Literal["off", "shadow", "active"]
    max_changes: int = Field(ge=1, le=100000)
    note: str = Field(max_length=1000)


@router.put("/replica/settings", dependencies=[Depends(require_csrf)])
def put_setting(body: SettingIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("platform", "replica_admin")
    note = _note(body.note, "Replika ayarı değişikliği")
    with ctx.conn.transaction():
        old = ctx.conn.execute("SELECT mode, max_changes FROM sync.replica_setting WHERE id = 1 FOR UPDATE").fetchone()
        if body.mode == "active" and old[0] != "active":
            done = ctx.conn.execute("SELECT EXISTS (SELECT 1 FROM sync.replica_run WHERE mode = 'shadow' "
                                    "AND status = 'done')").fetchone()[0]
            if not done:
                raise ApiError(409, "golge_gerekli", "Etkin moda geçmeden önce gölge modda en az bir tur tamamlanmalı: "
                                                     "ServiceNow'un kayıtları nasıl tanıyacağını önce görün.")
        ctx.conn.execute("""UPDATE sync.replica_setting SET mode = %s, max_changes = %s, updated_by = %s,
                                   updated_at = now(), note = %s WHERE id = 1""",
                         (body.mode, body.max_changes, ctx.principal.audit_id, note))
        _audit(ctx, "replica_setting", "replica:setting", {"from": {"mode": old[0], "max_changes": old[1]},
                                                          "to": {"mode": body.mode, "max_changes": body.max_changes},
                                                          "note": note})
        ctx.conn.execute("NOTIFY evren_replica")
    return {"message": f"Replika modu: {MODES[body.mode]}. Tur başına sınır {body.max_changes} CI."}


class ScopeIn(BaseModel):
    enabled: bool
    note: str = Field(max_length=1000)


@router.put("/replica/scope/{class_name}", dependencies=[Depends(require_csrf)])
def put_scope(class_name: str, body: ScopeIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("platform", "replica_admin")
    note = _note(body.note, "Kapsam değişikliği")
    with ctx.conn.transaction():
        row = ctx.conn.execute("UPDATE sync.sn_sync_scope SET enabled = %s WHERE sys_class_name = %s "
                               "RETURNING sys_class_name", (body.enabled, class_name)).fetchone()
        if row is None:
            raise ApiError(404, "kapsamda_yok", "Bu sınıf replika kapsam listesinde yok.")
        _audit(ctx, "replica_scope", f"replica:scope:{class_name}", {"enabled": body.enabled, "note": note})
        ctx.conn.execute("NOTIFY evren_replica")
    return {"message": f"{class_name} replikaya {'alındı' if body.enabled else 'gönderilmeyecek'}."}


class NoteIn(BaseModel):
    note: str = Field(max_length=1000)


@router.post("/replica/runs/{run_id}/approve", dependencies=[Depends(require_csrf)])
def approve_run(run_id: int, body: NoteIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Toplu değişiklik sınırını aşan bekletilmiş itmeyi onaylar (bir kez). Çalışan bekleyenleri gönderir."""
    ctx.require("platform", "replica_admin")
    note = _note(body.note, "Toplu itme onayı")
    if not 0 < run_id < 2**62:
        raise ApiError(404, "bulunamadi", "Çalışma bulunamadı.")
    with ctx.conn.transaction():
        row = ctx.conn.execute(
            """UPDATE sync.replica_run SET status = 'approved', approved_by = %s, approved_at = now(), approve_note = %s
                WHERE id = %s AND status = 'held' RETURNING candidates""",
            (ctx.principal.audit_id, note, run_id)).fetchone()
        if row is None:
            raise ApiError(409, "onaylanamaz", "Çalışma bulunamadı, bekletilmiş değil veya zaten onaylandı.")
        _audit(ctx, "replica_approve", f"replica:run:{run_id}", {"candidates": row[0], "note": note})
        ctx.conn.execute("NOTIFY evren_replica")
    return {"message": f"Onaylandı: {row[0]} CI değişikliği ServiceNow'a gönderilecek."}


class ResolveIn(BaseModel):
    action: Literal["accept", "exclude", "retry"]
    note: str = Field(max_length=1000)


@router.post("/replica/items/{sys_id}/resolve", dependencies=[Depends(require_csrf)])
def resolve_item(sys_id: str, body: ResolveIn, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """Çelişki veya hata kararı.

    * ``accept``: ServiceNow'un bulduğu kayıt bu CI'dır; eşleme o kayda bağlanır, sonraki turda doğrulanıp yazılır.
      Kayıt başka bir Evren CI'ına eşliyse kabul edilmez (önce Evren'de birleştirme kararı gerekir).
    * ``exclude``: bu CI replikaya gönderilmez (ör. ServiceNow'da bilerek tutulmayan kayıt).
    * ``retry``: hata veya dışarıda tutma kalkar, sonraki turda yeniden denenir.
    """
    ctx.require("platform", "replica_admin")
    note = _note(body.note, "Replika kararı")
    me = ctx.principal.audit_id
    with ctx.conn.transaction():
        row = ctx.conn.execute("SELECT state, identified_sn_sys_id, sn_sys_id FROM sync.sn_ci_map "
                               "WHERE evren_sys_id = %s FOR UPDATE", (sys_id,)).fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "Bu CI için replika kaydı yok.")
        state, identified, current = row
        if body.action == "accept":
            if state != "conflict" or not identified:
                raise ApiError(409, "kabul_edilemez", "Yalnızca ServiceNow'un bir kayıt bulduğu çelişki kabul edilir.")
            taken = ctx.conn.execute(
                """SELECT EXISTS (SELECT 1 FROM sync.sn_ci_map WHERE sn_sys_id = %(sn)s AND evren_sys_id <> %(me)s)
                       OR EXISTS (SELECT 1 FROM cmdb.cmdb_ci WHERE sys_id = %(sn)s AND sys_id <> %(me)s)""",
                {"sn": identified, "me": sys_id}).fetchone()[0]
            if taken:
                raise ApiError(409, "baska_ci_eslesik", "ServiceNow'un bulduğu kayıt başka bir Evren CI'ına eşli. Önce "
                                                        "Evren'de iki CI'ın aynı olup olmadığına karar verin.")
            ctx.conn.execute("""UPDATE sync.sn_ci_map SET state = 'pending_push', accepted_sn_sys_id = %s, attempts = 0,
                                       next_try_at = NULL, resolved_by = %s, resolved_at = now(), resolve_note = %s,
                                       updated_at = now() WHERE evren_sys_id = %s""", (identified, me, note, sys_id))
            msg = "ServiceNow kaydı kabul edildi; sonraki turda doğrulanıp güncellenecek."
        elif body.action == "exclude":
            ctx.conn.execute("""UPDATE sync.sn_ci_map SET state = 'excluded', next_try_at = NULL, resolved_by = %s,
                                       resolved_at = now(), resolve_note = %s, updated_at = now()
                                 WHERE evren_sys_id = %s""", (me, note, sys_id))
            msg = "CI replikanın dışında tutulacak."
        else:
            if state not in ("error", "excluded", "conflict"):
                raise ApiError(409, "yeniden_denenemez", "Yalnızca hata, çelişki veya dışarıda tutulan kayıt yeniden "
                                                          "denenir.")
            ctx.conn.execute("""UPDATE sync.sn_ci_map SET state = 'pending_push', attempts = 0, next_try_at = NULL,
                                       resolved_by = %s, resolved_at = now(), resolve_note = %s, updated_at = now()
                                 WHERE evren_sys_id = %s""", (me, note, sys_id))
            msg = "Sonraki turda yeniden denenecek."
        _audit(ctx, "replica_resolve", f"cmdb_ci:{sys_id}", {"action": body.action, "from_state": state,
                                                             "identified": identified, "previous_sn": current,
                                                             "note": note})
        ctx.conn.execute("NOTIFY evren_replica")
    return {"message": msg}
