"""Değişiklik kayıtları ve yetkisiz değişiklik tespiti (U5.5, F1-04, ADR-0025) — gerçek uzlaştırıcı, PostgreSQL.

Akış: ServiceNow CMDB ilk yüklemesi → ServiceNow Change (change_request + task_ci) → BMC keşfi iki kez; ikinci keşifte
RAM/OS değişir. Değişiklik kaydı örnekleri (sentetik): CHG0030001 ist-app-02 için onaylı ve uygulamada (task_ci),
CHG0030002 esx01 için yetkilendirme bekliyor, CHG0030004 esx01 için iptal. Kaynaklar simülatördür.
"""
from __future__ import annotations

import dataclasses
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import REPO

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources import SOURCES
from evren_core.connectors.sources.bmc_discovery import BMCDiscoveryClient, BMCDiscoverySimulator
from evren_core.connectors.sources.servicenow import ServiceNowClient, ServiceNowSimulator
from evren_core.envelope import IngestRecord
from evren_reconciler import ReconcilerStore

T0 = datetime(2026, 11, 26, 7, 0, tzinfo=UTC)      # ilk yükleme ve ilk keşif
T1 = T0 + timedelta(hours=6)                         # ikinci keşif: değişiklik görüldü
APP = "5b1c2d3e4f5061728394a5b6c7d8e9f0"           # ist-app-02 — CHG0030001 kapsar
ESX = "7d3e4f5061728394a5b6c7d8e9f0a1b2"           # esx01 — yalnızca onaysız/iptal kayıt
PW = secrets.token_hex(8)


class Capture(DryRunGateway):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[IngestRecord] = []

    def send(self, run_id, records):
        self.sent.extend(records)
        return super().send(run_id, records)


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def pull(name: str, at: datetime) -> tuple[str, list[IngestRecord]]:
    if name == "servicenow":
        src = ServiceNowClient(HttpClient("https://sn.test", transport=ServiceNowSimulator.from_dir(
            REPO / "connectors/servicenow_cmdb/samples"), auth_header=HttpClient.basic("u", PW), retries=0))
        ds, folder = "ServiceNow", "servicenow_cmdb"
    elif name == "change":
        spec = SOURCES["servicenow_change"]
        http = HttpClient("https://sn.test", transport=ServiceNowSimulator.from_dir(
            REPO / "connectors/servicenow_change/samples"), auth_header=HttpClient.basic("u", PW), retries=0)
        src = spec.build(http, {})
        src.clock = lambda: at  # geriye bakış penceresi örnek tarihlere göre
        ds, folder = "ServiceNowChange", "servicenow_change"
    else:
        src = BMCDiscoveryClient(HttpClient("https://bmc.test", transport=BMCDiscoverySimulator.from_dir(
            REPO / "connectors/bmc_discovery/samples"), auth_header="Bearer b", retries=0))
        ds, folder = "BMC", "bmc_discovery"
    gw = Capture()
    assert ConnectorRunner(src, load_mappings(REPO / "connectors" / folder, ds), gw, clock=lambda: at).run().completed
    return ds, gw.sent


def run(store: ReconcilerStore, ds: str, recs: list[IngestRecord], at: datetime, *, completed: bool = True,
        finish: bool = True) -> uuid.UUID:
    run_id = uuid.uuid4()
    store.start_run(run_id, ds, started_at=at)
    for r in recs:
        store.apply(r.model_copy(update={"run_id": run_id, "observed_at": at}))
    if finish:
        store.finish_run(run_id, completed=completed, finished_at=at + timedelta(minutes=5))
    return run_id


def changed_bmc(at: datetime, **by_ci: dict) -> list[IngestRecord]:
    """BMC örneğinin ikinci keşfi: verilen CI'ların (ServiceNow sys_id'ye bağlanan kayıtlar) öznitelikleri değişir."""
    _ds, recs = pull("bmc", at)
    names = {APP: "ist-app-02", ESX: "esx01"}
    out = []
    for r in recs:
        for ci, attrs in by_ci.items():
            if str(r.attributes.get("name", "")).lower() == names[ci]:
                r = r.model_copy(update={"attributes": {**r.attributes, **attrs}})
        out.append(r)
    return out


@pytest.fixture
def world(as_reconciler, metadata):
    """İlk yükleme, değişiklik kayıtları ve ilk keşif (T0). Deponun saati ``clock[0]`` ile ilerletilir."""
    clock = [T0]
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: clock[0])
    for name in ("servicenow", "change", "bmc"):
        ds, recs = pull(name, T0)
        run(store, ds, recs, T0)
    return store, clock


def ram_of(conn, ci):
    return fetch(conn, "SELECT (class_attributes->>'ram')::int FROM cmdb.cmdb_ci WHERE sys_id = %s", ci)[0][0]


# ---------------------------------------------------------------------------------------------- değişiklik kayıtları
def test_change_records_land_in_ref_tables_with_role_vocabulary(as_reconciler, world):
    rows = fetch(as_reconciler, """SELECT number, type, state, approval, cmdb_ci, start_date, work_end
                                     FROM ref.sn_change_request ORDER BY number""")
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("CHG0030001", "normal", "implement", "approved"), ("CHG0030002", "normal", "authorize", "requested"),
        ("CHG0030003", "standard", "closed", "not_requested"), ("CHG0030004", "emergency", "canceled", "approved")]
    assert rows[1][4] == ESX and rows[0][5] == datetime(2026, 11, 1, tzinfo=UTC) and rows[0][6] is None
    assert sorted(fetch(as_reconciler, "SELECT task, ci_item FROM ref.sn_task_ci")) == [
        ("c1a0000000000000000000000000c001", APP), ("c1a0000000000000000000000000c002", ESX)]  # olay kaydı yok
    # değişiklik kaydı CI olmaz, kimlik bağı kurulmaz
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_link WHERE data_source = 'ServiceNowChange'") \
        == [(0,)]


def test_only_the_change_role_writes_change_tables(as_reconciler, metadata, world):
    store, _ = world
    chg = IngestRecord(data_source="ServiceNow", run_id=uuid.uuid4(), source_key="d" * 32, class_hint="change_request",
                       attributes={"number": "CHG0099999", "state": "closed", "approval": "approved"}, observed_at=T0)
    assert store.apply(chg).outcome == "reference_skipped"  # ITSM rolü değişiklik kaydı yazamaz
    group = IngestRecord(data_source="ServiceNowChange", run_id=uuid.uuid4(), source_key="e" * 32,
                         class_hint="sys_user_group", attributes={"name": "Sahte grup"}, observed_at=T0)
    assert store.apply(group).outcome == "reference_skipped"  # değişiklik rolü referans tablosu yazamaz
    bad = chg.model_copy(update={"data_source": "ServiceNowChange", "source_key": "f" * 32,
                                 "attributes": {"number": "CHG0099998", "state": "-1"}})
    assert store.apply(bad).outcome == "reference_invalid"  # çevrilmemiş ürün kodu sözlüğe girmez
    assert fetch(as_reconciler, "SELECT count(*) FROM ref.sn_change_request WHERE number LIKE 'CHG00999%%'") == [(0,)]
    md = dataclasses.replace(metadata, data_sources=[{**d, "lifecycle": "shadow"} if d["name"] == "ServiceNowChange"
                                                     else d for d in metadata.data_sources])
    shadow = ReconcilerStore(as_reconciler, md, clock=lambda: T0)
    rec = chg.model_copy(update={"data_source": "ServiceNowChange", "source_key": "a" * 32,
                                 "attributes": {"number": "CHG0099997", "state": "closed"}})
    assert shadow.apply(rec).outcome == "reference_skipped"  # gölgedeki yeni ürün yazmaz (ADR-0009)


# ---------------------------------------------------------------------------------------------- tespit
def test_change_inside_an_approved_window_is_authorized(as_reconciler, world):
    store, clock = world
    old = ram_of(as_reconciler, APP)
    run(store, "BMC", changed_bmc(T1, **{APP: {"ram": old * 2}}), T1)
    obs = fetch(as_reconciler, """SELECT attribute, old_value, new_value, data_source, observed_from, observed_at,
                                         verdict FROM insight.config_change WHERE ci_sys_id = %s""", APP)
    assert obs == [("ram", old, old * 2, "BMC", T0, T1, "pending")]
    clock[0] = T1 + timedelta(minutes=30)
    assert store.evaluate_config_changes()["authorized"] == 0  # bekleme süresi dolmadı (gecikmeli kayıt payı)
    clock[0] = T1 + timedelta(minutes=70)
    assert store.evaluate_config_changes()["authorized"] == 1
    assert fetch(as_reconciler, """SELECT verdict, change_number, detail->>'window' FROM insight.config_change
                                   WHERE ci_sys_id = %s""", APP) == [("authorized", "CHG0030001", "planlı")]
    assert fetch(as_reconciler, "SELECT count(*) FROM insight.anomaly WHERE kind = 'unauthorized_change'") == [(0,)]


def test_change_without_an_approved_record_opens_an_anomaly(as_reconciler, world):
    store, clock = world
    old = ram_of(as_reconciler, ESX)
    run(store, "BMC", changed_bmc(T1, **{ESX: {"ram": old + 1024, "os_version": "8.0.4"}}), T1)
    clock[0] = T1 + timedelta(hours=2)
    assert store.evaluate_config_changes()["unauthorized"] == 2
    (sev, ds, start, end, details, text), = fetch(
        as_reconciler, """SELECT severity, data_source, window_start, window_end, details, explanation
                            FROM insight.anomaly WHERE kind = 'unauthorized_change' AND ci_sys_id = %s""", ESX)
    assert (sev, ds, start, end) == ("high", "BMC", T0, T1)  # üretim CI'ı: yüksek önem; tek anomali
    assert sorted(a["attribute"] for a in details["attributes"]) == ["os_version", "ram"]
    reasons = {c["number"]: c["reason"] for c in details["considered"]}
    assert "yetkilendirme" in reasons["CHG0030002"] and "bekliyor" in reasons["CHG0030002"]
    assert "iptal" in reasons["CHG0030004"]
    assert text.startswith("esx01: ") and "RAM (MB)" in text and "kapsayan onaylı değişiklik kaydı yok" in text
    assert {v for (v,) in fetch(as_reconciler, "SELECT verdict FROM insight.config_change WHERE ci_sys_id = %s",
                                 ESX)} == {"unauthorized"}


def test_late_change_record_resolves_the_anomaly_and_keeps_the_evidence(as_reconciler, world):
    store, clock = world
    run(store, "BMC", changed_bmc(T1, **{ESX: {"ram": ram_of(as_reconciler, ESX) + 2048}}), T1)
    clock[0] = T1 + timedelta(hours=2)
    store.evaluate_config_changes()
    # acil değişiklik sonradan kaydedildi ve onaylandı
    late = IngestRecord(data_source="ServiceNowChange", run_id=uuid.uuid4(), source_key="c" * 32,
                        class_hint="change_request", observed_at=clock[0],
                        attributes={"number": "CHG0030050", "type": "emergency", "state": "review",
                                    "approval": "approved", "work_start": "2026-11-26 10:00:00",
                                    "work_end": "2026-11-26 11:00:00", "cmdb_ci": ESX})
    assert store.apply(late).outcome == "reference"
    clock[0] += timedelta(minutes=5)
    assert store.evaluate_config_changes()["late_authorized"] == 0  # yeniden deneme 10 dakikada bir
    clock[0] += timedelta(minutes=10)
    assert store.evaluate_config_changes()["late_authorized"] == 1
    (status, related, text, details), = fetch(
        as_reconciler, """SELECT status, related_change, explanation, details FROM insight.anomaly
                            WHERE kind = 'unauthorized_change' AND ci_sys_id = %s""", ESX)
    assert (status, related, details["resolution"]) == ("resolved", "CHG0030050", "late_change_record")
    assert "Sonradan kaydedilen değişiklik kaydı kapsıyor: CHG0030050 (gerçekleşen pencere)" in text
    assert fetch(as_reconciler, """SELECT verdict, change_number, (detail->>'late')::bool FROM insight.config_change
                                   WHERE ci_sys_id = %s""", ESX) == [("authorized", "CHG0030050", True)]


def test_false_positive_is_not_reopened_or_resolved(as_reconciler, world):
    store, clock = world
    run(store, "BMC", changed_bmc(T1, **{ESX: {"ram": ram_of(as_reconciler, ESX) + 4096}}), T1)
    clock[0] = T1 + timedelta(hours=2)
    store.evaluate_config_changes()
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("UPDATE insight.anomaly SET status = 'false_positive' WHERE kind = 'unauthorized_change'")
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    store.apply(IngestRecord(data_source="ServiceNowChange", run_id=uuid.uuid4(), source_key="b" * 32,
                             class_hint="change_request", observed_at=clock[0],
                             attributes={"number": "CHG0030051", "type": "normal", "state": "closed",
                                         "approval": "approved", "start_date": "2026-11-26 08:00:00",
                                         "end_date": "2026-11-26 12:00:00", "cmdb_ci": ESX}))
    clock[0] += timedelta(minutes=30)
    assert store.evaluate_config_changes()["late_authorized"] == 0
    assert fetch(as_reconciler, "SELECT status FROM insight.anomaly WHERE kind = 'unauthorized_change'") == [
        ("false_positive",)]


def test_waits_for_the_run_and_suppresses_when_the_breaker_opened(as_reconciler, world):
    store, clock = world
    run_id = run(store, "BMC", changed_bmc(T1, **{ESX: {"ram": ram_of(as_reconciler, ESX) + 512}}), T1, finish=False)
    clock[0] = T1 + timedelta(hours=2)
    assert sum(store.evaluate_config_changes().values()) == 0  # çalışma kapanmadan karar yok
    store.finish_run(run_id, completed=False, finished_at=T1 + timedelta(minutes=5))  # yarıda kalan çekme
    assert store.evaluate_config_changes()["suppressed"] == 1
    assert fetch(as_reconciler, "SELECT verdict, anomaly_sys_id FROM insight.config_change WHERE ci_sys_id = %s",
                 ESX) == [("suppressed", None)]
    assert fetch(as_reconciler, "SELECT count(*) FROM insight.anomaly WHERE kind = 'unauthorized_change'") == [(0,)]


def test_not_observations(as_reconciler, metadata, world):
    """İlk doldurma, izlenmeyen alan, gözlemci olmayan rol ve aynı değer gözlem üretmez."""
    store, _ = world
    run(store, "BMC", changed_bmc(T1, **{APP: {"cpu_type": "Başka CPU"}}), T1)  # izlenmeyen alan
    run(store, "BMC", changed_bmc(T1 + timedelta(hours=1)), T1 + timedelta(hours=1))  # değişiklik yok
    assert fetch(as_reconciler, "SELECT count(*) FROM insight.config_change") == [(0,)]
    md = dataclasses.replace(metadata, change_control={**metadata.change_control, "observer_roles": ["Network"]})
    other = ReconcilerStore(as_reconciler, md, clock=lambda: T1)
    run(other, "BMC", changed_bmc(T1 + timedelta(hours=2), **{APP: {"ram": 1}}), T1 + timedelta(hours=2))
    assert fetch(as_reconciler, "SELECT count(*) FROM insight.config_change") == [(0,)]  # Keşif gözlemci değil
    assert ram_of(as_reconciler, APP) == 1  # golden record yine de güncellendi


def test_evren_born_ci_matches_changes_through_its_servicenow_sys_id(as_reconciler, metadata):
    """CI Evren'de doğup replikaya gittiyse ServiceNow'daki değişiklik kaydı SN sys_id'sini taşır (sync.sn_ci_map)."""
    clock = [T0]
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: clock[0])
    _ds, recs = pull("bmc", T0)
    run(store, "BMC", recs, T0)
    (ci,), = fetch(as_reconciler, "SELECT ci_sys_id FROM ingest.identity_link WHERE data_source = 'BMC' AND active "
                                  "ORDER BY source_key LIMIT 1")
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("INSERT INTO sync.sn_ci_map (evren_sys_id, sn_sys_id, state) VALUES (%s, %s, 'in_sync')",
                          (ci, "9" * 32))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    store.apply(IngestRecord(data_source="ServiceNowChange", run_id=uuid.uuid4(), source_key="8" * 32,
                             class_hint="change_request", observed_at=T0,
                             attributes={"number": "CHG0030060", "type": "standard", "state": "closed",
                                         "start_date": "2026-11-26 08:00:00", "end_date": "2026-11-26 14:00:00",
                                         "cmdb_ci": "9" * 32}))
    (name,), = fetch(as_reconciler, "SELECT name FROM cmdb.cmdb_ci WHERE sys_id = %s", ci)
    _ds, again = pull("bmc", T1)
    changed = [r.model_copy(update={"attributes": {**r.attributes, "ram": 7}})
               if r.attributes.get("name") == name else r for r in again]
    run(store, "BMC", changed, T1)
    clock[0] = T1 + timedelta(hours=2)
    assert store.evaluate_config_changes()["authorized"] >= 1
    assert fetch(as_reconciler, "SELECT change_number FROM insight.config_change WHERE ci_sys_id = %s", ci) == [
        ("CHG0030060",)]
