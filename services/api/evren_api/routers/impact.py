"""Etki analizi (U7.3, ADR-0030): bir CI düşerse neler etkilenir (yukarı) ve CI neye bağımlıdır (aşağı).

İlişki tipinin hangi ucunun diğerine bağımlı olduğu ``metadata/relationship_types.yaml`` → ``impact`` alanındadır
(``parent``: ebeveyn çocuğa bağımlı; ``child``: tersi; ``none``: bağımlılık yok). Yürüyüş özyinelemeli SQL'dir: döngüye
girmez, derinlik ve düğüm sayısıyla sınırlıdır. Okuma izni olmayan sınıftaki CI gösterilmez (sayısı döner) ama yürüyüş
onun üzerinden devam eder: gizli bir ara katman, okunabilir bir hizmetin etkilendiğini saklamamalıdır.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from psycopg import sql

from ..deps import Ctx, context
from ..errors import ApiError

router = APIRouter(prefix="/api/v1", tags=["impact"])

MAX_NODES = 500
#: Hizmet katmanı sınıfları (CSDM): etki özetinde ayrıca listelenir
SERVICE_ROOTS = ("cmdb_ci_service", "cmdb_ci_business_app", "cmdb_ci_business_capability")

_WALK = """
WITH RECURSIVE kinds(type_name, dir) AS (SELECT * FROM unnest(%(types)s::text[], %(dirs)s::text[])),
edges AS (
  SELECT CASE WHEN k.dir = 'parent' THEN r.parent ELSE r.child END AS dependent,
         CASE WHEN k.dir = 'parent' THEN r.child ELSE r.parent END AS dependency,
         t.name AS type_name
    FROM cmdb.cmdb_rel_ci r
    JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
    JOIN kinds k ON k.type_name = t.name),
walk(ci, depth, type_name, via, path) AS (
  SELECT e.{near}, 1, e.type_name, e.{far}, ARRAY[%(start)s::text, e.{near}::text]
    FROM edges e WHERE e.{far} = %(start)s
  UNION ALL
  SELECT e.{near}, w.depth + 1, e.type_name, e.{far}, w.path || e.{near}::text
    FROM edges e JOIN walk w ON e.{far} = w.ci
   WHERE w.depth < %(depth)s AND NOT (e.{near}::text = ANY(w.path)))
SELECT DISTINCT ON (w.ci) w.ci, w.depth, w.type_name, w.via, c.name, c.sys_class_name, c.used_for,
       c.life_cycle_stage = 'End of Life' OR c.install_status = 7
  FROM walk w JOIN cmdb.cmdb_ci c ON c.sys_id = w.ci
 ORDER BY w.ci, w.depth
 LIMIT %(limit)s
"""


def _walk(ctx: Ctx, start: str, start_name: str, direction: Literal["up", "down"], depth: int) -> dict[str, Any]:
    md = ctx.state.metadata
    pres = ctx.state.presenter
    kinds = {name: d for name, d in md.impact_directions.items() if d != "none"}
    labels = {t["name"]: str(t.get("label_tr") or t["name"]) for t in md.relationship_types}
    # Yukarı: bu CI'a bağımlı olanlar (bağımlılığı bu olan kenarın bağımlı ucu); aşağı: bu CI'ın bağımlılıkları
    near, far = ("dependent", "dependency") if direction == "up" else ("dependency", "dependent")
    query = sql.SQL(_WALK.format(near=near, far=far))  # near/far sabit iki değerden; kullanıcı girdisi değil
    with ctx.conn.cursor() as cur:
        cur.execute(query, {"types": list(kinds), "dirs": list(kinds.values()), "start": start, "depth": depth,
                            "limit": MAX_NODES + 1})
        rows = cur.fetchall()
    truncated = len(rows) > MAX_NODES
    hidden = 0
    readable: dict[str, bool] = {}
    services = [c for root in SERVICE_ROOTS if root in md.hierarchy for c in md.hierarchy.descendants(root)]
    nodes = []
    for ci, d, type_name, via, name, cls, used_for, retired in rows[:MAX_NODES]:
        if cls not in readable:
            readable[cls] = ctx.grant(cls, "read").allowed
        if not readable[cls]:
            hidden += 1
            continue
        # Bağımlı ucun bakışından fiil: uygulama "Üzerinde çalışır" sunucu, veritabanı "İçinde" örnek
        parent_label, _, child_label = labels.get(type_name, type_name).partition("::")
        phrase = parent_label if kinds.get(type_name) == "parent" else (child_label or parent_label)
        nodes.append({"sys_id": ci, "name": name or "(adsız)", "class": cls, "class_label": pres.class_label(cls),
                      "depth": d, "type": type_name, "phrase": phrase, "via": via, "service": cls in services,
                      "used_for": used_for, "production": used_for == "Production", "retired": bool(retired)})
    names = {start: start_name, **{n["sys_id"]: n["name"] for n in nodes}}  # gizli CI'ın adı sızmaz
    for n in nodes:
        n["via_name"] = names.get(n["via"], "(gizli CI)")
    nodes.sort(key=lambda n: (n["depth"], not n["service"], n["class_label"], n["name"].casefold()))
    return {"nodes": nodes, "services": [n for n in nodes if n["service"]], "hidden": hidden, "truncated": truncated,
            "count": len(nodes)}


@router.get("/ci/{sys_id}/impact")
def impact(sys_id: str, ctx: Ctx = Depends(context), depth: int = Query(6, ge=1, le=10)) -> dict[str, Any]:
    """CI düşerse etkilenenler (``upstream``) ve CI'ın bağımlılıkları (``downstream``); hizmet katmanı ayrıca."""
    if len(sys_id) != 32 or any(ch not in "0123456789abcdef" for ch in sys_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT sys_class_name, name FROM cmdb.cmdb_ci WHERE sys_id = %s", (sys_id,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "CI bulunamadı.")
    ctx.require(row[0], "read")
    up = _walk(ctx, sys_id, row[1] or "(adsız)", "up", depth)
    down = _walk(ctx, sys_id, row[1] or "(adsız)", "down", depth)
    return {"sys_id": sys_id, "name": row[1], "depth": depth, "upstream": up, "downstream": down,
            "summary": {"affected_services": len(up["services"]),
                        "affected_production_services": sum(1 for n in up["services"] if n["production"]),
                        "affected": up["count"], "dependencies": down["count"]}}
