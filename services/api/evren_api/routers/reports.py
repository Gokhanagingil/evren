"""Gölge mod raporu (F0-13): mevcut CMDB'nin (ITSM rolü; bugün ServiceNow) Evren gözlemleriyle karşılaştırılması.

Evren ana kaynak olmadan önce ölç, karşılaştır, kanıtla:

* **Kapsam:** ITSM'de olup hiçbir keşif rolünün görmediği CI'lar (eski/hayalet kayıt adayı) ve keşifte olup
  ITSM'de olmayanlar; sınıf × rol kapsam tablosu.
* **Mükerrer adayları:** aynı kimlik anahtarını (seri, UUID, FQDN, MAC, ad) paylaşan ITSM CI'ları. Yalnız ad
  ortaksa "olası"; seri/UUID çelişiyorsa "çelişkili" (ad tek başına kimlik değildir, CLAUDE.md kural 7).
* **Değer farkları:** ITSM'deki değerin yetkili kaynağın değerinden farklı olduğu öznitelikler; yalnız yazım
  (büyük/küçük harf, alan adı eki) farkı ayrı sayılır.
* **Gölge kaynak uyumu:** gölge moddaki ürünün değerlerinin yetkili değerle uyumu (ürün değişimi, ADR-0009).

Kurallar ürüne değil role bağlıdır: ITSM, keşif, izleme rolleri ``ingest.data_source.role``'dan okunur.
Bağımlı rollerin son çalışması sağlıklı değilse rapor ``inconclusive`` olur (GRC sözleşmesi, ``provenance``).
Kanıt anlık görüntüsü değiştirilemez (göç 0010) ve SHA-256 ile doğrulanır.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from ..deps import Ctx, context, require_csrf
from ..errors import ApiError
from ..idempotency import remember, replay, request_hash
from ..present import COVERAGE_ROLES
from .platform import _breaker_states

router = APIRouter(prefix="/api/v1/reports", tags=["rapor"])

DISCOVERY = COVERAGE_ROLES["discovery"]
ITSM = COVERAGE_ROLES["itsm"]
#: Mükerrer grubunu kuran anahtarlar ve gücü. IP grup kurmaz (yeniden kullanılır), yalnız kanıt olarak gösterilir.
KEY_STRENGTH = {"serial": "kesin", "uuid": "kesin", "correlation": "kesin", "fqdn": "güçlü", "mac": "güçlü",
                "compact": "olası", "name": "olası"}
KEY_LABEL = {"serial": "Seri numarası", "uuid": "BIOS UUID", "correlation": "Sanal makine kimliği", "fqdn": "FQDN",
             "mac": "MAC adresi", "compact": "Ad (sadeleştirilmiş)", "name": "Ad", "ip": "IP adresi"}
STRENGTH_RANK = {"kesin": 0, "güçlü": 1, "olası": 2}
VERDICT = {
    "kesin": "Kesin mükerrer: aynı donanım/sanal makine kimliği",
    "güçlü": "Güçlü aday: aynı FQDN veya MAC adresi (seri farklıysa eski kayıt olabilir)",
    "olası": "Olası: yalnız ad ortak — doğrulayın",
    "çelişkili": "Çelişkili: ad ortak ama seri/UUID farklı — büyük olasılıkla farklı cihazlar",
}
LIST_LIMIT = 200
SNAPSHOT_KIND = "shadow_report"


def _allowed_classes(ctx: Ctx) -> list[str]:
    return [c for c in ctx.state.metadata.class_map if ctx.grant(c, "read").allowed]


def _visible_attributes(ctx: Ctx) -> list[str]:
    """Okuma izninin hassasiyet sınırı içindeki öznitelikler (değer farkı listesinde gösterilebilenler)."""
    grant = ctx.grant("cmdb_ci", "read")
    md = ctx.state.metadata
    seen: dict[str, str] = {}
    for cls in md.class_map:
        for name, f in md.fields_of(cls, inherited=False).items():
            sens = f.get("sensitivity", "internal")
            if name not in seen or _rank(sens) > _rank(seen[name]):
                seen[name] = sens
    return sorted(n for n, s in seen.items() if grant.can_see(s))


def _rank(sensitivity: str) -> int:
    return {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}.get(sensitivity, 3)


def _roles_cte() -> sql.Composable:
    return sql.SQL("""cov AS (SELECT l.ci_sys_id, array_agg(DISTINCT d.role) AS roles
                                FROM ingest.identity_link l JOIN ingest.data_source d ON d.name = l.data_source
                               WHERE l.active GROUP BY 1)""")


def _arr(values: tuple[str, ...]) -> sql.Composable:
    return sql.SQL("ARRAY[{}]::text[]").format(sql.SQL(", ").join(sql.Literal(v) for v in values))


def _coverage(ctx: Ctx, classes: list[str]) -> dict[str, Any]:
    pres = ctx.state.presenter
    disc, itsm = _arr(DISCOVERY), _arr(ITSM)
    mon, sec = _arr(COVERAGE_ROLES["monitoring"]), _arr(COVERAGE_ROLES["security"])
    with ctx.conn.cursor() as cur:
        cur.execute(sql.SQL("""WITH {cte}
            SELECT ci.sys_class_name, count(*),
                   count(*) FILTER (WHERE cov.roles && {itsm}),
                   count(*) FILTER (WHERE cov.roles && {disc}),
                   count(*) FILTER (WHERE cov.roles && {mon}),
                   count(*) FILTER (WHERE cov.roles && {sec}),
                   count(*) FILTER (WHERE cov.roles && {itsm} AND NOT cov.roles && {disc}),
                   count(*) FILTER (WHERE cov.roles && {disc} AND NOT cov.roles && {itsm})
              FROM cmdb.cmdb_ci ci LEFT JOIN cov ON cov.ci_sys_id = ci.sys_id
             WHERE ci.sys_class_name = ANY(%s)
             GROUP BY 1 ORDER BY 2 DESC, 1""").format(cte=_roles_cte(), itsm=itsm, disc=disc, mon=mon, sec=sec),
            (classes,))
        rows = cur.fetchall()

        def listing(cond: sql.Composable) -> list[dict[str, Any]]:
            cur.execute(sql.SQL("""WITH {cte}
                SELECT ci.sys_id, ci.name, ci.sys_class_name, ci.ip_address, cov.roles
                  FROM cmdb.cmdb_ci ci JOIN cov ON cov.ci_sys_id = ci.sys_id
                 WHERE ci.sys_class_name = ANY(%s) AND {cond}
                 ORDER BY lower(ci.name), ci.sys_id LIMIT %s""").format(cte=_roles_cte(), cond=cond),
                (classes, LIST_LIMIT))
            return [{"sys_id": s, "name": n or "(adsız)", "class_label": pres.class_label(c), "ip_address": ip,
                     "roles": sorted(r)} for s, n, c, ip, r in cur.fetchall()]

        itsm_only = listing(sql.SQL("cov.roles && {itsm} AND NOT cov.roles && {disc}").format(itsm=itsm, disc=disc))
        not_in_itsm = listing(sql.SQL("cov.roles && {disc} AND NOT cov.roles && {itsm}").format(itsm=itsm, disc=disc))
    matrix = [{"class": c, "class_label": pres.class_label(c), "total": t, "itsm": i, "discovery": d, "monitoring": m,
               "security": s, "itsm_only": io, "not_in_itsm": ni} for c, t, i, d, m, s, io, ni in rows]
    tot = {k: sum(r[k] for r in matrix) for k in ("total", "itsm", "discovery", "monitoring", "security", "itsm_only",
                                                  "not_in_itsm")}
    seen = tot["itsm"] - tot["itsm_only"]
    return {"totals": {**tot, "itsm_seen_by_discovery": seen,
                       "itsm_seen_pct": round(100 * seen / tot["itsm"], 1) if tot["itsm"] else None},
            "matrix": matrix, "itsm_only": itsm_only, "not_in_itsm": not_in_itsm}


def _duplicates(ctx: Ctx, classes: list[str]) -> dict[str, Any]:
    pres = ctx.state.presenter
    itsm = _arr(ITSM)
    with ctx.conn.cursor() as cur:
        cur.execute(sql.SQL("""WITH itsm_ci AS (
                SELECT DISTINCT l.ci_sys_id FROM ingest.identity_link l JOIN ingest.data_source d ON d.name = l.data_source
                 JOIN cmdb.cmdb_ci ci ON ci.sys_id = l.ci_sys_id
                WHERE l.active AND d.role = ANY({itsm}) AND ci.sys_class_name = ANY(%s))
            SELECT k.key_type, k.value, array_agg(DISTINCT k.ci_sys_id::text ORDER BY k.ci_sys_id::text)
              FROM ingest.identity_key k JOIN itsm_ci USING (ci_sys_id)
             WHERE k.key_type = ANY(%s)
             GROUP BY k.key_type, k.family, k.value HAVING count(DISTINCT k.ci_sys_id) > 1
             ORDER BY 1, 2 LIMIT 5000""").format(itsm=itsm), (classes, [*KEY_STRENGTH, "ip"]))
        shared = cur.fetchall()
        # Birleşim-bul: herhangi bir grup kuran anahtarla bağlı CI'lar aynı gruptadır.
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for key_type, _value, cis in shared:
            if key_type in KEY_STRENGTH:
                for other in cis[1:]:
                    parent[find(other)] = find(cis[0])
        groups: dict[str, set[str]] = {}
        for x in list(parent):
            groups.setdefault(find(x), set()).add(x)
        members = sorted({c for g in groups.values() for c in g})
        info: dict[str, dict[str, Any]] = {}
        if members:
            cur.execute(sql.SQL("""SELECT ci.sys_id, ci.name, ci.sys_class_name, ci.serial_number,
                                          ci.class_attributes->>'bios_uuid', ci.fqdn, ci.ip_address, ci.sys_updated_on,
                                          EXISTS (SELECT 1 FROM ingest.identity_link l
                                                    JOIN ingest.data_source d ON d.name = l.data_source
                                                   WHERE l.ci_sys_id = ci.sys_id AND l.active AND d.role = ANY({disc}))
                                     FROM cmdb.cmdb_ci ci WHERE ci.sys_id = ANY(%s)""").format(disc=_arr(DISCOVERY)),
                        (members,))
            info = {r[0]: {"sys_id": r[0], "name": r[1] or "(adsız)", "class_label": pres.class_label(r[2]),
                           "serial_number": r[3], "bios_uuid": r[4], "fqdn": r[5], "ip_address": r[6],
                           "updated": r[7].isoformat() if r[7] else None, "seen_by_discovery": r[8]}
                    for r in cur.fetchall()}
    out = []
    for cis in groups.values():
        evidence = sorted(({"key": KEY_LABEL.get(t, t), "key_type": t, "value": v,
                            "strength": KEY_STRENGTH.get(t, "kanıt")} for t, v, ids in shared if set(ids) & cis),
                          key=lambda e: (STRENGTH_RANK.get(e["strength"], 9), e["key"], e["value"]))
        best = min((e["strength"] for e in evidence if e["strength"] in STRENGTH_RANK), key=STRENGTH_RANK.get)
        serials = {info[c]["serial_number"].upper() for c in cis if info.get(c, {}).get("serial_number")}
        uuids = {info[c]["bios_uuid"].lower() for c in cis if info.get(c, {}).get("bios_uuid")}
        conflict = best != "kesin" and (len(serials) > 1 or len(uuids) > 1)
        # Yalnız ad ortak ve donanım kimliği farklı → büyük olasılıkla farklı cihazlar. FQDN/MAC ortak ama seri
        # farklıysa aynı makinenin eski kaydı olabilir (ör. fizikselden sanala taşıma): güçlü aday, not düşülür.
        verdict = "çelişkili" if conflict and best == "olası" else best
        rows = sorted((info[c] for c in cis if c in info), key=lambda x: (x["name"].lower(), x["sys_id"]))
        seen = [r for r in rows if r["seen_by_discovery"]]
        unseen = [r for r in rows if not r["seen_by_discovery"]]
        if verdict == "çelişkili":
            advice = "Kayıtları birleştirmeyin; ad çakışmasını ServiceNow'da ad düzeltmesiyle giderin."
        elif seen and unseen:
            advice = (f"Keşfin görmediği {len(unseen)} kayıt eski olabilir: ServiceNow'da emekliye ayırma adayı "
                      f"({', '.join(r['sys_id'][:8] for r in unseen)}…); keşfin gördüğü kayıt korunur.")
        else:
            advice = "Kayıtları karşılaştırıp birini koruyun, diğerlerini ServiceNow'da emekliye ayırın."
        out.append({"verdict": verdict, "verdict_label": VERDICT[verdict], "evidence": evidence,
                    "hardware_conflict": conflict, "advice": advice, "cis": rows})
    order = {"kesin": 0, "güçlü": 1, "olası": 2, "çelişkili": 3}
    out.sort(key=lambda g: (order[g["verdict"]], g["cis"][0]["name"].lower() if g["cis"] else ""))
    counts = {k: sum(1 for g in out if g["verdict"] == k) for k in order}
    return {"groups": out[:LIST_LIMIT], "counts": counts, "group_count": len(out),
            "ci_count": sum(len(g["cis"]) for g in out)}


_COSMETIC = sql.SQL("""(lower(btrim(o.value->>1)) = lower(btrim(cur.golden))
                        OR (w.key IN ('name', 'host_name')
                            AND split_part(lower(btrim(o.value->>1)), '.', 1) = split_part(lower(btrim(cur.golden)), '.', 1)))""")


def _drift(ctx: Ctx, classes: list[str]) -> dict[str, Any]:
    pres = ctx.state.presenter
    fields = _visible_attributes(ctx)
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT name FROM ingest.data_source WHERE role = ANY(%s)", (list(ITSM),))
        itsm_sources = [r[0] for r in cur.fetchall()]
        base = sql.SQL("""FROM cmdb.ci_reconciliation_state s
              JOIN cmdb.cmdb_ci ci ON ci.sys_id = s.ci_sys_id
              CROSS JOIN LATERAL jsonb_each(s.winners) w
              CROSS JOIN LATERAL jsonb_array_elements(w.value->'overridden') o
              CROSS JOIN LATERAL (SELECT COALESCE(to_jsonb(ci)->>w.key, ci.class_attributes->>w.key) AS golden) cur
             WHERE ci.sys_class_name = ANY(%s) AND o.value->>0 = ANY(%s) AND w.key = ANY(%s)""")
        params = (classes, itsm_sources, fields)
        cur.execute(sql.SQL("""SELECT w.key, count(*), count(*) FILTER (WHERE {cos}) {base}
                               GROUP BY 1 ORDER BY 2 DESC, 1""").format(cos=_COSMETIC, base=base), params)
        by_attr = [{"attribute": a, "count": n, "cosmetic": c, "real": n - c} for a, n, c in cur.fetchall()]
        cur.execute(sql.SQL("""SELECT ci.sys_id, ci.name, ci.sys_class_name, w.key, o.value->>0, o.value->>1,
                                      cur.golden, w.value->>'source', {cos} {base}
                               ORDER BY {cos}, w.key, lower(ci.name), ci.sys_id LIMIT %s""").format(
            cos=_COSMETIC, base=base), (*params, LIST_LIMIT))
        samples = [{"sys_id": s, "name": n or "(adsız)", "class_label": pres.class_label(c), "attribute": a,
                    "itsm_source": src, "itsm_value": iv, "golden_value": gv, "golden_source": gs, "cosmetic": cos}
                   for s, n, c, a, src, iv, gv, gs, cos in cur.fetchall()]
    labels = {}
    for cls in classes:
        for name, f in ctx.state.metadata.fields_of(cls).items():
            labels.setdefault(name, f.get("label_tr", name))
    for row in by_attr:
        row["label"] = labels.get(row["attribute"], row["attribute"])
    refs = pres.resolve_refs(ctx.conn, [v for r in samples for v in (r["itsm_value"], r["golden_value"])
                                        if isinstance(v, str)])
    for row in samples:
        row["label"] = labels.get(row["attribute"], row["attribute"])
        row["itsm_display"] = pres.display(row["attribute"], row["itsm_value"], refs)
        row["golden_display"] = pres.display(row["attribute"], row["golden_value"], refs)
    return {"by_attribute": by_attr, "samples": samples, "total": sum(r["count"] for r in by_attr),
            "real": sum(r["real"] for r in by_attr), "cosmetic": sum(r["cosmetic"] for r in by_attr)}


def _shadow(ctx: Ctx, classes: list[str]) -> list[dict[str, Any]]:
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT sh.value->>0, count(*),
                              count(*) FILTER (WHERE lower(btrim(sh.value->>1)) =
                                               lower(btrim(COALESCE(to_jsonb(ci)->>w.key, ci.class_attributes->>w.key))))
                         FROM cmdb.ci_reconciliation_state s
                         JOIN cmdb.cmdb_ci ci ON ci.sys_id = s.ci_sys_id
                         CROSS JOIN LATERAL jsonb_each(s.winners) w
                         CROSS JOIN LATERAL jsonb_array_elements(w.value->'shadow') sh
                        WHERE ci.sys_class_name = ANY(%s)
                        GROUP BY 1 ORDER BY 1""", (classes,))
        return [{"source": src, "compared": n, "agree": a, "agree_pct": round(100 * a / n, 1) if n else None}
                for src, n, a in cur.fetchall()]


def build_report(ctx: Ctx) -> dict[str, Any]:
    md = ctx.state.metadata
    classes = _allowed_classes(ctx)
    states = _breaker_states(ctx)
    reg = md.sources
    deps = sorted({s.name for role in (*ITSM, *DISCOVERY) for s in reg.sources_in_role(role)
                   if s.lifecycle in ("active", "retiring")})
    unhealthy = [d for d in deps if states.get(d) != "ok"]
    collection = "partial" if unhealthy else "complete"
    return {
        "report": "shadow_report", "version": 1, "title": "Gölge mod raporu: mevcut CMDB ile Evren karşılaştırması",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "inconclusive" if unhealthy else "conclusive",
        "provenance": {"source_system": "Evren", "query_reference": "evren.report.shadow/v1",
                       "collection_status": collection, "depends_on": deps, "unhealthy_sources": unhealthy,
                       "itsm_roles": list(ITSM), "discovery_roles": list(DISCOVERY)},
        "coverage": _coverage(ctx, classes),
        "duplicates": _duplicates(ctx, classes),
        "drift": _drift(ctx, classes),
        "shadow_sources": _shadow(ctx, classes),
    }


def canonical(doc: dict[str, Any]) -> bytes:
    """Kanıt için kararlı JSON (sıralı anahtarlar, sabit ayraçlar): aynı içerik → aynı SHA-256."""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


@router.get("/shadow")
def shadow_report(ctx: Ctx = Depends(context)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    return {**build_report(ctx), "can_snapshot": ctx.grant(SNAPSHOT_KIND, "create").allowed}


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")


@router.post("/shadow/snapshots", dependencies=[Depends(require_csrf)], status_code=201)
def create_snapshot(body: SnapshotRequest, ctx: Ctx = Depends(context)) -> JSONResponse:
    ctx.require("cmdb_ci", "read")
    ctx.require(SNAPSHOT_KIND, "create")
    p = ctx.principal
    digest = request_hash("shadow-snapshot", body.note)
    report = build_report(ctx)
    content = {**report, "captured_by": p.audit_id, "note": body.note}
    raw = canonical(content)
    sha = hashlib.sha256(raw).hexdigest()
    with ctx.conn.transaction(), ctx.conn.cursor() as cur:
        if (prior := replay(cur, p.audit_id, body.idempotency_key, digest)) is not None:
            return prior
        cur.execute("""INSERT INTO insight.evidence_snapshot (kind, generated_by, collection_status, content,
                                                             content_sha256, note)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING sys_id, generated_at""",
                    (SNAPSHOT_KIND, p.audit_id, report["provenance"]["collection_status"], Jsonb(json.loads(raw)), sha,
                     body.note))
        sid, at = cur.fetchone()  # type: ignore[misc]
        cur.execute("INSERT INTO audit.security_event (principal, action, resource, decision, detail) "
                    "VALUES (%s, 'evidence_snapshot', %s, 'allow', %s)",
                    (p.audit_id, f"evidence_snapshot:{sid}", Jsonb({"sha256": sha, "status": report["status"]})))
        response = {"id": sid, "generated_at": at.isoformat(), "sha256": sha, "status": report["status"],
                    "collection_status": report["provenance"]["collection_status"], "replayed": False,
                    "message": "Rapor değiştirilemez kanıt olarak kaydedildi."}
        remember(cur, p.audit_id, body.idempotency_key, "/api/v1/reports/shadow/snapshots", digest, 201, response)
    return JSONResponse(response, status_code=201)


@router.get("/shadow/snapshots")
def list_snapshots(ctx: Ctx = Depends(context), limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    ctx.require("cmdb_ci", "read")
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT sys_id, generated_at, generated_by, collection_status, content_sha256, note,
                              content->>'status'
                         FROM insight.evidence_snapshot WHERE kind = %s ORDER BY generated_at DESC LIMIT %s""",
                    (SNAPSHOT_KIND, limit))
        items = [{"id": i, "generated_at": at.isoformat(), "generated_by": by, "collection_status": cs, "sha256": sha,
                  "note": note, "status": st} for i, at, by, cs, sha, note, st in cur.fetchall()]
    return {"items": items}


@router.get("/shadow/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, ctx: Ctx = Depends(context)) -> Response:
    """Kanıtı kayıttaki kararlı JSON biçiminde indirir; ``X-Evren-Content-SHA256`` ile doğrulanabilir."""
    ctx.require("cmdb_ci", "read")
    if len(snapshot_id) != 32 or not all(c in "0123456789abcdef" for c in snapshot_id):
        raise ApiError(400, "gecersiz_kimlik", "Kanıt kimliği 32 haneli küçük harf onaltılık olmalı.")
    row = ctx.conn.execute("""SELECT content, content_sha256, generated_at FROM insight.evidence_snapshot
                               WHERE sys_id = %s AND kind = %s""", (snapshot_id, SNAPSHOT_KIND)).fetchone()
    if row is None:
        raise ApiError(404, "bulunamadi", "Kanıt bulunamadı.")
    content, sha, at = row
    raw = canonical(content)
    if hashlib.sha256(raw).hexdigest() != sha:  # pragma: no cover - salt ekleme tetikleyicisiyle korunur
        raise ApiError(409, "kanit_bozuk", "Kanıt içeriği kayıtlı özetle uyuşmuyor.")
    name = f"evren-golge-mod-{at:%Y%m%d-%H%M}-{snapshot_id[:8]}.json"
    return Response(raw, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "X-Evren-Content-SHA256": sha})
