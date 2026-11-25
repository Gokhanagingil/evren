"""Platform uç noktaları: kimlik, özet, veri kaynakları, kurallar, GRC kontrolleri."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from psycopg import sql

from ..deps import Ctx, context
from ..present import COVERAGE_ROLES

router = APIRouter(prefix="/api/v1", tags=["platform"])

_SERVER = sql.SQL("ci.sys_class_name IN (SELECT descendant FROM meta.class_closure WHERE ancestor = 'cmdb_ci_server')")
_NETGEAR = sql.SQL("ci.sys_class_name IN (SELECT descendant FROM meta.class_closure WHERE ancestor = 'cmdb_ci_netgear')")
_PROD = sql.SQL("ci.used_for = 'Production'")


def _and(*parts: sql.Composable) -> sql.Composed:
    return sql.SQL(" AND ").join(parts)


def _not(part: sql.Composable) -> sql.Composed:
    return sql.SQL("NOT {}").format(part)


def _coverage_sql(flag: str) -> sql.Composed:
    """Kapsam koşulu: CI'a bu roldeki bir veri kaynağı bağlı mı? (rol adları sabit sözlükten, literal olarak)"""
    return sql.SQL("EXISTS (SELECT 1 FROM ingest.identity_link l JOIN ingest.data_source d ON d.name = l.data_source "
                   "WHERE l.ci_sys_id = ci.sys_id AND l.active AND d.role IN ({}))").format(
        sql.SQL(", ").join(sql.Literal(r) for r in COVERAGE_ROLES[flag]))


@router.get("/me")
def me(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    p = ctx.principal
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT name, label_tr FROM iam.role WHERE name = ANY(%s) ORDER BY name", (sorted(p.roles),))
        roles = [{"name": n, "label_tr": label} for n, label in cur.fetchall()]
    return {"id": p.id, "name": p.name, "mode": p.mode, "roles": roles,
            "permissions": ctx.state.authorizer.summary(ctx.perms),
            "can": {"review_match": ctx.grant("match_queue", "review_match").allowed,
                    "read_ci": ctx.grant("cmdb_ci", "read").allowed}}


def _source_rows(ctx: Ctx) -> list[dict[str, Any]]:
    md = ctx.state.metadata
    reg = md.sources
    out = []
    with ctx.conn.cursor() as cur:
        cur.execute(
            """SELECT data_source, status, count(*) FROM ingest.source_record GROUP BY 1, 2""")
        status_counts: dict[str, dict[str, int]] = {}
        for ds, st, n in cur.fetchall():
            status_counts.setdefault(ds, {})[st] = n
        cur.execute("""SELECT data_source, count(*) FILTER (WHERE method = 'import' OR method = 'manual'),
                              count(*) FILTER (WHERE method IN ('rule', 'score')) FROM ingest.identity_link
                        WHERE active GROUP BY 1""")
        links = {ds: (a, b) for ds, a, b in cur.fetchall()}
        cur.execute("""SELECT data_source, kind, count(*) FROM ingest.match_candidate WHERE status = 'open'
                        GROUP BY 1, 2""")
        queue: dict[str, dict[str, int]] = {}
        for ds, kind, n in cur.fetchall():
            queue.setdefault(ds, {})[kind] = n
        cur.execute(
            """SELECT data_source, started_at, finished_at, record_count, changed_count, error_count, completed,
                      breaker_state, breaker_reasons, baseline
                 FROM (SELECT *, row_number() OVER (PARTITION BY data_source ORDER BY started_at DESC) AS rn
                         FROM ingest.source_run) r
                WHERE rn <= 11 ORDER BY data_source, started_at DESC""")
        runs: dict[str, list[dict[str, Any]]] = {}
        for ds, st, fin, rec, chg, err, comp, state, reasons, base in cur.fetchall():
            runs.setdefault(ds, []).append({
                "started_at": st.isoformat(), "finished_at": fin.isoformat() if fin else None, "records": rec,
                "changed": chg, "errors": err, "completed": comp, "state": state or "warn", "reasons": list(reasons),
                "baseline": float(base) if base is not None else None})
    for src in md.data_sources:
        name = src["name"]
        role = reg.roles[src["role"]]
        s = reg.source(name)
        counts = status_counts.get(name, {})
        q = queue.get(name, {})
        out.append({
            "name": name, "label_tr": src["label_tr"], "role": s.role, "role_label": role.label_tr, "kind": role.kind,
            "lifecycle": s.lifecycle, "role_rank": s.role_rank, "writes_attributes": reg.writes_attributes(name),
            "may_create_ci": reg.may_create_ci(name), "product": dict(s.product), "connector": s.connector,
            "mappings": list(s.mappings), "schedule": s.schedule, "authoritative_for_tr": s.authoritative_for_tr,
            "open_question": s.open_question,
            "stats": {"records": sum(counts.values()), "linked": links.get(name, (0, 0))[1] + links.get(name, (0, 0))[0],
                      "review": q.get("review", 0), "insufficient": q.get("insufficient", 0),
                      "missing": counts.get("missing", 0), "new": 0},
            "runs": runs.get(name, []), "breaker": (runs.get(name) or [{}])[0].get("state"),
        })
    return out


@router.get("/sources")
def sources(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    md = ctx.state.metadata
    return {"roles": [{"name": r["name"], "label_tr": r["label_tr"], "kind": r["kind"],
                       "description_tr": r.get("description_tr", ""),
                       "ci_attribute_source": bool(r.get("ci_attribute_source", True))} for r in md.source_roles],
            "sources": _source_rows(ctx)}


@router.get("/rules")
def rules(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    return ctx.state.rules_view


def _breaker_states(ctx: Ctx) -> dict[str, str]:
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (data_source) data_source, COALESCE(breaker_state, 'warn'), completed
                         FROM ingest.source_run ORDER BY data_source, started_at DESC""")
        return {ds: (st if comp else "open") for ds, st, comp in cur.fetchall()}


def _control(ctx: Ctx, cid: str, title: str, role: str | None, where: sql.Composable, query_ref: str, why: str,
             states: dict[str, str]) -> dict[str, Any]:
    reg = ctx.state.metadata.sources
    deps = [s.name for s in reg.sources_in_role(role)] if role else []
    collection = "complete" if all(states.get(d) == "ok" for d in deps) else "partial"
    with ctx.conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT ci.sys_id, ci.name FROM cmdb.cmdb_ci ci WHERE {} ORDER BY lower(ci.name) LIMIT 500")
                    .format(where))
        evidence = [{"sys_id": a, "name": b} for a, b in cur.fetchall()]
    status = "inconclusive" if collection != "complete" else ("fail" if evidence else "pass")
    return {"id": cid, "title": title, "status": status, "why": why, "evidence": evidence,
            "provenance": {"source_system": "Evren", "query_reference": query_ref, "collection_status": collection,
                           "depends_on": ", ".join(deps) or None, "evaluated_at": datetime.now(UTC).isoformat()}}


@router.get("/controls")
def controls(ctx: Ctx = Depends(context)) -> list[dict[str, Any]]:
    """GRC Platformu'na giden CMDB kontrolleri. Bağımlı rolün son çalışması sağlıklı değilse ``inconclusive``."""
    ctx.require("cmdb_ci", "read")
    st = _breaker_states(ctx)
    prod = _and(_SERVER, _PROD)
    return [
        _control(ctx, "CMDB-01", "İzlenmeyen üretim sunucusu", "Monitoring", _and(prod, _not(_coverage_sql("monitoring"))),
                 "sys_class_nameINSTANCEOFcmdb_ci_server^used_for=Production^evren.coverage.monitoring=false",
                 "İzleme rolündeki kaynaklarda kaydı olmayan üretim sunucuları.", st),
        _control(ctx, "CMDB-02", "Uç nokta koruması olmayan sunucu", "EndpointSecurity",
                 _and(_SERVER, _not(_coverage_sql("security"))),
                 "sys_class_nameINSTANCEOFcmdb_ci_server^evren.coverage.security=false",
                 "Uç nokta güvenliği rolündeki kaynaklarla eşleşmeyen sunucular.", st),
        _control(ctx, "CMDB-03", "Sahibi olmayan üretim CI'ı", None, _and(prod, sql.SQL("ci.owned_by IS NULL")),
                 "sys_class_nameINSTANCEOFcmdb_ci_server^used_for=Production^owned_byISEMPTY",
                 "Yönetişim alanı boş; sahip ataması gerekli.", st),
        _control(ctx, "CMDB-04", "Mevcut CMDB'de kaydı olmayan sunucu (kapsam boşluğu)", "ITSM",
                 _and(_SERVER, _not(_coverage_sql("itsm"))),
                 "sys_class_nameINSTANCEOFcmdb_ci_server^evren.coverage.itsm=false",
                 "Keşifte bulunan ama mevcut CMDB'de olmayan sunucular; gölge mod raporu.", st),
    ]


@router.get("/summary")
def summary(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    with ctx.conn.cursor() as cur:
        cur.execute(sql.SQL("""SELECT count(*), count(*) FILTER (WHERE {s}), count(*) FILTER (WHERE {n}),
                               count(*) FILTER (WHERE ci.sys_class_name = 'cmdb_ci_vmware_instance'),
                               count(*) FILTER (WHERE {s} AND NOT {m}), count(*) FILTER (WHERE {s} AND NOT {e}),
                               count(*) FILTER (WHERE {s} AND NOT {i})
                          FROM cmdb.cmdb_ci ci""").format(
            s=_SERVER, n=_NETGEAR, m=_coverage_sql("monitoring"), e=_coverage_sql("security"), i=_coverage_sql("itsm")))
        total, servers, net, vms, unmon, unprot, noitsm = cur.fetchone()  # type: ignore[misc]
        cur.execute("SELECT kind, count(*) FROM ingest.match_candidate WHERE status = 'open' GROUP BY 1")
        q = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM cmdb.ci_reconciliation_state WHERE conflicts - '_dropped' <> '{}'::jsonb")
        unauth = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT count(*) FROM ingest.source_record")
        records = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT EXISTS (SELECT 1 FROM ingest.source_run WHERE notes LIKE 'sentetik%%')")
        synthetic = cur.fetchone()[0]  # type: ignore[index]
        worker = _worker_status(cur)
    return {"generated_at": datetime.now(UTC).isoformat(), "ci_count": total, "server_count": servers,
            "network_count": net, "vm_count": vms, "queue_review": q.get("review", 0),
            "queue_insufficient": q.get("insufficient", 0),
            "queue_unmatched": q.get("unmatched_shadow", 0) + q.get("unmatched_retiring", 0),
            "unmonitored": unmon, "unprotected": unprot, "not_in_itsm": noitsm, "unauthorized_attempts": unauth,
            "records": records, "synthetic": synthetic, "worker": worker}


def _worker_status(cur: Any) -> dict[str, Any]:
    """Uzlaştırıcı çalışanının durumu ve bekleyen insan istekleri (göç 0009).

    ``state``: ``ok`` (son tarama aralığının iki katı içinde görüldü), ``stale`` (görülmüyor), ``none`` (hiç
    çalışmadı). Bekleyen istek varken çalışan yoksa arayüz uyarır.
    """
    cur.execute("""SELECT (SELECT count(*) FROM ingest.match_candidate
                            WHERE applied_at IS NULL AND status IN ('accepted', 'created_new', 'rejected')),
                          (SELECT count(*) FROM ingest.split_request WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.manual_edit WHERE status = 'pending'),
                          (SELECT count(*) FROM ingest.match_candidate WHERE apply_error IS NOT NULL)
                          + (SELECT count(*) FROM ingest.split_request WHERE result_note LIKE 'Uygulanamadı:%%')
                          + (SELECT count(*) FROM ingest.manual_edit WHERE result_note LIKE 'Uygulanamadı:%%')""")
    decisions, splits, edits, failed = cur.fetchone()
    cur.execute("""SELECT seen_at, poll_seconds, stopped_at IS NULL AND seen_at > now() - 2 * poll_seconds * interval '1 second'
                     FROM ingest.worker_heartbeat ORDER BY seen_at DESC LIMIT 1""")
    row = cur.fetchone()
    state = "none" if row is None else ("ok" if row[2] else "stale")
    return {"state": state, "last_seen": row[0].isoformat() if row else None,
            "poll_seconds": row[1] if row else None, "failed": failed,
            "pending": {"decisions": decisions, "splits": splits, "edits": edits,
                        "total": decisions + splits + edits}}


@router.get("/meta/classes")
def classes(ctx: Ctx = Depends(context)) -> list[dict[str, Any]]:
    """Golden record'da kaydı olan sınıflar (filtre seçenekleri için); okunamayan sınıflar listelenmez."""
    ctx.require("cmdb_ci", "read")
    pres = ctx.state.presenter
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT sys_class_name, count(*) FROM cmdb.cmdb_ci GROUP BY 1 ORDER BY 1")
        rows = cur.fetchall()
    return [{"name": n, "label_tr": pres.class_label(n), "count": c} for n, c in rows if ctx.grant(n, "read").allowed]
