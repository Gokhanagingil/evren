"""Eskime işletimi tatbikatı (U7.1, F1-06, ADR-0028) — gerçek uzlaştırıcı, devre kesici ve PostgreSQL, en az yetkili rol.

Keşif kaynağı tam liste çalışmalarıyla 20 sunucu bildirir; dördüncü çalışmada biri yoktur. Devre kesici OK olduğu için
kayıt ``missing`` olur; süre dolunca emekliye ayırma adayı açılır. Kısmi çalışma kararı bastırır; ITSM kaydı CI'ı
"canlı" tutmaz; CI yeniden görülünce öneri kapanır; insan kararıyla emekliye ayrılan CI yeniden görülürse anomali açılır.
Veriler sentetiktir.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from evren_core.envelope import IngestRecord
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def as_owner(conn, sql, *params):
    conn.execute("RESET ROLE")
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else None
    conn.execute("SET ROLE evren_app_reconciler")
    return rows


class Fleet:
    """Bir keşif kaynağının gördüğü sentetik sunucu filosu; her çalışma tam listedir."""

    def __init__(self, conn, metadata, size: int = 20, source: str = "BMC") -> None:
        self.conn, self.md, self.source = conn, metadata, source
        self.tag = uuid.uuid4().hex[:6].upper()
        self.hosts = [f"st-{self.tag}-{i:02d}" for i in range(size)]
        self.ci: dict[str, str] = {}

    def run(self, at: datetime, *, without=(), completed: bool = True) -> str:
        store = ReconcilerStore(self.conn, self.md, clock=lambda: at)
        run = store.start_run(uuid.uuid4(), self.source, started_at=at)
        for i, host in enumerate(self.hosts):
            if host in without:
                continue
            res = store.apply(IngestRecord(data_source=self.source, run_id=run, source_key=host,
                                           class_hint="cmdb_ci_linux_server", observed_at=at,
                                           attributes={"name": host, "serial_number": f"ST{self.tag}{i:02d}"}))
            self.ci[host] = res.ci_sys_id
        return store.finish_run(run, completed=completed, finished_at=at).state.value

    def baseline(self, start: datetime) -> None:
        for k in range(3):
            self.run(start + k * DAY)


def store_at(conn, metadata, at):
    return ReconcilerStore(conn, metadata, clock=lambda: at)


def cases(conn, ci):
    return fetch(conn, "SELECT stage, status, last_seen, observations FROM insight.staleness_case WHERE ci_sys_id = %s "
                       "ORDER BY opened_at", ci)


def test_drill_missing_server_becomes_candidate_only_while_sources_are_healthy(as_reconciler, metadata):
    fleet = Fleet(as_reconciler, metadata)
    fleet.baseline(NOW - 50 * DAY)                     # son tam görülme: NOW-48 gün
    gone = fleet.hosts[0]
    assert fleet.run(NOW - DAY, without={gone}) == "ok"  # 20 → 19: devre kesici OK, kayıt missing
    ci = fleet.ci[gone]
    # ITSM kaydı (aynı seri) CI'ı "canlı" tutmaz (ADR-0028 §1)
    sn = store_at(as_reconciler, metadata, NOW)
    run = sn.start_run(uuid.uuid4(), "ServiceNow", started_at=NOW)
    assert sn.apply(IngestRecord(data_source="ServiceNow", run_id=run, source_key="sn-" + gone,
                                 class_hint="cmdb_ci_linux_server", observed_at=NOW,
                                 attributes={"name": gone, "serial_number": f"ST{fleet.tag}00"})).ci_sys_id == ci
    store = store_at(as_reconciler, metadata, NOW)
    assert store.evaluate_staleness()["opened"] == 1
    ((stage, status, last_seen, obs),) = cases(as_reconciler, ci)
    assert (stage, status, last_seen) == ("candidate", "open", NOW - 48 * DAY)  # donanım: 45 gün → aday
    assert [o["data_source"] for o in obs] == ["BMC"] and obs[0]["status"] == "missing"
    # Diğer 19 sunucu görülüyor: öneri yok; golden record kendiliğinden değişmedi
    assert fetch(as_reconciler, "SELECT count(*) FROM insight.staleness_case")[0][0] == 1
    assert fetch(as_reconciler, "SELECT life_cycle_stage, install_status FROM cmdb.cmdb_ci WHERE sys_id = %s",
                 ci) == [(None, None)]
    # Kısmi çalışma: karar bastırılır, öneriye dokunulmaz (kural 4)
    assert fleet.run(NOW, without=set(fleet.hosts[:15]), completed=False) == "open"
    later = store_at(as_reconciler, metadata, NOW + DAY)
    counts = later.evaluate_staleness()
    assert counts["suppressed"] >= 1 and counts["recovered"] == 0
    assert cases(as_reconciler, ci)[0][1] == "open"
    # Sunucu yeniden görülür (tam ve sağlıklı çalışma): öneri kapanır
    assert fleet.run(NOW + DAY) == "ok"
    assert store_at(as_reconciler, metadata, NOW + DAY).evaluate_staleness()["recovered"] == 1
    assert cases(as_reconciler, ci)[0][1] == "recovered"


def test_stale_then_candidate_and_dismissal_is_respected(as_reconciler, metadata):
    fleet = Fleet(as_reconciler, metadata)
    fleet.baseline(NOW - 25 * DAY)                     # son görülme NOW-23 gün
    gone = fleet.hosts[1]
    assert fleet.run(NOW - DAY, without={gone}) == "ok"
    ci = fleet.ci[gone]
    store_at(as_reconciler, metadata, NOW).evaluate_staleness()
    assert [c[:2] for c in cases(as_reconciler, ci)] == [("stale", "open")]  # 14 ≤ 23 < 45
    # Aday olmayan CI emekliye ayrılamaz
    (case_id,), = fetch(as_reconciler, "SELECT sys_id FROM insight.staleness_case WHERE ci_sys_id = %s", ci)
    as_owner(as_reconciler, "INSERT INTO ingest.retirement_request (case_sys_id, ci_sys_id, action, note, requested_by) "
                            "VALUES (%s, %s, 'retire', 'Kapatıldı sanıyorum', 'veri-sorumlusu')", case_id, ci)
    (res,) = store_at(as_reconciler, metadata, NOW).apply_retirements()
    assert res.outcome == "retire_rejected" and "henüz emekliye ayırma adayı değil" in res.summary
    # Süre ilerler: aday olur (aynı öneri ilerler)
    assert store_at(as_reconciler, metadata, NOW + 30 * DAY).evaluate_staleness()["advanced"] == 1
    assert [c[:2] for c in cases(as_reconciler, ci)] == [("candidate", "open")]
    # Koru: kapanır ve aynı son görülme için yeniden açılmaz
    as_owner(as_reconciler, "INSERT INTO ingest.retirement_request (case_sys_id, ci_sys_id, action, note, requested_by) "
                            "VALUES (%s, %s, 'dismiss', 'Yedek sunucu; bilerek kapalı', 'veri-sorumlusu')", case_id, ci)
    assert [r.outcome for r in store_at(as_reconciler, metadata, NOW + 30 * DAY).apply_retirements()] == [
        "retire_dismissed"]
    assert store_at(as_reconciler, metadata, NOW + 31 * DAY).evaluate_staleness()["opened"] == 0
    assert fetch(as_reconciler, "SELECT status, decided_by, decision_note FROM insight.staleness_case "
                                "WHERE sys_id = %s", case_id) == [("dismissed", "veri-sorumlusu",
                                                                    "Yedek sunucu; bilerek kapalı")]


def test_retirement_writes_lifecycle_through_manual_record_and_reappearance_opens_anomaly(as_reconciler, metadata):
    fleet = Fleet(as_reconciler, metadata)
    fleet.baseline(NOW - 60 * DAY)
    gone = fleet.hosts[2]
    assert fleet.run(NOW - DAY, without={gone}) == "ok"
    ci = fleet.ci[gone]
    store_at(as_reconciler, metadata, NOW).evaluate_staleness()
    (case_id,), = fetch(as_reconciler, "SELECT sys_id FROM insight.staleness_case WHERE ci_sys_id = %s", ci)
    as_owner(as_reconciler, "INSERT INTO ingest.retirement_request (case_sys_id, ci_sys_id, action, note, requested_by) "
                            "VALUES (%s, %s, 'retire', 'Donanım iade edildi', 'veri-sorumlusu')", case_id, ci)
    (res,) = store_at(as_reconciler, metadata, NOW).apply_retirements()
    assert res.outcome == "retire_retired" and "silinmedi" in res.summary
    assert fetch(as_reconciler, """SELECT life_cycle_stage, life_cycle_stage_status, install_status, operational_status
                                     FROM cmdb.cmdb_ci WHERE sys_id = %s""", ci) == [("End of Life", "Retired", 7, 6)]
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_link WHERE ci_sys_id = %s AND active "
                                "AND data_source = 'BMC'", ci)[0][0] == 1  # bağ kalır, CI silinmez
    assert as_owner(as_reconciler, "SELECT DISTINCT reason, actor FROM audit.ci_change WHERE ci_sys_id = %s "
                                   "AND attribute = 'life_cycle_stage'", ci) == [("staleness", "veri-sorumlusu")]
    assert cases(as_reconciler, ci)[0][1] == "retired"
    assert store_at(as_reconciler, metadata, NOW + DAY).evaluate_staleness()["opened"] == 0  # emekli: yeni öneri yok
    # Emekli CI yeniden görülür: anomali, CI kendiliğinden canlanmaz
    assert fleet.run(NOW + 2 * DAY) == "ok"
    assert store_at(as_reconciler, metadata, NOW + 2 * DAY).evaluate_staleness()["reappeared"] == 1
    assert fetch(as_reconciler, "SELECT kind, status FROM insight.anomaly WHERE ci_sys_id = %s", ci) == [
        ("retired_ci_seen", "open")]
    assert fetch(as_reconciler, "SELECT life_cycle_stage FROM cmdb.cmdb_ci WHERE sys_id = %s", ci) == [("End of Life",)]
    assert store_at(as_reconciler, metadata, NOW + 3 * DAY).evaluate_staleness()["reappeared"] == 0  # bir kez


def test_retirement_is_rejected_when_a_source_became_unhealthy_or_ci_is_seen(as_reconciler, metadata):
    fleet = Fleet(as_reconciler, metadata)
    fleet.baseline(NOW - 60 * DAY)
    gone = fleet.hosts[3]
    assert fleet.run(NOW - 2 * DAY, without={gone}) == "ok"
    ci = fleet.ci[gone]
    store_at(as_reconciler, metadata, NOW).evaluate_staleness()
    (case_id,), = fetch(as_reconciler, "SELECT sys_id FROM insight.staleness_case WHERE ci_sys_id = %s", ci)
    assert fleet.run(NOW - DAY, without={gone}, completed=False) == "open"  # karar ile uygulama arasında kısmi çalışma
    as_owner(as_reconciler, "INSERT INTO ingest.retirement_request (case_sys_id, ci_sys_id, action, note, requested_by) "
                            "VALUES (%s, %s, 'retire', 'Kapatıldı', 'veri-sorumlusu')", case_id, ci)
    (res,) = store_at(as_reconciler, metadata, NOW).apply_retirements()
    assert res.outcome == "retire_rejected" and "devre kesici" in res.summary
    assert fetch(as_reconciler, "SELECT life_cycle_stage FROM cmdb.cmdb_ci WHERE sys_id = %s", ci) == [(None,)]
    assert cases(as_reconciler, ci)[0][1] == "open"


def test_ci_without_observer_records_is_not_evaluated(as_reconciler, metadata):
    store = store_at(as_reconciler, metadata, NOW - 90 * DAY)
    run = store.start_run(uuid.uuid4(), "ServiceNow", started_at=NOW - 90 * DAY)
    res = store.apply(IngestRecord(data_source="ServiceNow", run_id=run, source_key="sn-only-" + uuid.uuid4().hex[:6],
                                   class_hint="cmdb_ci_linux_server", observed_at=NOW - 90 * DAY,
                                   attributes={"name": "yalniz-itsm", "serial_number": "ITSMONLY01"}))
    later = store_at(as_reconciler, metadata, NOW)
    cls, verdict = later.staleness_verdicts([res.ci_sys_id])[res.ci_sys_id]
    assert (cls, verdict.outcome) == ("cmdb_ci_linux_server", "not_observed")
    assert later.evaluate_staleness()["opened"] == 0
