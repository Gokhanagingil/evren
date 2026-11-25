"""Konfigürasyon öğeleri: arama/listeleme (imleçli), CI 360° (kaynak bilgisiyle), değişim geçmişi."""
from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg import sql

from evren_core.metadata import CORE_COLUMNS

from ..deps import Ctx, context
from ..errors import ApiError
from ..present import COVERAGE_ROLES
from .platform import _SERVER, _and, _coverage_sql, _not

router = APIRouter(prefix="/api/v1", tags=["ci"])

SCOPES: dict[str, sql.Composable] = {
    "izlenmeyen": _and(_SERVER, _not(_coverage_sql("monitoring"))),
    "korumasiz": _and(_SERVER, _not(_coverage_sql("security"))),
    "itsm-yok": _and(_SERVER, _not(_coverage_sql("itsm"))),
    "yetkisiz": sql.SQL("EXISTS (SELECT 1 FROM cmdb.ci_reconciliation_state s WHERE s.ci_sys_id = ci.sys_id "
                        "AND s.conflicts - '_dropped' <> '{}'::jsonb)"),
}
_COVERAGE_CASE = sql.SQL(" ").join(
    sql.SQL("WHEN d.role IN ({}) THEN {}").format(sql.SQL(", ").join(sql.Literal(r) for r in roles), sql.Literal(flag))
    for flag, roles in COVERAGE_ROLES.items())
_SYSID_LEN = 32


def _cursor_encode(name: str, sys_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([name, sys_id]).encode()).decode()


def _cursor_decode(token: str) -> tuple[str, str]:
    try:
        name, sys_id = json.loads(base64.urlsafe_b64decode(token.encode()))
        if not isinstance(name, str) or not isinstance(sys_id, str) or len(sys_id) != _SYSID_LEN:
            raise ValueError
        return name, sys_id
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ApiError(400, "gecersiz_imlec", "Sayfalama imleci geçersiz.") from None


def _like(q: str) -> str:
    return "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/ci")
def list_cis(ctx: Ctx = Depends(context), q: str | None = Query(None, max_length=200),
             cls: str | None = Query(None, alias="class", max_length=80),
             source: str | None = Query(None, max_length=64), scope: str | None = Query(None, max_length=20),
             cursor: str | None = Query(None, max_length=512), limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    md = ctx.state.metadata
    if cls and cls not in md.hierarchy:  # girdi doğrulaması yetki denetiminden önce: yazım hatası "ret" kaydı üretmez
        raise ApiError(400, "gecersiz_sinif", "Sınıf sözlükte yok.")
    ctx.require(cls or "cmdb_ci", "read")
    where: list[sql.Composable] = [sql.SQL("true")]
    params: list[Any] = []
    if cls:
        where.append(sql.SQL("ci.sys_class_name IN (SELECT descendant FROM meta.class_closure WHERE ancestor = %s)"))
        params.append(cls)
    if source:
        if source not in md.sources.sources:
            raise ApiError(400, "gecersiz_kaynak", "Veri kaynağı tanımsız.")
        where.append(sql.SQL("EXISTS (SELECT 1 FROM ingest.identity_link l WHERE l.ci_sys_id = ci.sys_id AND l.active "
                             "AND l.data_source = %s)"))
        params.append(source)
    if scope:
        if scope not in SCOPES:
            raise ApiError(400, "gecersiz_kapsam", "Kapsam filtresi tanımsız.", [{"izinli": sorted(SCOPES)}])
        where.append(SCOPES[scope])
    if q:
        where.append(sql.SQL("(lower(coalesce(ci.name, '')) LIKE %s OR lower(coalesce(ci.fqdn, '')) LIKE %s "
                             "OR coalesce(ci.ip_address, '') LIKE %s OR ci.sys_id = %s)"))
        like = _like(q.strip())
        params += [like, like, like, q.strip().lower()]
    filters = sql.SQL(" AND ").join(where)
    page_where: sql.Composable = filters
    page_params = list(params)
    if cursor:
        name, sys_id = _cursor_decode(cursor)
        page_where = _and(filters, sql.SQL("(lower(coalesce(ci.name, '')), ci.sys_id) > (%s, %s)"))
        page_params += [name, sys_id]
    with ctx.conn.cursor() as cur:
        cur.execute(sql.SQL(
            """SELECT ci.sys_id, ci.name, ci.sys_class_name, ci.ip_address, ci.fqdn, ci.class_attributes->>'os',
                      ci.used_for, lower(coalesce(ci.name, '')),
                      ARRAY(SELECT DISTINCT l.data_source FROM ingest.identity_link l
                             WHERE l.ci_sys_id = ci.sys_id AND l.active ORDER BY 1),
                      ARRAY(SELECT DISTINCT CASE {cov} END FROM ingest.identity_link l
                             JOIN ingest.data_source d ON d.name = l.data_source
                            WHERE l.ci_sys_id = ci.sys_id AND l.active)
                 FROM cmdb.cmdb_ci ci WHERE {where}
                ORDER BY lower(coalesce(ci.name, '')), ci.sys_id LIMIT %s""").format(cov=_COVERAGE_CASE, where=page_where),
            (*page_params, limit + 1))
        rows = cur.fetchall()
        cur.execute(sql.SQL("SELECT count(*) FROM (SELECT 1 FROM cmdb.cmdb_ci ci WHERE {} LIMIT 10001) t").format(filters),
                    params)
        total = cur.fetchone()[0]  # type: ignore[index]
    pres = ctx.state.presenter
    items = []
    for sys_id, name, cls_name, ip, fqdn, os_, used_for, _sort, sources, cov in rows[:limit]:
        items.append({"sys_id": sys_id, "name": name or "(adsız)", "class": cls_name,
                      "class_label": pres.class_label(cls_name), "ip_address": ip, "fqdn": fqdn, "os": os_,
                      "used_for": used_for, "used_for_label": pres.choice("used_for", used_for) if used_for else None,
                      "sources": sources, "coverage": {f: f in cov for f in COVERAGE_ROLES}})
    nxt = _cursor_encode(rows[limit - 1][7], rows[limit - 1][0]) if len(rows) > limit else None
    return {"items": items, "count": total if total <= 10000 else "10000+", "next_cursor": nxt, "limit": limit}


@router.get("/ci/{sys_id}")
def get_ci(sys_id: str, ctx: Ctx = Depends(context)) -> dict[str, Any]:
    if len(sys_id) != _SYSID_LEN or not all(c in "0123456789abcdef" for c in sys_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    md = ctx.state.metadata
    pres = ctx.state.presenter
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT to_jsonb(ci) FROM cmdb.cmdb_ci ci WHERE ci.sys_id = %s", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "CI bulunamadı.")
        doc = row[0]
        cls = doc["sys_class_name"]
        grant = ctx.require(cls, "read")
        cur.execute("SELECT winners, conflicts FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s", (sys_id,))
        st = cur.fetchone() or ({}, {})
        winners, conflicts = st
        cur.execute(
            """SELECT l.data_source, l.source_key, l.method, l.rule_ref, l.probability, l.decided_by, l.decided_at,
                      sr.last_seen, sr.status, d.role
                 FROM ingest.identity_link l
                 JOIN ingest.source_record sr USING (data_source, source_key)
                 JOIN ingest.data_source d ON d.name = l.data_source
                WHERE l.ci_sys_id = %s AND l.active ORDER BY l.data_source, l.source_key""", (sys_id,))
        links = cur.fetchall()
        cur.execute(
            """SELECT t.name, 'parent', r.child, c.name FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                 JOIN cmdb.cmdb_ci c ON c.sys_id = r.child WHERE r.parent = %s
               UNION ALL
               SELECT t.name, 'child', r.parent, c.name FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                 JOIN cmdb.cmdb_ci c ON c.sys_id = r.parent WHERE r.child = %s""", (sys_id, sys_id))
        relations = [{"type": t, "direction": d, "target": tid, "target_name": tn or "(adsız)"}
                     for t, d, tid, tn in cur.fetchall()]
    fields = md.fields_of(cls)
    values: dict[str, Any] = {}
    for name in fields:
        v = doc.get(name) if name in CORE_COLUMNS else doc["class_attributes"].get(name)
        if v is not None and grant.can_see(fields[name].get("sensitivity", "internal")):
            values[name] = v
    refs = pres.resolve_refs(ctx.conn, [v for v in values.values() if isinstance(v, str)] +
                             [v for att in conflicts.values() if isinstance(att, list) for _, v in att
                              if isinstance(v, str)])
    attrs = []
    derived = {"discovery_source": "İlk keşfeden kaynak", "first_discovered": "Bağlı kaynakların ilk görülme zamanı",
               "last_discovered": "Keşif rollerindeki kaynakların son gözlem zamanı"}
    for name in sorted(values, key=lambda n: (n != "name", n)):
        w = winners.get(name) or ({"source": "Evren", "role": "Platform", "reason": f"Evren türetir: {derived[name]}"}
                                  if name in derived else {})
        attrs.append({
            "attribute": name, "label": fields[name].get("label_tr", name), "value": values[name],
            "display": pres.display(name, values[name], refs), "source": w.get("source", "—"),
            "role": w.get("role", ""), "reason": w.get("reason", "Uzlaştırma kaydı yok (ilk yükleme öncesi değer)"),
            "observed_at": w.get("observed_at"),
            "overridden": [{"source": s, "value": v, "display": pres.display(name, v, refs)} for s, v in w.get("overridden", [])],
            "unauthorized": w.get("unauthorized", []),
            "shadow": [{"source": s, "display": pres.display(name, v, refs)} for s, v in w.get("shadow", [])],
        })
    rejected = [{"attribute": a, "label": fields.get(a, {}).get("label_tr", a),
                 "attempts": [{"source": s, "display": pres.display(a, v, refs)} for s, v in att]}
                for a, att in sorted(conflicts.items()) if a != "_dropped" and grant.can_see(
                    fields.get(a, {}).get("sensitivity", "internal"))]
    roles = {r[0]: r[9] for r in links}
    coverage = {flag: any(role in rs for role in roles.values()) for flag, rs in COVERAGE_ROLES.items()}
    return {
        "sys_id": sys_id, "class": cls, "class_label": pres.class_label(cls), "name": doc.get("name") or "(adsız)",
        "fqdn": doc.get("fqdn"), "ip_address": doc.get("ip_address"), "os": doc["class_attributes"].get("os"),
        "used_for": doc.get("used_for"), "used_for_label": pres.choice("used_for", doc.get("used_for")),
        "support_group": refs.get(doc.get("support_group") or "", doc.get("support_group")),
        "owned_by": refs.get(doc.get("owned_by") or "", doc.get("owned_by")) if doc.get("owned_by") else None,
        "operational_status": pres.choice("operational_status", doc.get("operational_status")),
        "sources": sorted(roles), "attributes": attrs, "rejected": rejected, "relations": relations,
        "links": [{"data_source": ds, "role": role, "source_key": key, "outcome": method,
                   "summary": f"{method}" + (f" · {rule}" if rule else "") + (f" · %{float(prob) * 100:.1f}" if prob else "")
                              + (f" · karar: {by}" if method == "manual" else ""),
                   "observed_at": seen.isoformat(), "status": status}
                  for ds, key, method, rule, prob, by, _at, seen, status, role in links],
        "server": cls in md.hierarchy and "cmdb_ci_server" in md.hierarchy.ancestors(cls),
        "coverage": coverage, "unauthorized_count": len(rejected),
        "golden_version": doc.get("golden_version"), "sys_updated_on": doc.get("sys_updated_on"),
    }


@router.get("/ci/{sys_id}/history")
def ci_history(sys_id: str, ctx: Ctx = Depends(context), limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    if len(sys_id) != _SYSID_LEN:
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s", (sys_id,))
        row = cur.fetchone()
        if row is None:
            raise ApiError(404, "bulunamadi", "CI bulunamadı.")
        grant = ctx.require(row[0], "read")
        fields = ctx.state.metadata.fields_of(row[0])
        cur.execute("""SELECT changed_at, attribute, old_value, new_value, data_source, actor, reason
                         FROM audit.ci_change WHERE ci_sys_id = %s ORDER BY changed_at DESC, id DESC LIMIT %s""",
                    (sys_id, limit))
        items = [{"changed_at": at.isoformat(), "attribute": a, "old": o, "new": n, "source": s, "actor": who,
                  "reason": r} for at, a, o, n, s, who, r in cur.fetchall()
                 if a == "sys_class_name" or grant.can_see(fields.get(a, {}).get("sensitivity", "internal"))]
    return {"items": items}
