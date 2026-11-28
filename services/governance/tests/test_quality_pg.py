"""Kural değerlendirme (U8.6, ADR-0037): ilişkisiz veritabanı → bulgu → sorumluya tek görev; düzeltilince bulgu ve
görev kendiliğinden kapanır; "geçerli değil" süreli istisna; "reddedildi" sorumlu değişene kadar yeni görev açmaz;
boş alan ayrıntısı; kaynak rolü sağlıksızken sonuç yok; izinli olmayan ilişki; sınır; sağlık ölçümü; en az yetki.
Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from evren_core.governance import ServiceNowTaskSimulator
from evren_governance import GovernanceConfig, GovernanceWorker
from evren_governance.quality import measure_health, snapshot_health


def owner() -> psycopg.Connection:
    return psycopg.connect(autocommit=True)


def person() -> str:
    sid = uuid.uuid4().hex
    with owner() as c:
        c.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'evren')",
                  (sid, f"kural.{sid[:8]}", f"Kural Kişi {sid[:6]}"))
    return sid


def ci(name: str, cls: str, *, owned_by: str | None = None, used_for: str | None = None) -> str:
    with owner() as c:
        return c.execute("""INSERT INTO cmdb.cmdb_ci (sys_class_name, name, owned_by, used_for) VALUES (%s, %s, %s, %s)
                            RETURNING sys_id""", (cls, name, owned_by, used_for)).fetchone()[0]


def relate(parent: str, child: str, rel_type: str = "Runs on::Runs") -> None:
    with owner() as c:
        c.execute("""INSERT INTO cmdb.cmdb_rel_ci (parent, child, type)
                     SELECT %s, %s, sys_id FROM cmdb.cmdb_rel_type WHERE name = %s""", (parent, child, rel_type))


def rule(tag: str, kind: str, params: dict, *, scope_class: str, query: str | None = None, max_findings: int = 100,
         open_task: bool = True, dimension: str = "completeness") -> str:
    with owner() as c:
        return c.execute(
            """INSERT INTO insight.quality_rule (name, description, check_kind, params, scope_class, scope_query, dimension,
                                                 severity, open_task, task_title, task_instructions, due_days, channel,
                                                 exception_days, max_findings, state, next_run_at, created_by)
               VALUES (%s, 'Test kuralı', %s, %s, %s, %s, %s, 'medium', %s, 'İlişkileri tamamlayın',
                       'Kullanan uygulamayı ekleyin.', 14, 'evren', 30, %s, 'active', now() - interval '1 minute',
                       'test:kurulum') RETURNING sys_id""",
            (f"Kural {tag} {kind}", kind, Jsonb(params), scope_class,
             query if query is not None else f"nameSTARTSWITH{tag}", dimension, open_task, max_findings)).fetchone()[0]


def request_run(rid: str) -> None:
    with owner() as c:
        c.execute("INSERT INTO insight.quality_rule_run (rule, trigger, requested_by, status) "
                  "VALUES (%s, 'manual', 'test', 'requested')", (rid,))


def findings(rid: str) -> list[dict]:
    with owner() as c:
        cur = c.execute("""SELECT f.*, c.name AS ci_name FROM insight.finding f JOIN cmdb.cmdb_ci c ON c.sys_id = f.ci_sys_id
                            WHERE f.rule = %s ORDER BY c.name, f.first_seen_at""", (rid,))
        names = [d.name for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def tasks(finding: str) -> list[dict]:
    with owner() as c:
        cur = c.execute("SELECT * FROM gov.task WHERE source = 'rule' AND source_ref = %s ORDER BY created_at", (finding,))
        names = [d.name for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def last_run(rid: str) -> dict:
    with owner() as c:
        cur = c.execute("SELECT * FROM insight.quality_rule_run WHERE rule = %s ORDER BY id DESC LIMIT 1", (rid,))
        return dict(zip([d.name for d in cur.description], cur.fetchone(), strict=True))


def decide(task_id: str, outcome: str, note: str = "Bilinçli olarak bağımsız çalışıyor") -> None:
    """API'nin yaptığı gibi (karar API'dedir; burada sahibiyle kurulum)."""
    with owner() as c:
        c.execute("""UPDATE gov.task SET state = 'done', outcome = %s, outcome_note = %s, closed_by = 'dev:sorumlu',
                            closed_at = now() WHERE sys_id = %s""", (outcome, note, task_id))


@pytest.fixture
def worker():
    cfg = GovernanceConfig(conninfo="", public_url="https://evren.ornek.test", worker_id=f"test-kural-{uuid.uuid4().hex[:6]}",
                           sync_minutes=1)
    return GovernanceWorker(cfg, transport=ServiceNowTaskSimulator(), connect=lambda: psycopg.connect())


def cycle(worker: GovernanceWorker) -> dict:
    with worker.open() as conn:
        return worker.cycle(conn)


ORPHAN_DB = {"related_classes": ["cmdb_ci_appl", "cmdb_ci_computer", "cmdb_ci_business_app", "cmdb_ci_service"],
             "exclude_classes": ["cmdb_ci_db_instance"]}


def test_orphan_database_opens_one_task_and_closes_when_fixed(worker):
    tag = f"kdb{uuid.uuid4().hex[:6]}"
    sahip = person()
    used = ci(f"{tag}-a-kullanilan", "cmdb_ci_db_mssql_instance", owned_by=sahip)
    relate(ci(f"{tag}-uygulama", "cmdb_ci_appl"), used, "Depends on::Used by")
    lonely = ci(f"{tag}-b-yalniz", "cmdb_ci_db_ora_instance", owned_by=sahip)
    orphan = ci(f"{tag}-c-sahipsiz", "cmdb_ci_db_instance")
    other_db = ci(f"{tag}-d-replika", "cmdb_ci_db_instance", owned_by=sahip)
    relate(other_db, orphan, "Depends on::Used by")  # veritabanı ↔ veritabanı ilişkisi sayılmaz
    rid = rule(tag, "no_relation", ORPHAN_DB, scope_class="cmdb_ci_db_instance")
    totals = cycle(worker)
    run = last_run(rid)
    assert run["status"] == "done" and run["in_scope"] == 4 and run["matched"] == 3 and run["tasks_opened"] == 3
    assert totals["health_measured"] == 1
    fs = {f["ci_sys_id"]: f for f in findings(rid)}
    assert set(fs) == {lonely, orphan, other_db} and all(f["state"] == "open" for f in fs.values())
    assert "ilişkisi yok" in fs[lonely]["details"]["text"]
    t = tasks(fs[lonely]["sys_id"])
    assert len(t) == 1 and t[0]["assignee_user"] == sahip and t[0]["kind"] == "finding"
    assert t[0]["title"].endswith(f"{tag}-b-yalniz") and "Bulgu:" in t[0]["description"]
    assert tasks(fs[orphan]["sys_id"])[0]["assignee_basis"] == "unassigned"
    # İkinci çalışma: aynı bulgu ve görev, yenisi yok
    request_run(rid)
    cycle(worker)
    assert last_run(rid)["opened"] == 0 and last_run(rid)["tasks_opened"] == 0 and len(findings(rid)) == 3
    # Sorumlu ilişkiyi kurar → bulgu kapanır, görev "Bulgu giderildi" ile kapanır
    relate(lonely, ci(f"{tag}-sunucu", "cmdb_ci_linux_server"))
    request_run(rid)
    cycle(worker)
    assert last_run(rid)["resolved"] == 1
    f = next(x for x in findings(rid) if x["ci_sys_id"] == lonely)
    assert f["state"] == "resolved" and f["resolved_at"] is not None
    closed = tasks(f["sys_id"])[0]
    assert closed["state"] == "done" and closed["outcome"] == "resolved" and closed["closed_by"] == "evren:kural"


def test_not_applicable_is_a_timed_exception_and_rejection_waits_for_new_owner(worker):
    tag = f"kis{uuid.uuid4().hex[:6]}"
    ilk, sonraki = person(), person()
    a = ci(f"{tag}-a", "cmdb_ci_db_instance", owned_by=ilk)
    b = ci(f"{tag}-b", "cmdb_ci_db_instance", owned_by=ilk)
    rid = rule(tag, "no_relation", ORPHAN_DB, scope_class="cmdb_ci_db_instance")
    cycle(worker)
    fa, fb = (next(f for f in findings(rid) if f["ci_sys_id"] == x) for x in (a, b))
    decide(tasks(fa["sys_id"])[0]["sys_id"], "not_applicable")
    decide(tasks(fb["sys_id"])[0]["sys_id"], "rejected", "Bu veritabanının sorumlusu ben değilim")
    fa2, fb2 = (next(f for f in findings(rid) if f["ci_sys_id"] == x) for x in (a, b))
    assert fa2["state"] == "excepted" and fa2["exception_by"] == "dev:sorumlu" and fa2["exception_until"] is not None
    assert fb2["state"] == "open" and fb2["triage"] is True
    request_run(rid)
    cycle(worker)
    assert len(tasks(fa["sys_id"])) == 1 and len(tasks(fb["sys_id"])) == 1  # istisna ve ayıklama: yeni görev yok
    # Sorumlu değişince ayıklanan bulgu yeni sorumluya gider
    with owner() as c:
        c.execute("UPDATE cmdb.cmdb_ci SET owned_by = %s WHERE sys_id = %s", (sonraki, b))
        c.execute("UPDATE insight.finding SET exception_until = now() - interval '1 minute' WHERE sys_id = %s",
                  (fa["sys_id"],))
    request_run(rid)
    cycle(worker)
    tb = tasks(fb["sys_id"])
    assert len(tb) == 2 and tb[1]["assignee_user"] == sonraki
    assert next(f for f in findings(rid) if f["ci_sys_id"] == b)["triage"] is False
    # Süresi dolan istisna yeniden açılır ve yeni görev alır
    assert next(f for f in findings(rid) if f["ci_sys_id"] == a)["state"] == "open"
    assert len(tasks(fa["sys_id"])) == 2


def test_missing_fields_details_and_no_task_rule(worker):
    tag = f"kmf{uuid.uuid4().hex[:6]}"
    ci(f"{tag}-dolu", "cmdb_ci_linux_server", owned_by=person(), used_for="Production")
    bos = ci(f"{tag}-bos", "cmdb_ci_linux_server", used_for="Production")
    ci(f"{tag}-test", "cmdb_ci_linux_server", used_for="Test")
    rid = rule(tag, "missing_fields", {"fields": ["owned_by"]}, scope_class="cmdb_ci",
               query=f"nameSTARTSWITH{tag}^used_for=Production", open_task=False, dimension="compliance")
    cycle(worker)
    (f,) = findings(rid)
    assert f["ci_sys_id"] == bos and f["details"]["empty"] == ["owned_by"] and f["details"]["text"] == "Boş alan: Sahibi"
    assert tasks(f["sys_id"]) == [] and last_run(rid)["in_scope"] == 2


def test_role_rule_is_inconclusive_when_role_sources_are_not_healthy(worker):
    tag = f"krl{uuid.uuid4().hex[:6]}"
    ci(f"{tag}-srv", "cmdb_ci_linux_server", used_for="Production")
    with owner() as c:  # izleme kaynaklarının son çalışması tamamlanmamış
        for (src,) in c.execute("SELECT name FROM ingest.data_source WHERE role = 'Monitoring'").fetchall():
            c.execute("INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, completed) "
                      "VALUES (gen_random_uuid(), %s, 'snapshot', now(), false)",
                      (src,))
    rid = rule(tag, "not_seen_by_role", {"role": "Monitoring"}, scope_class="cmdb_ci_server", dimension="coverage")
    cycle(worker)
    run = last_run(rid)
    assert run["status"] == "inconclusive" and "sağlıklı değil" in run["note"] and findings(rid) == []


def test_relation_not_allowed_reports_offending_relations(worker):
    tag = f"kri{uuid.uuid4().hex[:6]}"
    srv = ci(f"{tag}-srv", "cmdb_ci_linux_server")
    app = ci(f"{tag}-app", "cmdb_ci_appl")
    relate(srv, app)  # sunucu "Runs on" uygulama: yön ters
    relate(app, ci(f"{tag}-srv2", "cmdb_ci_linux_server"))  # izinli
    rid = rule(tag, "relation_not_allowed", {}, scope_class="cmdb_ci", open_task=False, dimension="correctness")
    cycle(worker)
    (f,) = findings(rid)
    assert f["ci_sys_id"] == srv and f["details"]["relations"][0]["verdict"] == "reversed"
    assert "yön ters" in f["details"]["text"]


def test_limit_and_invalid_rule_fail_without_touching_findings(worker):
    tag = f"klm{uuid.uuid4().hex[:6]}"
    for i in range(3):
        ci(f"{tag}-{i}", "cmdb_ci_db_instance")
    rid = rule(tag, "no_relation", ORPHAN_DB, scope_class="cmdb_ci_db_instance", max_findings=2)
    bad = rule(tag, "missing_fields", {"fields": ["olmayan_alan"]}, scope_class="cmdb_ci")
    cycle(worker)
    assert last_run(rid)["status"] == "failed" and "sınırı" in last_run(rid)["note"] and findings(rid) == []
    assert last_run(bad)["status"] == "failed" and "olmayan_alan" in last_run(bad)["note"]


def test_health_measures_dimensions_and_includes_rules(worker):
    tag = f"khs{uuid.uuid4().hex[:6]}"
    ci(f"{tag}-db", "cmdb_ci_db_instance")
    rid = rule(tag, "no_relation", ORPHAN_DB, scope_class="cmdb_ci_db_instance")
    cycle(worker)
    with worker.open() as conn:
        h = measure_health(conn, worker.metadata)
        assert snapshot_health(conn, worker.metadata, force=True) is True
        assert snapshot_health(conn, worker.metadata) is False  # bir saat dolmadan yeniden ölçmez
    assert set(h["dimensions"]) == {"completeness", "correctness", "compliance", "coverage"}
    metric = next(m for m in h["dimensions"]["completeness"]["metrics"] if m["id"] == f"rule:{rid}")
    assert metric["open"] == 1 and metric["total"] == 1 and metric["pct"] == 0.0
    assert h["overall"] is not None and h["grade"] in ("good", "warn", "bad")


def test_worker_cannot_except_define_or_decide(worker):
    tag = f"kyt{uuid.uuid4().hex[:6]}"
    ci(f"{tag}-db", "cmdb_ci_db_instance")
    rid = rule(tag, "no_relation", ORPHAN_DB, scope_class="cmdb_ci_db_instance")
    cycle(worker)
    (f,) = findings(rid)
    with owner() as c:
        c.execute("SET ROLE evren_app_governance")
        for stmt, args in (("UPDATE insight.finding SET exception_until = now() + interval '1 day' WHERE sys_id = %s",
                            (f["sys_id"],)),
                           ("UPDATE insight.quality_rule SET state = 'paused' WHERE sys_id = %s", (rid,)),
                           ("DELETE FROM insight.finding WHERE sys_id = %s", (f["sys_id"],))):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt, args)
        with pytest.raises(psycopg.errors.CheckViolation):  # istisnasız "istisna" durumu olmaz
            c.execute("UPDATE insight.finding SET state = 'excepted' WHERE sys_id = %s", (f["sys_id"],))
    with owner() as c:  # kapanan bulgu yeniden açılmaz
        c.execute("UPDATE insight.finding SET state = 'resolved', resolved_at = now() WHERE sys_id = %s", (f["sys_id"],))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE insight.finding SET state = 'open' WHERE sys_id = %s", (f["sys_id"],))


# ------------------------------------------------------------------ kapsam boşluğu (P1.1, ADR-0046)
COVERAGE = {"roles": ["Monitoring", "EndpointSecurity"], "fresh_days": 7,
            "observed_by": ["Virtualization", "Discovery"], "observed_within_hours": 48}


def healthy_runs(*roles: str) -> None:
    """Rollerin kaynaklarına sağlıklı son çalışma (devre kesici OK); kural sonuç verebilsin."""
    with owner() as c:
        for (src,) in c.execute("SELECT name FROM ingest.data_source WHERE role = ANY(%s)", (list(roles),)).fetchall():
            c.execute("""INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, finished_at, completed,
                                                         breaker_state)
                         VALUES (gen_random_uuid(), %s, 'snapshot', clock_timestamp(), clock_timestamp(), true, 'ok')""",
                      (src,))


def seen(ci_id: str, source: str, days_ago: float) -> None:
    """CI'ın ``source`` tarafından ``days_ago`` gün önce görülmesi (kaynak kaydı + etkin bağ)."""
    key = f"{source}-{ci_id[:12]}"
    with owner() as c:
        c.execute("""INSERT INTO ingest.source_record (data_source, source_key, class_hint, content_hash, last_seen)
                     VALUES (%s, %s, 'cmdb_ci_linux_server', 'x', now() - make_interval(secs => %s))
                     ON CONFLICT (data_source, source_key) DO UPDATE SET last_seen = EXCLUDED.last_seen""",
                  (source, key, days_ago * 86400))
        c.execute("""INSERT INTO ingest.identity_link (data_source, source_key, ci_sys_id, method, decided_by)
                     VALUES (%s, %s, %s, 'rule', 'test') ON CONFLICT DO NOTHING""", (source, key, ci_id))


def test_coverage_gap_collects_missing_roles_in_one_finding_and_closes_when_covered(worker):
    tag = f"kps{uuid.uuid4().hex[:6]}"
    sahip = person()
    srv = ci(f"{tag}-calisan", "cmdb_ci_linux_server", owned_by=sahip, used_for="Production")
    kapali = ci(f"{tag}-kapali", "cmdb_ci_linux_server", owned_by=sahip, used_for="Production")
    seen(srv, "vCenter", 0.1)            # çalışıyor (sanallaştırma 2,4 saat önce gördü)
    seen(srv, "Zabbix", 14)              # izleme 14 gündür görmüyor
    seen(kapali, "vCenter", 10)          # 10 gündür hiçbir gözlemci görmüyor: kapatılmış, görev açılmaz
    healthy_runs("Monitoring", "EndpointSecurity", "Virtualization", "Discovery")
    rid = rule(tag, "coverage_gap", COVERAGE, scope_class="cmdb_ci_server", query=f"nameSTARTSWITH{tag}",
               dimension="coverage")
    cycle(worker)
    run = last_run(rid)
    assert run["status"] == "done", run["note"]
    [f] = findings(rid)
    assert f["ci_name"] == f"{tag}-calisan" and f["details"]["missing"] == ["Monitoring", "EndpointSecurity"]
    assert f["details"]["last_seen"]["EndpointSecurity"] is None and f["details"]["last_seen"]["Monitoring"]
    assert f["details"]["text"] == "İzleme rolünde 14 gündür görülmüyor; Uç nokta güvenliği rolünde hiç görülmedi"
    [t] = tasks(f["sys_id"])
    assert t["state"] == "open" and "14 gündür" in t["description"]
    # İzleme giderildi: bulgu aynı kalır, yalnızca uç nokta güvenliği eksik; görev açık
    seen(srv, "Zabbix", 0)
    healthy_runs("Monitoring", "EndpointSecurity", "Virtualization", "Discovery")
    request_run(rid)
    cycle(worker)
    [f2] = findings(rid)
    assert f2["sys_id"] == f["sys_id"] and f2["state"] == "open" and f2["details"]["missing"] == ["EndpointSecurity"]
    assert tasks(f["sys_id"])[0]["state"] == "open" and len(tasks(f["sys_id"])) == 1
    # Uç nokta güvenliği de gördü: bulgu kapanır, görev kendiliğinden kapanır
    seen(srv, "Defender", 0)
    healthy_runs("Monitoring", "EndpointSecurity", "Virtualization", "Discovery")
    request_run(rid)
    cycle(worker)
    [f3] = findings(rid)
    assert f3["state"] == "resolved"
    assert tasks(f["sys_id"])[0]["state"] != "open"


def test_coverage_gap_is_inconclusive_when_an_observer_role_is_unhealthy(worker):
    tag = f"kpi{uuid.uuid4().hex[:6]}"
    srv = ci(f"{tag}-srv", "cmdb_ci_linux_server", used_for="Production")
    seen(srv, "vCenter", 0.1)
    healthy_runs("Monitoring", "EndpointSecurity", "Discovery")
    with owner() as c:  # sanallaştırma kaynağının son çalışması tamamlanmamış
        for (src,) in c.execute("SELECT name FROM ingest.data_source WHERE role = 'Virtualization'").fetchall():
            c.execute("INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, completed) "
                      "VALUES (gen_random_uuid(), %s, 'snapshot', clock_timestamp(), false)", (src,))
    rid = rule(tag, "coverage_gap", COVERAGE, scope_class="cmdb_ci_server", dimension="coverage")
    cycle(worker)
    assert last_run(rid)["status"] == "inconclusive" and findings(rid) == []


def queue_item(source: str, key: str, candidate: str) -> str:
    """Kaynağın eşleşmemiş kaydı kuyrukta; aday olarak ``candidate`` CI'ını gösteriyor."""
    with owner() as c:
        c.execute("""INSERT INTO ingest.source_record (data_source, source_key, class_hint, content_hash, last_seen)
                     VALUES (%s, %s, 'cmdb_ci_linux_server', 'x', now())""", (source, key))
        return c.execute("""INSERT INTO ingest.match_candidate (data_source, source_key, reason, candidates)
                            VALUES (%s, %s, 'Kural 2 CI ile eşleşti', %s) RETURNING sys_id""",
                         (source, key, Jsonb([{"sys_id": candidate, "probability": 0.99}]))).fetchone()[0]


def test_identity_gap_opens_no_owner_task_and_closes_when_the_queue_links_the_record(worker):
    """P1.2: izleme sunucuyu tanıyor ama kaydı kuyrukta bekliyor → sahibine görev yok; kuyruk bağlayınca bulgu kapanır."""
    tag = f"kik{uuid.uuid4().hex[:6]}"
    srv = ci(f"{tag}-srv", "cmdb_ci_linux_server", owned_by=person(), used_for="Production")
    seen(srv, "vCenter", 0.1)
    qid = queue_item("Zabbix", f"{tag}-zbx", srv)
    healthy_runs("Monitoring", "Virtualization", "Discovery")
    rid = rule(tag, "coverage_gap", {"roles": ["Monitoring"], "observed_by": ["Virtualization", "Discovery"]},
               scope_class="cmdb_ci_server", dimension="coverage")
    cycle(worker)
    [f] = findings(rid)
    d = f["details"]
    assert d["identity_only"] is True and d["identity"][0]["kind"] == "queue" and d["identity"][0]["candidate"] == qid
    assert "eşleştirme kuyruğunda" in d["text"] and tasks(f["sys_id"]) == []
    with owner() as c:  # eşleştirme analisti kaydı sunucuya bağladı
        c.execute("""INSERT INTO ingest.identity_link (data_source, source_key, ci_sys_id, method, decided_by)
                     VALUES ('Zabbix', %s, %s, 'manual', 'analist')""", (f"{tag}-zbx", srv))
        c.execute("UPDATE ingest.match_candidate SET status = 'accepted', decided_by = 'analist', decided_at = now() "
                  "WHERE sys_id = %s", (qid,))
    healthy_runs("Monitoring", "Virtualization", "Discovery")
    request_run(rid)
    cycle(worker)
    assert findings(rid)[0]["state"] == "resolved" and tasks(f["sys_id"]) == []


def test_mixed_gap_opens_owner_task_and_duplicate_is_a_hint(worker):
    """İzleme için kuyruk ipucu, uç nokta güvenliği gerçekten eksik → sahibine görev (metinde kimlik notu); başka bir
    örnekte aynı adlı, izleme kaynağına bağlı ikinci CI olası mükerrer ipucudur."""
    tag = f"kim{uuid.uuid4().hex[:6]}"
    srv = ci(f"{tag}-srv", "cmdb_ci_linux_server", owned_by=person(), used_for="Production")
    seen(srv, "vCenter", 0.1)
    queue_item("Zabbix", f"{tag}-zbx", srv)
    dup_src = ci(f"{tag}-dup", "cmdb_ci_linux_server", owned_by=person(), used_for="Production")
    seen(dup_src, "vCenter", 0.1)
    twin = ci(f"{tag}-dup.corp.local", "cmdb_ci_linux_server")  # aynı kısa ad; izleme kaynağı buna bağlanmış
    seen(twin, "Zabbix", 0)
    healthy_runs("Monitoring", "EndpointSecurity", "Virtualization", "Discovery")
    rid = rule(tag, "coverage_gap", COVERAGE, scope_class="cmdb_ci_server", dimension="coverage")
    cycle(worker)
    by_ci = {f["ci_name"]: f for f in findings(rid)}
    mixed = by_ci[f"{tag}-srv"]["details"]
    assert mixed["identity_only"] is False and mixed["identity_roles"] == ["Monitoring"]
    [t] = tasks(by_ci[f"{tag}-srv"]["sys_id"])
    assert "olası kimlik boşluğu" in t["description"]
    dup = by_ci[f"{tag}-dup"]["details"]
    assert [h["kind"] for h in dup["identity"]] == ["duplicate"] and dup["identity"][0]["ci"] == twin
    assert dup["identity_roles"] == ["Monitoring"] and dup["identity_only"] is False  # uç nokta yine eksik
    assert f"{tag}-dup.corp.local" in by_ci[f"{tag}-dup"]["details"]["text"]
