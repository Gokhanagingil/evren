"""Görev çalışanı (U8.4, ADR-0035): ServiceNow'a yalnızca standart alanlarla teslim, correlation_id ile tekillik,
hata ve yeniden deneme, ServiceNow'da karşılığı olmayan sorumlu, ServiceNow'da kararsız kapatma, Evren'de kapanınca
ServiceNow kaydının kapatılması, en az yetki. Veriler sentetiktir; ServiceNow bellek içi simülatördür."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from evren_core.governance import ALLOWED_SN_FIELDS, ServiceNowTaskSimulator
from evren_governance import GovernanceConfig, GovernanceWorker


def owner() -> psycopg.Connection:
    return psycopg.connect(autocommit=True)


def person(source: str = "servicenow", active: bool = True) -> str:
    sid = uuid.uuid4().hex
    with owner() as c:
        c.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source, active) VALUES (%s, %s, %s, %s, %s)",
                  (sid, f"gov.{sid[:8]}", f"Görev Kişi {sid[:6]}", source, active))
    return sid


def group(source: str = "servicenow") -> str:
    sid = uuid.uuid4().hex
    with owner() as c:
        c.execute("INSERT INTO ref.sys_user_group (sys_id, name, source) VALUES (%s, %s, %s)",
                  (sid, f"Görev Grup {sid[:6]}", source))
    return sid


def task(*, user: str | None = None, grp: str | None = None, channel: str = "servicenow", title: str | None = None) -> str:
    basis = "assigned" if (user or grp) else "unassigned"
    with owner() as c:
        return c.execute(
            """INSERT INTO gov.task (kind, title, description, assignee_user, assignee_group, assignee_basis, due_at,
                                     source, channel, delivery_status, created_by)
               VALUES ('manual', %s, 'Uygulamanızın kayıtlarını gözden geçirin.', %s, %s, %s, %s, 'manual', %s, %s,
                       'test:kurulum') RETURNING sys_id""",
            (title or f"Test görevi {uuid.uuid4().hex[:6]}", user, grp, basis, datetime.now(UTC) + timedelta(days=7),
             channel, "pending" if channel == "servicenow" else "not_needed")).fetchone()[0]


def row(sys_id: str) -> dict:
    with owner() as c:
        cur = c.execute("SELECT * FROM gov.task WHERE sys_id = %s", (sys_id,))
        names = [d.name for d in cur.description]
        return dict(zip(names, cur.fetchone(), strict=True))


def events(sys_id: str) -> list[str]:
    with owner() as c:
        return [r[0] for r in c.execute("SELECT action FROM gov.task_event WHERE task = %s ORDER BY id", (sys_id,))]


@pytest.fixture
def sim():
    return ServiceNowTaskSimulator()


@pytest.fixture
def worker(sim):
    cfg = GovernanceConfig(conninfo="", public_url="https://evren.ornek.test", worker_id=f"test-gov-{uuid.uuid4().hex[:6]}",
                           sync_minutes=1)
    return GovernanceWorker(cfg, transport=sim, connect=lambda: psycopg.connect())


def cycle(worker: GovernanceWorker) -> dict:
    with worker.open() as conn:
        return worker.cycle(conn)


def test_task_is_delivered_with_standard_fields_only_and_correlation(worker, sim):
    u, g = person(), group()
    t = task(user=u, grp=g)
    totals = cycle(worker)
    assert totals["delivered"] >= 1
    r = row(t)
    assert r["delivery_status"] == "delivered" and r["external_number"].startswith("SCTASK")
    sn = sim.rows[r["external_ref"]]
    assert set(sn) - {"sys_id", "number", "state", "active"} <= ALLOWED_SN_FIELDS and not sim.unknown_fields
    assert sn["correlation_id"] == t and sn["assigned_to"] == u and sn["assignment_group"] == g
    assert sn["short_description"].startswith(f"[Evren {r['number']}]")
    assert f"https://evren.ornek.test/#/gorevler/{t}" in sn["description"]
    assert len(sn["due_date"]) == 19  # "YYYY-MM-DD HH:MM:SS" (UTC)
    assert events(t) == ["delivered"]
    # İkinci tur aynı görevi yeniden açmaz
    before = len(sim.rows)
    cycle(worker)
    assert len(sim.rows) == before


def test_retry_after_lost_response_does_not_open_a_second_record(worker, sim):
    t = task(user=person())
    # Önceki deneme ServiceNow'da kaydı açmış ama yanıt kaybolmuş
    sim.rows["a" * 32] = {"sys_id": "a" * 32, "number": "SCTASK0000999", "state": "1", "active": "true",
                          "correlation_id": t}
    cycle(worker)
    r = row(t)
    assert r["external_ref"] == "a" * 32 and r["delivery_status"] == "delivered"
    assert sum(1 for x in sim.rows.values() if x.get("correlation_id") == t) == 1


def test_unreachable_servicenow_marks_failed_without_secrets_then_recovers(worker, sim):
    t = task(user=person())
    sim.fail_next = 50
    cycle(worker)
    r = row(t)
    assert r["delivery_status"] == "failed" and r["delivery_attempts"] == 1
    assert "503" in r["delivery_note"] and "simulator" not in r["delivery_note"].lower()
    assert "delivery_failed" in events(t)
    sim.fail_next = 0
    cycle(worker)
    assert row(t)["delivery_status"] == "delivered"


def test_assignee_without_servicenow_counterpart_stays_in_evren(worker, sim):
    t = task(user=person(source="evren"))
    cycle(worker)
    r = row(t)
    assert r["delivery_status"] == "kept_in_evren" and "Evren'de açık" in r["delivery_note"]
    assert not any(x.get("correlation_id") == t for x in sim.rows.values())


def test_closed_in_servicenow_without_decision_is_flagged_and_task_stays_open(worker, sim):
    t = task(user=person())
    cycle(worker)
    sim.close_externally(row(t)["external_ref"], state="3")
    with psycopg.connect(autocommit=True) as c:  # eşitleme aralığını beklemeden
        c.execute("UPDATE gov.task SET last_synced_at = now() - interval '2 hours' WHERE sys_id = %s", (t,))
    cycle(worker)
    r = row(t)
    assert r["state"] == "open" and r["external_closed"] is True and r["external_state"] == "3"
    assert events(t)[-1] == "external_closed"


def test_task_closed_in_evren_closes_servicenow_record_with_decision(worker, sim):
    t = task(user=person())
    cycle(worker)
    ref = row(t)["external_ref"]
    with owner() as c:
        c.execute("""UPDATE gov.task SET state = 'done', outcome = 'corrected', outcome_note = 'Sahip güncellendi',
                            closed_by = 'test:kisi', closed_at = now() WHERE sys_id = %s""", (t,))
    cycle(worker)
    r = row(t)
    assert r["delivery_status"] == "closed_sent" and r["external_closed"] is True
    assert sim.rows[ref]["state"] == "3" and sim.rows[ref]["active"] == "false"
    assert sim.rows[ref]["close_notes"] == "Evren'de kapatıldı: Düzeltildi — Sahip güncellendi"
    # İptal edilen görev "kapatıldı, tamamlanmadı" durumuyla kapanır
    t2 = task(user=person())
    cycle(worker)
    with owner() as c:
        c.execute("""UPDATE gov.task SET state = 'cancelled', outcome = 'cancelled', closed_by = 'test:yonetici',
                            closed_at = now() WHERE sys_id = %s""", (t2,))
    cycle(worker)
    assert sim.rows[row(t2)["external_ref"]]["state"] == "4"


def test_evren_channel_tasks_are_not_sent_and_heartbeat_reports(worker, sim):
    t = task(user=person(), channel="evren")
    cycle(worker)
    assert row(t)["delivery_status"] == "not_needed"
    assert not any(x.get("correlation_id") == t for x in sim.rows.values())
    with owner() as c:
        kind, details = c.execute("SELECT kind, details FROM ingest.worker_heartbeat WHERE worker_id = %s",
                                  (worker.config.worker_id,)).fetchone()
    assert kind == "governance" and details["channel"] == "servicenow" and "pending" in details


def test_closed_task_cannot_be_reopened_and_worker_cannot_decide():
    t = task(user=person(), channel="evren")
    with owner() as c:
        c.execute("""UPDATE gov.task SET state = 'done', outcome = 'confirmed', closed_by = 'x', closed_at = now()
                      WHERE sys_id = %s""", (t,))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE gov.task SET state = 'open', closed_at = NULL, outcome = NULL WHERE sys_id = %s", (t,))
        c.execute("INSERT INTO gov.task_event (task, actor, action) VALUES (%s, 'test', 'completed')", (t,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # olay geçmişi salt ekleme
            c.execute("DELETE FROM gov.task_event WHERE task = %s", (t,))
    t2 = task(user=person(), channel="evren")
    with owner() as c:
        c.execute("SET ROLE evren_app_governance")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE gov.task SET state = 'done', outcome = 'confirmed', closed_at = now() WHERE sys_id = %s",
                      (t2,))


def test_channel_settings_reject_non_task_tables():
    from evren_core.governance import ChannelSettings

    with pytest.raises(ValueError):
        ChannelSettings.from_json({"table": "sys_user"})
    with pytest.raises(ValueError):
        ChannelSettings.from_json({"table": "cmdb_ci_server"})
    assert ChannelSettings.from_json({"table": "vtb_task", "default_group": "b" * 32}).default_group == "b" * 32
    assert isinstance(ServiceNowTaskSimulator(), ServiceNowTaskSimulator)
