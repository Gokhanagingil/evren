"""İlişki haritası (U7.4, ADR-0031): CI'ın çevresi, istenirse geçmiş bir anda.

Merkezden katman katman (en çok 3 adım) bütün ilişki tipleriyle genişler. Düğümün dikey düzeyi bağımlılık yönünden
gelir (``relationship_types.yaml`` → ``impact``): bu CI'a bağımlı olanlar üstte (negatif düzey), bağımlılıkları altta,
etki taşımayan bağlantılar (fiziksel bağlantı, sahiplik) aynı düzeyde. Okuma izni olmayan sınıftaki CI haritaya girmez
ve üzerinden genişlenmez; yalnızca sayısı döner. ``at`` verilirse ilişkiler o ana geri sarılır (``evren_core.timeline``);
düğüm adları bugünkü hâliyledir.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from evren_core.timeline import RelationKey, relations_at

from ..deps import Ctx, context
from ..errors import ApiError
from .impact import SERVICE_ROOTS
from .timeline import moment, relation_changes_after, relation_labels, relation_rows, valid_sysid

router = APIRouter(prefix="/api/v1", tags=["graph"])

#: Haritada en çok düğüm (okunabilirlik ve maliyet sınırı; aşılırsa ``truncated``)
MAX_NODES = 120


def _edges_around(ctx: Ctx, ids: list[str], at: datetime | None) -> set[RelationKey]:
    rows = relation_rows(ctx, ids)
    if at is None:
        return {r.key for r in rows}
    return relations_at(rows, relation_changes_after(ctx, ids, at), at)


@router.get("/ci/{sys_id}/graph")
def ci_graph(sys_id: str, ctx: Ctx = Depends(context), depth: int = Query(2, ge=1, le=3),
             at: datetime | None = Query(None, description="Geçmiş bir an (ISO 8601, saat dilimiyle)")) -> dict[str, Any]:
    valid_sysid(sys_id)
    if at is not None:
        at = moment(at)
    md, pres = ctx.state.metadata, ctx.state.presenter
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT name, sys_class_name, sys_created_on FROM cmdb.cmdb_ci WHERE sys_id = %s", (sys_id,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "CI bulunamadı.")
    ctx.require(row[1], "read")
    directions = md.impact_directions
    labels = relation_labels(md)
    services = {c for root in SERVICE_ROOTS if root in md.hierarchy for c in md.hierarchy.descendants(root)}
    readable: dict[str, bool] = {}

    def can_read(cls: str) -> bool:
        if cls not in readable:
            readable[cls] = ctx.grant(cls, "read").allowed
        return readable[cls]

    existed = at is None or row[2] <= at
    nodes: dict[str, dict[str, Any]] = {sys_id: {"name": row[0], "class": row[1], "depth": 0, "level": 0}}
    edges: dict[RelationKey, None] = {}
    hidden: set[str] = set()
    truncated = False
    frontier = [sys_id] if existed else []
    for step in range(1, depth + 1):
        if not frontier:
            break
        keys = _edges_around(ctx, frontier, at)
        found = {end for _, p, c in keys for end in (p, c) if end not in nodes and end not in hidden}
        with ctx.conn.cursor() as cur:
            cur.execute("""SELECT sys_id, name, sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])""",
                        (sorted(found),))
            info = {sid: (name, cls) for sid, name, cls in cur.fetchall()}
        nxt: list[str] = []
        # Düzey: kenarı geçerken bağımlıdan bağımlılığa inilir (+1), tersinde çıkılır (-1), etkisizde aynı kalır
        for rel_type, parent, child in sorted(keys):
            if parent in hidden or child in hidden:
                continue
            for near, far in ((parent, child), (child, parent)):
                if near not in nodes or far in nodes or far in hidden:
                    continue
                if far not in info:
                    continue  # silinmiş uç (beklenmez: CI fiziksel silinmez)
                name, cls = info[far]
                if not can_read(cls):
                    hidden.add(far)
                    continue
                if len(nodes) >= MAX_NODES:
                    truncated = True
                    continue
                d = directions.get(rel_type, "none")
                dependent = parent if d == "parent" else child if d == "child" else None
                shift = 0 if dependent is None else (-1 if far == dependent else 1)
                nodes[far] = {"name": name, "class": cls, "depth": step, "level": nodes[near]["level"] + shift}
                nxt.append(far)
            if parent in nodes and child in nodes:
                edges[(rel_type, parent, child)] = None
        frontier = nxt
    # Son katmandaki düğümlerin kendi aralarındaki kenarlar da çizilir (genişlenmeden)
    if frontier:
        for key in _edges_around(ctx, frontier, at):
            if key[1] in nodes and key[2] in nodes:
                edges[key] = None
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT sys_id, used_for, life_cycle_stage = 'End of Life' OR install_status = 7
                         FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])""", (list(nodes),))
        flags = {sid: (used, bool(retired)) for sid, used, retired in cur.fetchall()}
        cur.execute("""SELECT t.name, r.parent, r.child, array_agg(DISTINCT p.data_source ORDER BY p.data_source)
                         FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                         JOIN cmdb.cmdb_rel_ci_source p ON p.rel_sys_id = r.sys_id
                        WHERE r.parent = ANY(%(ids)s::bpchar[]) AND r.child = ANY(%(ids)s::bpchar[])
                        GROUP BY 1, 2, 3""", {"ids": list(nodes)})
        sources = {(t, p, c): list(s) for t, p, c, s in cur.fetchall()}
    out_nodes = []
    for sid, n in nodes.items():
        used, retired = flags.get(sid, (None, False))
        out_nodes.append({"sys_id": sid, "name": n["name"] or "(adsız)", "class": n["class"],
                          "class_label": pres.class_label(n["class"]), "depth": n["depth"], "level": n["level"],
                          "center": sid == sys_id, "service": n["class"] in services,
                          "production": used == "Production", "retired": retired})
    out_nodes.sort(key=lambda n: (n["level"], n["depth"], n["class_label"], n["name"].casefold()))
    out_edges = []
    for rel_type, parent, child in edges:
        d = directions.get(rel_type, "none")
        out_edges.append({"type": rel_type, "parent": parent, "child": child,
                          "parent_phrase": labels.get(rel_type, (rel_type, rel_type))[0],
                          "child_phrase": labels.get(rel_type, (rel_type, rel_type))[1], "impact": d,
                          "dependent": parent if d == "parent" else child if d == "child" else None,
                          "sources": sources.get((rel_type, parent, child), [])})
    levels = [n["level"] for n in out_nodes]
    return {"center": sys_id, "name": row[0], "at": at.isoformat() if at else None, "existed": existed,
            "depth": depth, "nodes": out_nodes, "edges": out_edges, "hidden": len(hidden), "truncated": truncated,
            "levels": {"min": min(levels), "max": max(levels)}}
