"""Konfigürasyon öğeleri: arama/listeleme (imleçli), CI 360° (kaynak bilgisiyle), değişim geçmişi, ayırma,
elle düzenleme.

API golden record'a yazmaz (CLAUDE.md kural 1): ayırma isteği ``ingest.split_request``'e (göç 0007, ADR-0011),
elle düzenleme ``ingest.manual_edit``'e (göç 0008) yazılır; uzlaştırıcı uygular.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from evren_core.change_control import APPROVAL_TR, STATE_TR, TYPE_TR, VERDICT_TR
from evren_core.metadata import CORE_COLUMNS

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash
from ..present import COVERAGE_ROLES
from .attestation import ci_attestation
from .insight import ANOMALY_STATUS
from .manual import manual_relation_keys
from .platform import _SERVER, _and, _coverage_sql, _not
from .quality import ci_findings
from .staleness import ci_staleness

router = APIRouter(prefix="/api/v1", tags=["ci"])

SCOPES: dict[str, sql.Composable] = {
    "izlenmeyen": _and(_SERVER, _not(_coverage_sql("monitoring"))),
    "korumasiz": _and(_SERVER, _not(_coverage_sql("security"))),
    "itsm-yok": _and(_SERVER, _not(_coverage_sql("itsm"))),
    "yetkisiz": sql.SQL("EXISTS (SELECT 1 FROM cmdb.ci_reconciliation_state s WHERE s.ci_sys_id = ci.sys_id "
                        "AND s.conflicts - '_dropped' <> '{}'::jsonb)"),
    # ADR-0025: açık (veya incelenen) yetkisiz değişiklik anomalisi olan CI'lar
    "yetkisiz-degisiklik": sql.SQL("EXISTS (SELECT 1 FROM insight.anomaly a WHERE a.ci_sys_id = ci.sys_id "
                                   "AND a.kind = 'unauthorized_change' AND a.status IN ('open', 'acknowledged'))"),
}
_COVERAGE_CASE = sql.SQL(" ").join(
    sql.SQL("WHEN d.role IN ({}) THEN {}").format(sql.SQL(", ").join(sql.Literal(r) for r in roles), sql.Literal(flag))
    for flag, roles in COVERAGE_ROLES.items())
_SYSID_LEN = 32
_SYSID = r"^[0-9a-f]{32}$"
SPLIT_STATUS = {"pending": "Uygulanmayı bekliyor", "applied": "Uygulandı", "rejected": "Reddedildi"}


class ManualEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attributes: dict[str, Any] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


class SplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_id: str = Field(pattern=_SYSID)
    target_ci: str | None = Field(default=None, pattern=_SYSID)
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


def _split_blocker(method: str, others: int, may_create: bool, has_target: bool,
                   data_source: str = "") -> tuple[int, str, str] | None:
    """Ayırmayı engelleyen durum (HTTP kodu, hata kodu, Türkçe ileti). Uzlaştırıcı aynı denetimi yeniden yapar.

    ``others``: CI'da kalacak, elle giriş dışındaki etkin kaynak kaydı sayısı."""
    if data_source == "Manual":
        return (422, "elle_giris_bagi",
                "Elle giriş kaydı ayrılmaz; elle girilen değerleri Düzenle ile kaldırın.")
    if method == "import":
        return (422, "ilk_yukleme_bagi",
                "Bu CI'ın sys_id'si bu kaynak kaydından geliyor (ilk yükleme); bunun yerine diğer kayıtları ayırın.")
    if others == 0:
        return 409, "tek_kaynak", "CI'ın tek kaynak kaydı ayrılamaz."
    if not has_target and not may_create:
        return 422, "kaynak_ci_acamaz", "Bu kaynak yeni CI açamaz (gölge/devir); hedef CI seçin."
    return None


def _valid_sysid(sys_id: str) -> None:
    if len(sys_id) != _SYSID_LEN or not all(c in "0123456789abcdef" for c in sys_id):
        raise ApiError(400, "gecersiz_sys_id", "sys_id 32 haneli küçük harf onaltılık olmalı.")


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
    _valid_sysid(sys_id)
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
                      sr.last_seen, sr.status, d.role, l.sys_id,
                      EXISTS (SELECT 1 FROM ingest.split_request s WHERE s.link_sys_id = l.sys_id AND s.status = 'pending')
                 FROM ingest.identity_link l
                 JOIN ingest.source_record sr USING (data_source, source_key)
                 JOIN ingest.data_source d ON d.name = l.data_source
                WHERE l.ci_sys_id = %s AND l.active ORDER BY l.data_source, l.source_key""", (sys_id,))
        links = cur.fetchall()
        cur.execute(
            """SELECT sys_id, status, attributes, requested_by, requested_at, result, result_note
                 FROM ingest.manual_edit WHERE ci_sys_id = %s ORDER BY requested_at DESC LIMIT 10""", (sys_id,))
        edits = cur.fetchall()
        cur.execute("SELECT attributes FROM ingest.source_record WHERE data_source = 'Manual' AND source_key = %s",
                    (sys_id,))
        manual_row = cur.fetchone()
        cur.execute(
            """SELECT sys_id, status, data_source, source_key, requested_by, requested_at, result_ci, result_note,
                      applied_at, CASE WHEN ci_sys_id = %s THEN 'source' ELSE 'result' END, ci_sys_id
                 FROM ingest.split_request WHERE ci_sys_id = %s OR result_ci = %s
                ORDER BY requested_at DESC LIMIT 20""", (sys_id, sys_id, sys_id))
        splits = [{"id": i, "status": st, "status_label": SPLIT_STATUS[st], "data_source": ds, "source_key": key,
                   "requested_by": by, "requested_at": at.isoformat(), "result_ci": res, "note": note,
                   "applied_at": done.isoformat() if done else None, "side": side, "ci_sys_id": src_ci}
                  for i, st, ds, key, by, at, res, note, done, side, src_ci in cur.fetchall()]
        cur.execute(
            """SELECT t.name, 'parent', r.child, c.name, r.discovery_source,
                      ARRAY(SELECT DISTINCT p.data_source FROM cmdb.cmdb_rel_ci_source p WHERE p.rel_sys_id = r.sys_id
                             ORDER BY 1)
                 FROM cmdb.cmdb_rel_ci r
                 JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type JOIN cmdb.cmdb_ci c ON c.sys_id = r.child
                WHERE r.parent = %s
               UNION ALL
               SELECT t.name, 'child', r.parent, c.name, r.discovery_source,
                      ARRAY(SELECT DISTINCT p.data_source FROM cmdb.cmdb_rel_ci_source p WHERE p.rel_sys_id = r.sys_id
                             ORDER BY 1)
                 FROM cmdb.cmdb_rel_ci r
                 JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type JOIN cmdb.cmdb_ci c ON c.sys_id = r.parent
                WHERE r.child = %s""", (sys_id, sys_id))
        rel_rows = cur.fetchall()
        manual_rels = manual_relation_keys(ctx.conn, sys_id)
        # source: ilk bildiren (cmdb_rel_ci.discovery_source); sources: bugün bildirenler (ADR-0031)
        relations = [{"type": t, "direction": d, "target": tid, "target_name": tn or "(adsız)", "source": src,
                      "sources": list(srcs or [src]), "manual": (t, d, tid) in manual_rels}
                     for t, d, tid, tn, src, srcs in rel_rows]
        cur.execute(
            """SELECT r.sys_id, r.action, r.type, CASE WHEN r.ci_sys_id = %s THEN r.direction
                        WHEN r.direction = 'parent' THEN 'child' ELSE 'parent' END,
                      CASE WHEN r.ci_sys_id = %s THEN r.target_sys_id ELSE r.ci_sys_id END, c.name, r.requested_by,
                      r.requested_at
                 FROM ingest.manual_relation r
                 JOIN cmdb.cmdb_ci c ON c.sys_id = CASE WHEN r.ci_sys_id = %s THEN r.target_sys_id ELSE r.ci_sys_id END
                WHERE r.status = 'pending' AND (r.ci_sys_id = %s OR r.target_sys_id = %s)
                ORDER BY r.requested_at""", (sys_id, sys_id, sys_id, sys_id, sys_id))
        pending_relations = [{"id": i, "action": a, "type": t, "direction": d, "target": tid,
                              "target_name": tn or "(adsız)", "requested_by": by, "requested_at": at.isoformat()}
                             for i, a, t, d, tid, tn, by, at in cur.fetchall()]
        # Bağımlı CI kimliği (ADR-0029): bu CI'a bağlı bağımlılar ve bağımlıysa ana CI'ı
        cur.execute("""SELECT k.ci_sys_id, c.name, c.sys_class_name,
                              string_agg(DISTINCT k.signature || ': ' || k.value, ' · ')
                         FROM ingest.dependent_key k JOIN cmdb.cmdb_ci c ON c.sys_id = k.ci_sys_id
                        WHERE k.parent_ci_sys_id = %s GROUP BY 1, 2, 3 ORDER BY c.sys_class_name, lower(c.name)
                        LIMIT 500""", (sys_id,))
        dep_rows = cur.fetchall()
        cur.execute("""SELECT DISTINCT k.parent_ci_sys_id, c.name, c.sys_class_name FROM ingest.dependent_key k
                         JOIN cmdb.cmdb_ci c ON c.sys_id = k.parent_ci_sys_id WHERE k.ci_sys_id = %s""", (sys_id,))
        parent_rows = cur.fetchall()
    fields = md.fields_of(cls)
    values: dict[str, Any] = {}
    for name in fields:
        v = doc.get(name) if name in CORE_COLUMNS else doc["class_attributes"].get(name)
        if v is not None and grant.can_see(fields[name].get("sensitivity", "internal")):
            values[name] = v
    refs = pres.resolve_refs(ctx.conn, [v for v in values.values() if isinstance(v, str)] +
                             [v for att in conflicts.values() if isinstance(att, list) for _, v in att
                              if isinstance(v, str)])
    # CI'a başvuran alanlar (ör. ağ adaptörünün bağlı olduğu CI): okuma izni olan sınıfta CI adıyla gösterilir
    ci_refs = [v for n, v in values.items() if isinstance(v, str) and v not in refs and re.match(_SYSID, v)
               and str(fields[n].get("reference") or "").startswith("cmdb_ci")]
    if ci_refs:
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT sys_id, name, sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])",
                        (ci_refs,))
            refs.update({sid: name or "(adsız)" for sid, name, c in cur.fetchall() if ctx.grant(c, "read").allowed})
    attrs = []
    derived = {"discovery_source": "İlk keşfeden kaynak", "first_discovered": "Bağlı kaynakların ilk görülme zamanı",
               "last_discovered": "Keşif rollerindeki kaynakların son gözlem zamanı"}
    for name in sorted(values, key=lambda n: (n != "name", n)):
        w = winners.get(name) or ({"source": "Evren", "role": "Platform", "reason": f"Evren türetir: {derived[name]}"}
                                  if name in derived else {})
        attrs.append({
            "attribute": name, "label": fields[name].get("label_tr", name), "value": values[name],
            "type": fields[name].get("type", "string"),
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
    reg = md.sources
    write = ctx.grant(cls, "write")
    editable = ctx.state.edit_policy.fields(cls, write)
    manual_values = manual_row[0] if manual_row else {}
    edit_fields = []
    for name, spec in editable.items():
        raw = doc.get(name) if name in CORE_COLUMNS else doc["class_attributes"].get(name)
        if raw is not None and not grant.can_see(spec.sensitivity):
            raw = None
        edit_fields.append({**spec.as_json(), "value": raw, "manual_value": manual_values.get(name),
                            "display": pres.display(name, raw, refs) if raw is not None else None,
                            "source": (winners.get(name) or {}).get("source")})
    edit_fields.sort(key=lambda f: (not f["governed"], f["label"].casefold()))
    can_split = ctx.grant(cls, "split").allowed

    observed = sum(1 for r in links if r[0] != "Manual")

    def split_state(method: str, ds: str, pending: bool) -> dict[str, Any]:
        blocker = _split_blocker(method, observed - 1, ds in reg.sources and reg.may_create_ci(ds), False, ds)
        return {"allowed": can_split and not pending and blocker is None, "pending": pending,
                "reason": "Ayırma isteği uygulanmayı bekliyor" if pending else blocker[2] if blocker else None}

    coverage = {flag: any(role in rs for role in roles.values()) for flag, rs in COVERAGE_ROLES.items()}
    posture = _security_posture(ctx, sys_id, grant)
    change_control = _change_control(ctx, sys_id, fields, grant, pres, refs)
    return {
        "sys_id": sys_id, "class": cls, "class_label": pres.class_label(cls), "name": doc.get("name") or "(adsız)",
        "fqdn": doc.get("fqdn"), "ip_address": doc.get("ip_address"), "os": doc["class_attributes"].get("os"),
        "used_for": doc.get("used_for"), "used_for_label": pres.choice("used_for", doc.get("used_for")),
        "support_group": refs.get(doc.get("support_group") or "", doc.get("support_group")),
        "owned_by": refs.get(doc.get("owned_by") or "", doc.get("owned_by")) if doc.get("owned_by") else None,
        "operational_status": pres.choice("operational_status", doc.get("operational_status")),
        "sources": sorted(roles), "attributes": attrs, "rejected": rejected, "relations": relations,
        "pending_relations": pending_relations, "can_relate": ctx.grant("cmdb_rel_ci", "create").allowed,
        "can_unrelate": ctx.grant("cmdb_rel_ci", "delete").allowed,
        "links": [{"data_source": ds, "role": role, "source_key": key, "outcome": method,
                   "summary": f"{method}" + (f" · {rule}" if rule else "") + (f" · %{float(prob) * 100:.1f}" if prob else "")
                              + (f" · karar: {by}" if method in ("manual", "split") else ""),
                   "observed_at": seen.isoformat(), "status": status, "id": link_id, "method": method,
                   "split": split_state(method, ds, pending)}
                  for ds, key, method, rule, prob, by, _at, seen, status, role, link_id, pending in links],
        "can_split": can_split, "splits": splits, "can_edit": bool(edit_fields), "editable": edit_fields,
        "edits": [{"id": i, "status": st, "status_label": SPLIT_STATUS[st], "attributes": sorted(attrs),
                   "requested_by": by, "requested_at": at.isoformat(), "result": res, "note": note}
                  for i, st, attrs, by, at, res, note in edits],
        "server": cls in md.hierarchy and "cmdb_ci_server" in md.hierarchy.ancestors(cls),
        "coverage": coverage, "unauthorized_count": len(rejected), "change_control": change_control,
        "unauthorized_changes": change_control["open_unauthorized"],
        "attestation": ci_attestation(ctx, sys_id, cls, doc),
        "dependents": [{"sys_id": d, "name": n or "(adsız)", "class": c, "class_label": pres.class_label(c), "key": k}
                       for d, n, c, k in dep_rows if ctx.grant(c, "read").allowed],
        "depends_on": [{"sys_id": d, "name": n or "(adsız)", "class": c, "class_label": pres.class_label(c)}
                       for d, n, c in parent_rows if ctx.grant(c, "read").allowed],
        "staleness": ci_staleness(ctx, sys_id, cls),
        "findings": ci_findings(ctx, sys_id),
        "golden_version": doc.get("golden_version"), "sys_updated_on": doc.get("sys_updated_on"),
        **({"security_posture": posture} if posture is not None else {}),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _change_control(ctx: Ctx, sys_id: str, fields: dict[str, Any], grant: Any, pres: Any,
                    refs: dict[str, Any]) -> dict[str, Any]:
    """Değişiklik denetimi (ADR-0025): gözlenen değişiklikler (alan hassasiyetine göre süzülür) ve CI'a bağlı değişiklik
    kayıtları. CI Evren'de doğup replikaya gittiyse ServiceNow'daki sys_id'si de aranır."""
    with ctx.conn.cursor() as cur:
        cur.execute(
            """SELECT c.attribute, c.old_value, c.new_value, c.data_source, c.observed_from, c.observed_at, c.verdict,
                      c.change_number, c.detail->>'explanation', COALESCE((c.detail->>'late')::bool, false),
                      c.evaluated_at, a.status
                 FROM insight.config_change c LEFT JOIN insight.anomaly a ON a.sys_id = c.anomaly_sys_id
                WHERE c.ci_sys_id = %s ORDER BY c.observed_at DESC, c.attribute LIMIT 50""", (sys_id,))
        observations = [
            {"attribute": a, "label": fields.get(a, {}).get("label_tr", a), "old": o, "new": n,
             "old_display": pres.display(a, o, refs), "new_display": pres.display(a, n, refs), "source": ds,
             "observed_from": _iso(f), "observed_at": _iso(t), "verdict": v, "verdict_label": VERDICT_TR[v],
             "change_number": num, "explanation": why, "late": late, "evaluated_at": _iso(ev),
             "anomaly_status": ast, "anomaly_status_label": ANOMALY_STATUS.get(ast) if ast else None}
            for a, o, n, ds, f, t, v, num, why, late, ev, ast in cur.fetchall()
            if grant.can_see(fields.get(a, {}).get("sensitivity", "internal"))]
        cur.execute("SELECT sn_sys_id FROM sync.sn_ci_map WHERE evren_sys_id = %s AND sn_sys_id IS NOT NULL", (sys_id,))
        ids = [sys_id, *(r[0] for r in cur.fetchall())]
        cur.execute(
            """SELECT cr.number, cr.short_description, cr.type, cr.state, cr.approval, cr.start_date, cr.end_date,
                      cr.work_start, cr.work_end
                 FROM ref.sn_change_request cr
                WHERE cr.cmdb_ci = ANY(%(ids)s::bpchar[])
                   OR EXISTS (SELECT 1 FROM ref.sn_task_ci t WHERE t.task = cr.sys_id
                                                             AND t.ci_item = ANY(%(ids)s::bpchar[]))
                ORDER BY COALESCE(cr.work_start, cr.start_date) DESC NULLS LAST, cr.number DESC LIMIT 20""",
            {"ids": ids})
        changes = [{"number": num, "short_description": desc, "type": t, "type_label": TYPE_TR.get(t or "", "—"),
                    "state": st, "state_label": STATE_TR.get(st or "", "—"), "approval": ap,
                    "approval_label": APPROVAL_TR.get(ap or "", "—"), "start_date": _iso(sd), "end_date": _iso(ed),
                    "work_start": _iso(ws), "work_end": _iso(we)}
                   for num, desc, t, st, ap, sd, ed, ws, we in cur.fetchall()]
        cur.execute(
            """SELECT sys_id, status, severity, detected_at, explanation, related_change, reviewed_by, reviewed_at,
                      review_note
                 FROM insight.anomaly WHERE ci_sys_id = %s AND kind = 'unauthorized_change'
                ORDER BY detected_at DESC LIMIT 20""", (sys_id,))
        anomalies = [{"sys_id": i, "status": st, "status_label": ANOMALY_STATUS.get(st, st), "severity": sev,
                      "detected_at": _iso(at), "explanation": why, "related_change": rel, "reviewed_by": by,
                      "reviewed_at": _iso(rat), "review_note": note}
                     for i, st, sev, at, why, rel, by, rat, note in cur.fetchall()]
    open_unauthorized = sum(1 for a in anomalies if a["status"] in ("open", "acknowledged"))
    return {"observations": observations, "changes": changes, "anomalies": anomalies,
            "open_unauthorized": open_unauthorized, "can_review": ctx.grant("anomaly", "anomaly_review").allowed}


def _security_posture(ctx: Ctx, sys_id: str, grant: Any) -> dict[str, Any] | None:
    """KISITLI güvenlik duruşu (ADR-0023): yalnızca ``restricted`` okuma yetkisiyle (Güvenlik okuyucu, Denetçi).
    Yetkisiz kullanıcıya bölümün varlığı da gösterilmez; her gösterim denetim izine ``restricted_read`` yazılır."""
    if not grant.can_see("restricted"):
        return None
    row = ctx.conn.execute(
        """SELECT data_source, protection_status, health_status, exposure_level, risk_score, vulnerabilities_critical,
                  vulnerabilities_high, last_seen, observed_at FROM cmdb.ci_security_posture WHERE ci_sys_id = %s""",
        (sys_id,)).fetchone()
    if row is None:
        return None
    ds, protection, health, exposure, risk, crit, high, seen, observed = row
    ctx.security_event("restricted_read", "allow", f"cmdb_ci:{sys_id}", {"table": "cmdb.ci_security_posture"})
    return {"data_source": ds, "protection_status": protection, "health_status": health, "exposure_level": exposure,
            "risk_score": risk, "vulnerabilities_critical": crit, "vulnerabilities_high": high,
            "last_seen": seen.isoformat() if seen else None, "observed_at": observed.isoformat()}


@router.patch("/ci/{sys_id}", dependencies=[Depends(require_csrf)], status_code=202)
def edit_ci(sys_id: str, body: ManualEditRequest, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Elle düzenleme isteği: değerler Manual kaynak kaydı olarak uzlaştırmaya girer (202: uzlaştırıcı uygular)."""
    _valid_sysid(sys_id)
    p = ctx.principal
    row = ctx.conn.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s", (sys_id,)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "CI bulunamadı.")
    grant = ctx.require(row[0], "write")
    values = ctx.state.edit_policy.validate(ctx.conn, row[0], grant, body.attributes)
    digest = request_hash(sys_id, "edit", values, body.note)
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""INSERT INTO ingest.manual_edit (ci_sys_id, attributes, note, requested_by)
                       VALUES (%s, %s, %s, %s) RETURNING sys_id""", (sys_id, Jsonb(values), body.note, p.audit_id))
        req_id = cur.fetchone()[0]  # type: ignore[index]
        payload = {"id": req_id, "ci_sys_id": sys_id, "attributes": sorted(values), "by": p.audit_id}
        cur.execute("INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload) "
                    "VALUES ('cmdb_ci', %s, 'evren.ci.edit.requested.v1', %s)", (sys_id, Jsonb(payload)))
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'ci_edit', %s, 'allow', %s)", (p.audit_id, f"cmdb_ci:{sys_id}", Jsonb(payload)))
        response = {"id": req_id, "status": "pending", "status_label": SPLIT_STATUS["pending"], "ci_sys_id": sys_id,
                    "attributes": sorted(values), "replayed": False,
                    "message": "Düzenleme kaydedildi; uzlaştırıcı değerleri Manual kaynağı olarak uyguladığında CI "
                               "güncellenir."}
        remember(cur, p.audit_id, body.idempotency_key, f"/api/v1/ci/{sys_id}", digest, 202, response)
    return JSONResponse(response, status_code=202)


@router.post("/ci/{sys_id}/split", dependencies=[Depends(require_csrf)], status_code=202)
def split_ci(sys_id: str, body: SplitRequest, ctx: Ctx = Depends(context)) -> JSONResponse:
    """Yanlış birleştirilmiş kaynak kaydını CI'dan ayırma isteği (202: uzlaştırıcı uygular)."""
    _valid_sysid(sys_id)
    p = ctx.principal
    md = ctx.state.metadata
    digest = request_hash(sys_id, body.model_dump(exclude={"idempotency_key"}))
    # Yetki denetimleri işlem dışında: ret kaydı işlemle birlikte geri alınmasın.
    row = ctx.conn.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s", (sys_id,)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "CI bulunamadı.")
    ctx.require(row[0], "split")
    target_cls = None
    if body.target_ci and body.target_ci != sys_id:
        trow = ctx.conn.execute("SELECT sys_class_name FROM cmdb.cmdb_ci WHERE sys_id = %s",
                                (body.target_ci,)).fetchone()
        if trow is None:
            raise ApiError(422, "hedef_yok", "Hedef CI bulunamadı.")
        target_cls = trow[0]
        ctx.require(target_cls, "merge")
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute(
            """SELECT l.ci_sys_id, l.active, l.method, l.data_source, l.source_key, sr.class_hint,
                      (SELECT count(*) FROM ingest.identity_link o WHERE o.ci_sys_id = l.ci_sys_id AND o.active
                          AND o.sys_id <> l.sys_id AND o.data_source <> 'Manual')
                 FROM ingest.identity_link l JOIN ingest.source_record sr USING (data_source, source_key)
                WHERE l.sys_id = %s""", (body.link_id,))
        link = cur.fetchone()
        if link is None or link[0] != sys_id or not link[1]:
            raise ApiError(422, "bag_yok", "Bu bağ, CI'ın etkin kaynak kayıtları arasında değil.")
        _, _, method, ds, key, class_hint, others = link
        reg = md.sources
        if blocker := _split_blocker(method, others, ds in reg.sources and reg.may_create_ci(ds),
                                     body.target_ci is not None, ds):
            raise ApiError(*blocker)
        if body.target_ci == sys_id:
            raise ApiError(422, "hedef_ayni", "Hedef CI, ayrılan CI ile aynı olamaz.")
        h = md.hierarchy
        if target_cls and class_hint in h and target_cls in h and not h.compatible(class_hint, target_cls):
            raise ApiError(422, "sinif_uyumsuz", "Kaynak kaydının sınıfı hedef CI ile uyumsuz.",
                           [{"kayit": class_hint, "hedef": target_cls}])
        try:
            with ctx.conn.transaction():
                cur.execute(
                    """INSERT INTO ingest.split_request (link_sys_id, ci_sys_id, data_source, source_key, target_ci,
                                                        note, requested_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                    (body.link_id, sys_id, ds, key, body.target_ci, body.note, p.audit_id))
        except psycopg.errors.UniqueViolation:
            raise ApiError(409, "bekleyen_ayirma", "Bu kaynak kaydı için bekleyen bir ayırma isteği var.") from None
        req_id = cur.fetchone()[0]  # type: ignore[index]
        payload = {"id": req_id, "ci_sys_id": sys_id, "data_source": ds, "source_key": key,
                   "target_ci": body.target_ci, "by": p.audit_id}
        cur.execute("INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload) "
                    "VALUES ('cmdb_ci', %s, 'evren.ci.split.requested.v1', %s)", (sys_id, Jsonb(payload)))
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'ci_split', %s, 'allow', %s)", (p.audit_id, f"cmdb_ci:{sys_id}", Jsonb(payload)))
        response = {"id": req_id, "status": "pending", "status_label": SPLIT_STATUS["pending"], "ci_sys_id": sys_id,
                    "data_source": ds, "source_key": key, "target_ci": body.target_ci, "replayed": False,
                    "message": "Ayırma isteği kaydedildi; uzlaştırıcı uyguladığında kayıt "
                               + ("seçilen CI'a" if body.target_ci else "yeni bir CI'a")
                               + " taşınır ve iki CI yeniden uzlaştırılır."}
        remember(cur, p.audit_id, body.idempotency_key, f"/api/v1/ci/{sys_id}/split", digest, 202, response)
    return JSONResponse(response, status_code=202)


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
