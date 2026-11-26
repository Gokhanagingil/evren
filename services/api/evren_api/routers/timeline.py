"""Zaman içinde sorgu (U7.4, F1-19, ADR-0031): CI'ın geçmiş bir andaki hâli ve değişiklik zaman çizelgesi.

Golden record'un yalnızca bugünkü hâli tutulur; geçmiş ``audit.ci_change`` içindedir. Bir andaki hâl bugünkü hâlden o
andan sonraki değişiklikler geri sarılarak bulunur (``evren_core.timeline``). İlişki açılması/kalkması da aynı izdedir
(ADR-0031). Okuma izni ve alan duyarlılığı bugünkü CI detayıyla aynıdır; okunamayan sınıftaki karşı CI'ın adı
gösterilmez.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from evren_core.metadata import CORE_COLUMNS
from evren_core.timeline import Change, RelationRow, attributes_at, group_moments, relations_at

from ..deps import Ctx, context
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["timeline"])

#: Bir sorguda geri sarılacak en çok değişiklik (çok eski bir an çok satır demektir; aşılırsa açıkça bildirilir)
MAX_CHANGES = 20000
#: Türetilen zaman damgaları değişim geçmişine yazılmaz; geçmiş bir anda gösterilmez
DERIVED = frozenset({"first_discovered", "last_discovered"})
_SYSID_LEN = 32


def valid_sysid(sys_id: str) -> None:
    if len(sys_id) != _SYSID_LEN or any(ch not in "0123456789abcdef" for ch in sys_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")


def moment(at: datetime) -> datetime:
    """Sorgulanan an: saat dilimi zorunlu, gelecek olamaz."""
    if at.tzinfo is None:
        raise ApiError(422, "saat_dilimi_yok", "Zaman saat dilimiyle verilmeli (ör. 2026-11-01T09:00:00Z).")
    if at > datetime.now(UTC):
        raise ApiError(422, "gelecek_zaman", "Gelecekteki bir an sorgulanamaz.")
    return at


def relation_labels(md: Any) -> dict[str, tuple[str, str]]:
    """İlişki tipi → (ebeveynin bakışından fiil, çocuğun bakışından fiil): "Bağımlı" / "Kullanılır"."""
    out = {}
    for t in md.relationship_types:
        parent, _, child = str(t.get("label_tr") or t["name"]).partition("::")
        out[str(t["name"])] = (parent, child or parent)
    return out


def phrase(labels: dict[str, tuple[str, str]], rel_type: str, as_parent: bool) -> str:
    pair = labels.get(rel_type) or tuple(rel_type.partition("::")[::2])
    return pair[0] if as_parent else pair[1]


def ci_names(ctx: Ctx, ids: set[str]) -> dict[str, tuple[str, str, bool]]:
    """sys_id → (ad, sınıf, okunabilir mi). Okunamayan sınıfın adı dönmez."""
    if not ids:
        return {}
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT sys_id, name, sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])",
                    (sorted(ids),))
        rows = cur.fetchall()
    readable: dict[str, bool] = {}
    out = {}
    for sid, name, cls in rows:
        if cls not in readable:
            readable[cls] = ctx.grant(cls, "read").allowed
        out[sid] = (name or "(adsız)" if readable[cls] else "", cls if readable[cls] else "", readable[cls])
    return out


def _load_ci(ctx: Ctx, sys_id: str) -> dict[str, Any]:
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT to_jsonb(ci) FROM cmdb.cmdb_ci ci WHERE ci.sys_id = %s", (sys_id,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "CI bulunamadı.")
    return row[0]


def _change(row: tuple[Any, ...]) -> Change:
    at, seq, attribute, old, new, source, actor, reason, correlation = row
    return Change(at=at, seq=seq, attribute=attribute, old=old, new=new, source=source, actor=actor, reason=reason,
                  correlation=correlation or {})


# Sorgular tam metin sabitleridir (dizge birleştirme yok); değerler her zaman parametredir
_RELATION_EVENTS_SQL = """
SELECT changed_at, id, attribute, old_value, new_value, data_source, actor, reason, correlation FROM audit.ci_change
 WHERE ci_sys_id = ANY(%s::bpchar[]) AND attribute = 'relation' AND changed_at > %s
 ORDER BY changed_at DESC, id DESC LIMIT %s"""
_CHANGES_AFTER_SQL = """
SELECT changed_at, id, attribute, old_value, new_value, data_source, actor, reason, correlation FROM audit.ci_change
 WHERE ci_sys_id = %s AND changed_at > %s
 ORDER BY changed_at DESC, id DESC LIMIT %s"""
_TIMELINE_SQL = """
SELECT changed_at, id, attribute, old_value, new_value, data_source, actor, reason, correlation FROM audit.ci_change
 WHERE ci_sys_id = %s AND (%s::timestamptz IS NULL OR (changed_at, id) < (%s, %s))
 ORDER BY changed_at DESC, id DESC LIMIT %s"""


def relation_rows(ctx: Ctx, ids: list[str]) -> list[RelationRow]:
    """Bugün var olan ve verilen CI'lardan birine dokunan ilişkiler (açıldıkları anla)."""
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT t.name, r.parent, r.child, r.sys_created_on FROM cmdb.cmdb_rel_ci r
                         JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                        WHERE r.parent = ANY(%(ids)s::bpchar[]) OR r.child = ANY(%(ids)s::bpchar[])""", {"ids": ids})
        return [RelationRow(t, p, c, created) for t, p, c, created in cur.fetchall()]


def relation_changes_after(ctx: Ctx, ids: list[str], at: datetime) -> list[Change]:
    """Verilen CI'ların o andan sonraki ilişki olayları (olay iki uca da yazılır)."""
    with ctx.conn.cursor() as cur:
        cur.execute(_RELATION_EVENTS_SQL, (ids, at, MAX_CHANGES + 1))
        rows = cur.fetchall()
    if len(rows) > MAX_CHANGES:
        raise ApiError(422, "cok_eski_an", "Bu an için geri sarılacak değişiklik çok fazla; daha yakın bir an seçin.")
    return [_change(r) for r in rows]


def _current_values(md: Any, doc: dict[str, Any], cls: str) -> dict[str, Any]:
    values = {"sys_class_name": cls}
    for name in md.fields_of(cls):
        v = doc.get(name) if name in CORE_COLUMNS else (doc.get("class_attributes") or {}).get(name)
        if v is not None:
            values[name] = v
    return values


def _created_on(doc: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(doc["sys_created_on"]))


@router.get("/ci/{sys_id}/as-of")
def ci_as_of(sys_id: str, at: datetime = Query(..., description="Sorgulanan an (ISO 8601, saat dilimiyle)"),
             ctx: Ctx = Depends(context)) -> dict[str, Any]:
    """CI'ın o andaki öznitelikleri ve ilişkileri; bugünle farkı."""
    valid_sysid(sys_id)
    at = moment(at)
    md, pres = ctx.state.metadata, ctx.state.presenter
    doc = _load_ci(ctx, sys_id)
    cls_now = doc["sys_class_name"]
    grant = ctx.require(cls_now, "read")
    created = _created_on(doc)
    with ctx.conn.cursor() as cur:
        cur.execute(_CHANGES_AFTER_SQL, (sys_id, at, MAX_CHANGES + 1))
        rows = cur.fetchall()
    if len(rows) > MAX_CHANGES:
        raise ApiError(422, "cok_eski_an", "Bu an için geri sarılacak değişiklik çok fazla; daha yakın bir an seçin.")
    changes = [_change(r) for r in rows]
    base = {"sys_id": sys_id, "name": doc.get("name"), "at": at.isoformat(), "created_on": created.isoformat(),
            "existed": created <= at, "changes_since": sum(1 for c in changes if c.attribute != "relation")}
    if created > at:
        return {**base, "class": None, "class_label": None, "attributes": [], "changed_count": 0,
                "relations": {"then": [], "added_since": [], "removed_since": []}, "hidden_relations": 0}
    current = _current_values(md, doc, cls_now)
    then = attributes_at(current, changes, at)
    cls_then = str(then.get("sys_class_name") or cls_now)
    fields = {**(md.fields_of(cls_then) if cls_then in md.class_map else {}), **md.fields_of(cls_now)}
    refs = pres.resolve_refs(ctx.conn, [v for v in [*then.values(), *current.values()] if isinstance(v, str)])
    attrs = []
    for name in sorted(fields, key=lambda n: (n != "name", fields[n].get("label_tr", n).casefold())):
        if name in DERIVED or not grant.can_see(fields[name].get("sensitivity", "internal")):
            continue
        old_v, now_v = then.get(name), current.get(name)
        if old_v is None and now_v is None:
            continue
        attrs.append({"attribute": name, "label": fields[name].get("label_tr", name),
                      "then": pres.display(name, old_v, refs), "now": pres.display(name, now_v, refs),
                      "changed": old_v != now_v})
    # İlişkiler: o anda olanlar ve bugünle farkı (CI'ın kendi ilişki olaylarından)
    rel_now = relation_rows(ctx, [sys_id])
    keys_then = relations_at(rel_now, [c for c in changes if c.attribute == "relation"], at)
    keys_now = {r.key for r in rel_now}
    labels = relation_labels(md)
    others = {p if c == sys_id else c for _, p, c in keys_then | keys_now}
    names = ci_names(ctx, others)
    hidden = 0

    def item(key: tuple[str, str, str]) -> dict[str, Any] | None:
        nonlocal hidden
        rel_type, parent, child = key
        other = child if parent == sys_id else parent
        name, cls, readable = names.get(other, ("", "", False))
        if not readable:
            hidden += 1
            return None
        return {"type": rel_type, "phrase": phrase(labels, rel_type, parent == sys_id),
                "direction": "parent" if parent == sys_id else "child", "other": other, "other_name": name,
                "other_class_label": pres.class_label(cls)}

    def items(keys: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
        out = [x for x in (item(k) for k in keys) if x]
        return sorted(out, key=lambda x: (x["phrase"].casefold(), x["other_name"].casefold()))

    relations = {"then": items(keys_then), "added_since": items(keys_now - keys_then),
                 "removed_since": items(keys_then - keys_now)}
    return {**base, "class": cls_then, "class_label": pres.class_label(cls_then), "attributes": attrs,
            "changed_count": sum(1 for a in attrs if a["changed"]), "relations": relations,
            "hidden_relations": hidden}


def _cursor_encode(at: datetime, seq: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([at.isoformat(), seq]).encode()).decode().rstrip("=")


def _cursor_decode(token: str) -> tuple[datetime, int]:
    try:
        at, seq = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        return datetime.fromisoformat(at), int(seq)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "gecersiz_imlec", "Sayfa imleci geçersiz.") from exc


@router.get("/ci/{sys_id}/timeline")
def ci_timeline(sys_id: str, ctx: Ctx = Depends(context), before: str | None = Query(None, max_length=200),
                limit: int = Query(200, ge=5, le=1000)) -> dict[str, Any]:
    """Değişiklik zaman çizelgesi (yeniden eskiye): aynı çalışmanın veya aynı elle isteğin değişiklikleri tek an."""
    valid_sysid(sys_id)
    md, pres = ctx.state.metadata, ctx.state.presenter
    doc = _load_ci(ctx, sys_id)
    cls = doc["sys_class_name"]
    grant = ctx.require(cls, "read")
    cursor = _cursor_decode(before) if before else None
    with ctx.conn.cursor() as cur:
        cur.execute(_TIMELINE_SQL,
                    (sys_id, cursor[0] if cursor else None, cursor[0] if cursor else None,
                     cursor[1] if cursor else 0, limit + 1))
        rows = cur.fetchall()
    more = len(rows) > limit
    changes = [_change(r) for r in rows[:limit]]
    fields = md.fields_of(cls)
    visible = [c for c in changes if c.attribute in ("relation", "sys_class_name")
               or grant.can_see(fields.get(c.attribute, {}).get("sensitivity", "internal"))]
    moments = group_moments(visible)
    if more and len(moments) > 1:
        moments = moments[:-1]  # son an sayfa sınırında bölünmüş olabilir: sonraki sayfada bütün gelir
    last = moments[-1].changes[-1] if moments else (changes[-1] if changes else None)
    refs = pres.resolve_refs(ctx.conn, [v for c in visible for v in (c.old, c.new) if isinstance(v, str)])
    labels = relation_labels(md)
    rel_values = [v for m in moments for c in m.changes if c.attribute == "relation" for v in (c.old, c.new)
                  if isinstance(v, dict)]
    names = ci_names(ctx, {v["child"] if v.get("parent") == sys_id else v.get("parent", "") for v in rel_values})

    def show(attribute: str, value: Any) -> str:
        if attribute == "sys_class_name" and isinstance(value, str):
            return pres.class_label(value)
        return pres.display(attribute, value, refs)

    out = []
    for m in moments:
        attrs, rels = [], []
        for c in m.changes:
            if c.attribute == "relation":
                value = c.new if isinstance(c.new, dict) else c.old
                if not isinstance(value, dict):
                    continue
                other = value["child"] if value.get("parent") == sys_id else value.get("parent", "")
                name, _, readable = names.get(other, ("", "", False))
                rels.append({"action": "added" if isinstance(c.new, dict) else "removed",
                             "phrase": phrase(labels, str(value.get("type")), value.get("parent") == sys_id),
                             "type": value.get("type"), "other": other if readable else None,
                             "other_name": name if readable else "(gizli CI)", "no_effect": c.no_effect,
                             "ended": (c.correlation or {}).get("ended")})
            else:
                label = "Sınıf" if c.attribute == "sys_class_name" else fields.get(c.attribute, {}).get(
                    "label_tr", c.attribute)
                attrs.append({"attribute": c.attribute, "label": label, "old": show(c.attribute, c.old),
                              "new": show(c.attribute, c.new)})
        out.append({"at": m.at.isoformat(), "source": m.source, "actor": m.actor, "reason": m.reason,
                    "reason_label": m.reason_label, "changes": attrs, "relations": rels})
    next_cursor = _cursor_encode(last.at, last.seq) if more and last else None
    return {"sys_id": sys_id, "created_on": _created_on(doc).isoformat(), "moments": out, "next_cursor": next_cursor}
