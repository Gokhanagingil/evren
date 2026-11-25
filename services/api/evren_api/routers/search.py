"""Küresel CI araması (U3, ADR-0018): OpenSearch; yoksa veya ulaşılamazsa PostgreSQL yedeği.

Yetki arama motoruna bırakılmaz: kullanıcının okuyabildiği sınıflar sorguya süzgeç olarak, görebildiği alanlar arama
alanı olarak verilir; yanıttaki her alan sınıf bazında hassasiyetle yeniden süzülür. Yanıt hangi motorun kullanıldığını
söyler (``engine``) ve yedeğe düşüldüyse ``degraded: true`` olur.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row

from evren_core.normalize import ascii_fold
from evren_core.opensearch_client import OpenSearchError
from evren_core.search import ALIAS, SEARCH_FIELDS, build_query

from ..deps import Ctx, context

router = APIRouter(prefix="/api/v1", tags=["arama"])
log = logging.getLogger("evren.api.search")
_RESULT_FIELDS = ("name", "ip_address", "fqdn", "os")
# ascii_fold'un SQL karşılığı (yalnızca Türkçe harfler; arama yedeği için yeterli)
_FOLD = "lower(translate(coalesce({}, ''), 'İIıŞşĞğÜüÖöÇç', 'iiissgguuoocc'))"


def _sensitivity(md: Any, cls: str, field: str) -> str:
    return (md.fields_of(cls).get(field) or {}).get("sensitivity", "internal")


@router.get("/search")
def search(ctx: Ctx = Depends(context), q: str = Query(..., min_length=1, max_length=200),
           limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    root = ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    grants = {c: ctx.grant(c, "read") for c in md.hierarchy.parents}
    classes = sorted(c for c, g in grants.items() if g.allowed)
    fields = [f for f in SEARCH_FIELDS if root.can_see(_sensitivity(md, "cmdb_ci", f))]
    client = ctx.state.search
    engine, degraded, hits = "postgresql", False, None
    if client is not None:
        try:
            res = client.search(ALIAS, build_query(q, classes=classes, fields=fields, limit=limit))
            hits = [h["_source"] for h in res.get("hits", {}).get("hits", [])]
            engine = "opensearch"
        except OpenSearchError as exc:
            log.warning("OpenSearch kullanılamıyor (%s); PostgreSQL yedeğine düşüldü", exc)
            degraded = True
    if hits is None:
        hits = _postgres(ctx, q, classes, fields, limit)
    pres = ctx.state.presenter
    items = []
    for h in hits:
        cls = h["sys_class_name"]
        grant = grants.get(cls)
        if grant is None or not grant.allowed:
            continue  # savunma: motor süzgeci atlanmış olsa bile yanıta girmez
        item = {"sys_id": h["sys_id"], "class": cls, "class_label": pres.class_label(cls),
                "sources": h.get("sources") or []}
        for f in _RESULT_FIELDS:
            item[f] = h.get(f) if grant.can_see(_sensitivity(md, cls, f)) else None
        item["name"] = item["name"] or "(adsız)"
        items.append(item)
    return {"engine": engine, "degraded": degraded, "items": items}


def _postgres(ctx: Ctx, q: str, classes: list[str], fields: list[str], limit: int) -> list[dict[str, Any]]:
    """Yedek: alt dize araması, yalnızca görülebilir alanlarda. Sorgu ``ascii_fold`` ile, sütunlar aynı Türkçe
    katlamayla (``_FOLD``) karşılaştırılır: "İST-DB", "ist-db" ve "ıst-db" aynı sonucu verir."""
    folded = ascii_fold(q.strip())
    like = "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cols = {"name": "ci.name", "fqdn": "ci.fqdn", "ip_address": "ci.ip_address", "serial_number": "ci.serial_number",
            "asset_tag": "ci.asset_tag", "mac_address": "ci.mac_address", "os": "ci.class_attributes->>'os'"}
    usable = [cols[f] for f in fields if f in cols]
    if not usable or not classes:
        return []
    match = " OR ".join(_FOLD.format(c) + " LIKE %(like)s" for c in usable)  # sütunlar sabit listeden
    with ctx.conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""SELECT ci.sys_id, ci.name, ci.sys_class_name, ci.ip_address, ci.fqdn, ci.class_attributes->>'os' AS os,
                       ARRAY(SELECT DISTINCT l.data_source FROM ingest.identity_link l
                              WHERE l.ci_sys_id = ci.sys_id AND l.active ORDER BY 1) AS sources
                  FROM cmdb.cmdb_ci ci
                 WHERE ci.sys_class_name = ANY(%(classes)s) AND (ci.sys_id = %(exact)s OR {match})
                 ORDER BY (ci.sys_id = %(exact)s) DESC, lower(coalesce(ci.name, '')) LIMIT %(limit)s""",  # nosec B608  # noqa: S608
            {"like": like, "exact": q.strip().lower(), "classes": classes, "limit": limit})
        return cur.fetchall()
