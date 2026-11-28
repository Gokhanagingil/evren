"""Kural değerlendirme ve CMDB sağlık ölçümü (U8.6, ADR-0037; F1-09, F1-18, F1-26).

Görev çalışanı her turda:

1. Süresi dolan istisnaları yeniden açar.
2. Zamanı gelen etkin kuralları ve "şimdi çalıştır" isteklerini değerlendirir: kapsamda kuralın koşuluna uyan her CI
   için bulgu açar (varsa günceller), artık uymayan bulguyu kapatır (açık görevi tetikleyiciyle kapanır), açık
   bulgunun sorumlusuna tek görev açar. Kural bir kaynak rolüne bağlıysa ve o rolün kaynağı son çalışmada sağlıklı
   değilse sonuç verilmez, bulgular değişmez (devre kesici, kural 4).
3. Saatte bir (veya bir kural çalıştıysa) CMDB sağlığını ölçer: tamlık, doğruluk, uyum ve kapsam puanları
   (``evren_core.health``), ``insight.health_snapshot``'a yazar.

CMDB'ye yazmaz, görev kararı veremez, istisna veremez (veritabanı yetkileriyle).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from evren_core.governance import NOT_RETIRED_SQL
from evren_core.health import DIMENSIONS, Ratio, completeness_ratio, dimension_score, grade, overall_score
from evren_core.metadata import Metadata, metadata_field_resolver
from evren_core.quality import DIMENSION_TR, compile_rule, describe_details, relation_violations

from .tasks import NoChannel, open_ci_tasks

__all__ = ["RULE_ACTOR", "RuleEvaluator", "measure_health"]

log = logging.getLogger("evren.governance")
RULE_ACTOR = "evren:kural"
HEALTH_EVERY = timedelta(hours=1)
RELATION_CANDIDATES = 50000
#: Kapsam bayrağı → rol(ler); sağlık panosunun kapsam boyutu (ürün değil rol, ADR-0009)
COVERAGE_ROLES: dict[str, tuple[str, ...]] = {
    "discovery": ("Discovery", "Virtualization", "Network"), "monitoring": ("Monitoring",),
    "security": ("EndpointSecurity",), "itsm": ("ITSM",),
}
COVERAGE_TR = {"discovery": "Keşifte görülen", "monitoring": "İzlenen", "security": "Uç nokta korumalı",
               "itsm": "Mevcut CMDB'de kayıtlı"}


def _role_health(conn: psycopg.Connection[Any], md: Metadata, roles: tuple[str, ...]) -> str | None:
    """Rol(ler)in etkin kaynakları sağlıklıysa ``None``; değilse Türkçe neden."""
    for role in roles:
        sources = [s.name for s in md.sources.sources_in_role(role)]
        if not sources:
            return f"{md.sources.roles[role].label_tr} rolünde etkin veri kaynağı yok."
        rows = dict(conn.execute(
            """SELECT DISTINCT ON (data_source) data_source, CASE WHEN completed THEN coalesce(breaker_state, 'warn')
                                                              ELSE 'open' END
                 FROM ingest.source_run WHERE data_source = ANY(%s) ORDER BY data_source, started_at DESC""",
            (sources,)).fetchall())
        bad = [s for s in sources if rows.get(s) != "ok"]
        if bad:
            return f"{', '.join(bad)} son çalışmada sağlıklı değil veya hiç çalışmadı; bulgular değişmedi."
    return None


class RuleEvaluator:
    def __init__(self, md: Metadata, event: Callable[[psycopg.Connection[Any], str, str, dict[str, Any]], None]) -> None:
        self.md = md
        self._event = event

    def cycle(self, conn: psycopg.Connection[Any], totals: dict[str, int]) -> int:
        """Süresi dolan istisnalar, zamanı gelen kurallar ve istekler. Döner: değerlendirilen kural sayısı."""
        reopened = conn.execute("""UPDATE insight.finding SET state = 'open', triage = false
                                    WHERE state = 'excepted' AND exception_until <= now()""").rowcount
        totals["exceptions_expired"] += reopened
        evaluated = 0
        due = conn.execute("""SELECT sys_id FROM insight.quality_rule WHERE state = 'active' AND next_run_at <= now()
                               ORDER BY next_run_at LIMIT 10""").fetchall()
        for (rid,) in due:
            with conn.transaction():
                row = conn.execute("""SELECT interval_hours FROM insight.quality_rule
                                       WHERE sys_id = %s AND state = 'active' AND next_run_at <= now()
                                       FOR UPDATE SKIP LOCKED""", (rid,)).fetchone()
                if row is None:
                    continue
                conn.execute("""UPDATE insight.quality_rule SET next_run_at = now() + make_interval(hours => %s),
                                       last_run_at = now() WHERE sys_id = %s""", (row[0], rid))
                run = conn.execute("INSERT INTO insight.quality_rule_run (rule, trigger) VALUES (%s, 'schedule') "
                                   "RETURNING id", (rid,)).fetchone()[0]
            self.evaluate(conn, rid, run, totals)
            evaluated += 1
        requested = conn.execute("""SELECT id, rule FROM insight.quality_rule_run WHERE status = 'requested'
                                     ORDER BY id LIMIT 10""").fetchall()
        for run, rid in requested:
            claimed = conn.execute("UPDATE insight.quality_rule_run SET status = 'running', started_at = now() "
                                   "WHERE id = %s AND status = 'requested' RETURNING id", (run,)).fetchone()
            if claimed:
                conn.execute("UPDATE insight.quality_rule SET last_run_at = now() WHERE sys_id = %s", (rid,))
                self.evaluate(conn, rid, run, totals)
                evaluated += 1
        return evaluated

    def evaluate(self, conn: psycopg.Connection[Any], rid: str, run: int, totals: dict[str, int]) -> None:
        r = conn.execute("""SELECT name, check_kind, params, scope_class, scope_query, open_task, task_title,
                                   task_instructions, due_days, channel, max_findings
                              FROM insight.quality_rule WHERE sys_id = %s""", (rid,)).fetchone()
        name, kind, params, scope_class, scope_query, open_task, title, instructions, due_days, channel, limit = r

        def finish(status: str, note: str | None, **n: int) -> None:
            conn.execute("""UPDATE insight.quality_rule_run SET status = %s, note = %s, finished_at = now(),
                                   in_scope = %s, matched = %s, opened = %s, resolved = %s, tasks_opened = %s
                             WHERE id = %s""",
                         (status, note, n.get("in_scope"), n.get("matched"), n.get("opened"), n.get("resolved"),
                          n.get("tasks"), run))

        try:
            plan = compile_rule(self.md, scope_class, scope_query, kind, params)
        except ValueError as exc:
            finish("failed", str(exc)[:300])
            return
        if plan.depends_on_roles and (why := _role_health(conn, self.md, plan.depends_on_roles)):
            finish("inconclusive", why[:300])
            totals["rules_inconclusive"] += 1
            return
        in_scope = conn.execute(f"SELECT count(*) FROM cmdb.cmdb_ci c WHERE {plan.scope_sql}",  # noqa: S608 - sözlükten derlendi
                                plan.scope_params).fetchone()[0]
        where, wparams = plan.where
        detail = plan.detail_sql or "NULL::text[]"
        cap = RELATION_CANDIDATES if plan.relation_check else limit
        rows = conn.execute(f"SELECT c.sys_id, {detail} FROM cmdb.cmdb_ci c WHERE {where} LIMIT %s",  # noqa: S608 - sözlükten derlendi
                            [*plan.detail_params, *wparams, cap + 1]).fetchall()
        if len(rows) > cap:
            finish("failed", f"Bulgu sayısı sınırı ({cap}) aşıldı; kapsamı daraltın veya sınırı artırın. Bulgular "
                             "değişmedi.", in_scope=in_scope)
            return
        base = {"summary": plan.summary}
        matched: dict[str, dict[str, Any]] = {}
        if plan.relation_check:
            rels: dict[str, list[tuple[str, str, str, str]]] = {}
            for parent, pclass, rtype, cclass, cname in conn.execute(
                    """SELECT r.parent, p.sys_class_name, t.name, ch.sys_class_name, ch.name
                         FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_ci p ON p.sys_id = r.parent
                         JOIN cmdb.cmdb_ci ch ON ch.sys_id = r.child JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                        WHERE r.parent = ANY(%s)""", ([row[0] for row in rows],)).fetchall():
                rels.setdefault(parent, []).append((pclass, rtype, cclass, cname))
            for ci, found in rels.items():
                if bad := relation_violations(self.md, found):
                    matched[ci] = {**base, "relations": bad[:10], "count": len(bad)}
            if len(matched) > limit:
                finish("failed", f"Bulgu sayısı sınırı ({limit}) aşıldı. Bulgular değişmedi.", in_scope=in_scope)
                return
        else:
            for ci, extra in rows:
                if isinstance(extra, dict):  # kapsam boşluğu: eksik roller ve rol başına son görülme
                    matched[ci] = {**base, **extra}
                else:
                    matched[ci] = {**base, **({"empty": list(extra)} if extra is not None else {})}
        labels = {k: str(v.get("label_tr") or k) for k, v in self.md.fields_of(scope_class).items()}
        role_labels = {k: v.label_tr for k, v in self.md.sources.roles.items()}
        for d in matched.values():
            d["text"] = describe_details(kind, d, labels, role_labels=role_labels)

        current = {ci: (fid, state, triage) for fid, ci, state, triage in conn.execute(
            "SELECT sys_id, ci_sys_id, state, triage FROM insight.finding WHERE rule = %s AND state <> 'resolved'",
            (rid,)).fetchall()}
        opened = resolved = 0
        with conn.transaction():
            for ci, d in matched.items():
                if ci in current:
                    conn.execute("UPDATE insight.finding SET last_seen_at = now(), details = %s WHERE sys_id = %s",
                                 (Jsonb(d), current[ci][0]))
                else:
                    conn.execute("INSERT INTO insight.finding (rule, ci_sys_id, details) VALUES (%s, %s, %s)",
                                 (rid, ci, Jsonb(d)))
                    opened += 1
            for ci, (fid, _state, _t) in current.items():
                if ci not in matched:
                    conn.execute("UPDATE insight.finding SET state = 'resolved', resolved_at = now() WHERE sys_id = %s",
                                 (fid,))
                    resolved += 1
        tasks = self.open_tasks(conn, rid, name, title, instructions, due_days, channel) if open_task else 0
        finish("done", None, in_scope=in_scope, matched=len(matched), opened=opened, resolved=resolved, tasks=tasks)
        totals["findings_opened"] += opened
        totals["findings_resolved"] += resolved
        totals["rule_tasks"] += tasks
        log.info("kural %s: kapsam=%d bulgu=%d yeni=%d kapanan=%d görev=%d", name, in_scope, len(matched), opened,
                 resolved, tasks)

    def open_tasks(self, conn: psycopg.Connection[Any], rid: str, name: str, title: str, instructions: str,
                   due_days: int, channel: str | None) -> int:
        """Açık (ayıklamada olmayan) ve açık görevi olmayan bulgulara görev. Sorumlu görevi reddettiyse, CI'ın
        sorumlusu değişene kadar yeni görev açılmaz (bulgu Konfigürasyon yöneticisinin listesinde kalır)."""
        rows = conn.execute(
            """SELECT f.sys_id, f.ci_sys_id, c.name, c.owned_by, c.managed_by, c.support_group, f.triage, f.details,
                      (SELECT array[t.assignee_user::text, t.assignee_group::text] FROM gov.task t
                        WHERE t.source = 'rule' AND t.source_ref = f.sys_id AND t.outcome = 'rejected'
                        ORDER BY t.closed_at DESC LIMIT 1)
                 FROM insight.finding f JOIN cmdb.cmdb_ci c ON c.sys_id = f.ci_sys_id
                WHERE f.rule = %s AND f.state = 'open'
                  AND NOT EXISTS (SELECT 1 FROM gov.task t WHERE t.source = 'rule' AND t.source_ref = f.sys_id
                                     AND t.state = 'open')""", (rid,)).fetchall()
        items = []
        for fid, ci, ci_name, owned, managed, group, triage, details, rejected in rows:
            text = (details or {}).get("text") or ""
            items.append({"ci": ci, "owned_by": owned, "managed_by": managed, "support_group": group,
                          "source_ref": fid, "dedup_key": f"rule:{rid}:{ci}", "title": f"{title} — {ci_name}",
                          "description": "\n\n".join(p for p in (f"Bulgu: {text}" if text else "", instructions) if p),
                          "triage": triage,
                          "skip_if": (lambda who, rej=rejected, t=triage: bool(t) and rej is not None
                                      and [who.user, who.group] == list(rej))})
        try:
            res = open_ci_tasks(conn, items, kind="finding", source="rule", due_days=due_days, channel=channel,
                                actor=RULE_ACTOR, event=self._event, event_detail={"rule": name})
        except NoChannel:
            log.warning("kural %s: etkin görev kanalı yok; görev açılmadı", name)
            return 0
        for item, _task, _who in res.created:
            if item["triage"]:  # sorumlu değişti: ayıklama biter
                conn.execute("UPDATE insight.finding SET triage = false WHERE sys_id = %s", (item["source_ref"],))
        return len(res.created)


# ---------------------------------------------------------------------------------------------- sağlık ölçümü
def _empty(expr: str) -> str:
    return f"({expr} IS NULL OR {expr}::text = '')"


def measure_health(conn: psycopg.Connection[Any], md: Metadata) -> dict[str, Any]:
    """Dört boyutun ölçümleri ve puanları (``health_rules.yaml`` + etkin kurallar). Yazmaz; döner."""
    rules = md.health or {}
    not_retired = NOT_RETIRED_SQL
    metrics: dict[str, list[dict[str, Any]]] = {d: [] for d in DIMENSIONS}

    def add(dim: str, mid: str, label: str, ratio: Ratio, **extra: Any) -> None:
        metrics[dim].append({"id": mid, "label": label, "good": ratio.good, "total": ratio.total, "pct": ratio.pct,
                             **extra})

    total = conn.execute(f"SELECT count(*) FROM cmdb.cmdb_ci c WHERE {not_retired}").fetchone()[0]  # noqa: S608 - sabit
    # Tamlık: sınıf başına zorunlu (tam) ve önerilen (yarım ağırlık) alanlar
    for entry in rules.get("completeness", []):
        cls = entry["applies_to"]
        if cls not in md.hierarchy:
            continue
        resolve = metadata_field_resolver(md, cls, "c")
        req, rec = list(entry.get("required", [])), list(entry.get("recommended", []))
        exprs = [resolve(f).sql for f in req + rec]
        cols = ", ".join(f"count(*) FILTER (WHERE NOT {_empty(e)})" for e in exprs)
        row = conn.execute(f"SELECT count(*), {cols} FROM cmdb.cmdb_ci c "  # noqa: S608 - alanlar sözlükten
                           f"WHERE c.sys_class_name = ANY(%s) AND {not_retired}",
                           (sorted(md.hierarchy.descendants(cls) | {cls}),)).fetchone()
        n, counts = row[0], row[1:]
        ratio = completeness_ratio(n, dict(zip(req, counts[:len(req)], strict=True)),
                                   dict(zip(rec, counts[len(req):], strict=True)))
        label = next((c.get("label_tr") or cls for c in md.classes if c["name"] == cls), cls)
        add("completeness", f"fields:{cls}", f"{label}: zorunlu ve önerilen alanlar", ratio, ci=n)
    # Doğruluk: eskimiş / emekliye ayırma adayı CI (açık eskime önerisi)
    stale = conn.execute("SELECT count(DISTINCT ci_sys_id) FROM insight.staleness_case WHERE status = 'open'").fetchone()[0]
    add("correctness", "stale", "Eskimemiş CI (kaynakların görmeye devam ettiği)", Ratio.from_bad(stale, total))
    # Uyum: üretimdeki sunucuda yönetişim alanları
    comp = rules.get("compliance") or {}
    if comp.get("governance_fields"):
        resolve = metadata_field_resolver(md, "cmdb_ci_server", "c")
        filled = " AND ".join(f"NOT {_empty(resolve(f).sql)}" for f in comp["governance_fields"])
        n, good = conn.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {filled}) FROM cmdb.cmdb_ci c "  # noqa: S608 - alanlar sözlükten
            f"WHERE c.sys_class_name = ANY(%s) AND c.used_for = %s AND {not_retired}",
            (sorted(md.hierarchy.descendants("cmdb_ci_server") | {"cmdb_ci_server"}),
             comp.get("production_value", "Production"))).fetchone()
        add("compliance", "governance_fields", "Üretim sunucusunda sahip ve destek grubu dolu", Ratio(good, n))
    # Kapsam: sunucuların rol bazında görülme oranı (rolünde etkin kaynak olmayan ölçülmez)
    cov_cls = (rules.get("coverage") or {}).get("applies_to", "cmdb_ci_server")
    servers = sorted(md.hierarchy.descendants(cov_cls) | {cov_cls})
    n_srv = conn.execute(f"SELECT count(*) FROM cmdb.cmdb_ci c WHERE c.sys_class_name = ANY(%s) AND {not_retired}",  # noqa: S608 - sabit
                         (servers,)).fetchone()[0]
    for flag, roles in COVERAGE_ROLES.items():
        if not any(md.sources.sources_in_role(r) for r in roles):
            continue
        seen = conn.execute(
            f"""SELECT count(*) FROM cmdb.cmdb_ci c WHERE c.sys_class_name = ANY(%s) AND {not_retired}
                  AND EXISTS (SELECT 1 FROM ingest.identity_link l JOIN ingest.data_source d ON d.name = l.data_source
                               WHERE l.ci_sys_id = c.sys_id AND l.active AND d.role = ANY(%s))""",  # noqa: S608 - sabit
            (servers, list(roles))).fetchone()[0]
        add("coverage", f"coverage:{flag}", f"{COVERAGE_TR[flag]} sunucu", Ratio(seen, n_srv))
    # Etkin kurallar: kapsamdaki CI'lardan açık bulgusu olmayanların oranı (istisna kabul edilmiş risktir)
    rule_rows = conn.execute(
        """SELECT q.sys_id, q.name, q.dimension, q.severity,
                  (SELECT r.in_scope FROM insight.quality_rule_run r WHERE r.rule = q.sys_id AND r.status = 'done'
                    ORDER BY r.id DESC LIMIT 1),
                  (SELECT count(*) FROM insight.finding f WHERE f.rule = q.sys_id AND f.state = 'open'),
                  (SELECT count(*) FROM insight.finding f WHERE f.rule = q.sys_id AND f.state = 'excepted')
             FROM insight.quality_rule q WHERE q.state = 'active' ORDER BY q.name""").fetchall()
    for rid, name, dim, severity, in_scope, open_, excepted in rule_rows:
        if in_scope is None:
            continue
        add(dim, f"rule:{rid}", name, Ratio.from_bad(open_, in_scope), rule=rid, severity=severity, open=open_,
            excepted=excepted)
    weights = rules.get("weights")
    thresholds = rules.get("thresholds")
    scores = {d: dimension_score([Ratio(m["good"], m["total"]) for m in metrics[d]]) for d in DIMENSIONS}
    overall = overall_score(scores, weights)
    return {"overall": overall, "grade": grade(overall, thresholds), "scores": scores, "total_ci": total,
            "dimensions": {d: {"label": DIMENSION_TR[d], "score": scores[d], "grade": grade(scores[d], thresholds),
                               "metrics": metrics[d]} for d in DIMENSIONS},
            "weights": weights or {}, "thresholds": thresholds or {}}


def snapshot_health(conn: psycopg.Connection[Any], md: Metadata, *, force: bool = False) -> bool:
    """Son ölçüm bir saatten eskiyse (veya ``force``) ölçer ve yazar."""
    if not force:
        last = conn.execute("SELECT max(taken_at) FROM insight.health_snapshot").fetchone()[0]
        if last is not None and datetime.now(UTC) - last < HEALTH_EVERY:
            return False
    h = measure_health(conn, md)
    s = h["scores"]
    conn.execute("""INSERT INTO insight.health_snapshot (overall, completeness, correctness, compliance, coverage, details)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                 (h["overall"], s["completeness"], s["correctness"], s["compliance"], s["coverage"], Jsonb(h)))
    return True
