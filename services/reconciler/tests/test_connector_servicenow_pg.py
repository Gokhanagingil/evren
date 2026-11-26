"""ServiceNow bağlayıcısı → gerçek uzlaştırıcı (U5.2, ADR-0004/0020): sys_id korunur, keşif kaynakları SN CI'ına bağlanır.

Olay omurgasından gelen ServiceNow kaydı (``apply``) bağı yoksa kimlik çözümlemesine girmez: ``sync.sn_ci_map``'te
eşlemesi varsa Evren CI'ına bağlanır, yoksa CI aynı sys_id ile açılır veya o sys_id'li CI'a bağlanır. Gölge kaynak CI
açamaz. Kaynaklar
``ServiceNowSimulator``, ``ZabbixSimulator`` ve ``VCenterSimulator`` (sentetik)."""
from __future__ import annotations

import dataclasses
import secrets
import uuid
from datetime import UTC, datetime

from conftest import REPO

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.bmc_discovery import BMCDiscoveryClient, BMCDiscoverySimulator
from evren_core.connectors.sources.defender import DefenderClient, DefenderSimulator
from evren_core.connectors.sources.servicenow import ServiceNowClient, ServiceNowSimulator
from evren_core.connectors.sources.vcenter import VCenterClient, VCenterSimulator
from evren_core.connectors.sources.zabbix import ZabbixClient, ZabbixSimulator
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 7, 0, tzinfo=UTC)
APP = "5b1c2d3e4f5061728394a5b6c7d8e9f0"  # örnek ServiceNow kaydı: ist-app-02
PW = secrets.token_hex(8)  # simülatörler parola denetlemez; sabit parola yazılmaz


class Capture(DryRunGateway):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list = []

    def send(self, run_id, records):
        self.sent.extend(records)
        return super().send(run_id, records)


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def records(name: str):
    if name == "servicenow":
        src = ServiceNowClient(HttpClient("https://sn.test", transport=ServiceNowSimulator.from_dir(
            REPO / "connectors/servicenow_cmdb/samples"), auth_header=HttpClient.basic("u", PW), retries=0))
        ds, folder = "ServiceNow", "servicenow_cmdb"
    elif name == "bmc":
        src = BMCDiscoveryClient(HttpClient("https://bmc.test", transport=BMCDiscoverySimulator.from_dir(
            REPO / "connectors/bmc_discovery/samples"), auth_header="Bearer b", retries=0))
        ds, folder = "BMC", "bmc_discovery"
    elif name == "defender":
        sim = DefenderSimulator.from_dir(REPO / "connectors/defender/samples")
        src = DefenderClient(HttpClient(DefenderSimulator.API, transport=sim, retries=0),
                             HttpClient("https://login.microsoftonline.com", transport=sim, retries=0),
                             tenant="11111111-2222-3333-4444-555555555555", client_id="a", client_secret=PW)
        ds, folder = "Defender", "defender"
    elif name == "zabbix":
        src = ZabbixClient(HttpClient("https://z.test", transport=ZabbixSimulator.from_dir(
            REPO / "connectors/zabbix/samples"), auth_header="Bearer z", retries=0))
        ds, folder = "Zabbix", "zabbix"
    else:
        src = VCenterClient(HttpClient("https://v.test", transport=VCenterSimulator.from_dir(
            REPO / "connectors/vcenter/samples"), retries=0), user="u", password=PW)
        ds, folder = "vCenter", "vcenter"
    gw, run_id = Capture(), uuid.uuid4()
    assert ConnectorRunner(src, load_mappings(REPO / "connectors" / folder, ds), gw, clock=lambda: NOW
                           ).run(run_id=run_id).completed
    return ds, run_id, gw.sent


def run(store: ReconcilerStore, name: str):
    ds, run_id, recs = records(name)
    store.start_run(run_id, ds, started_at=NOW)
    results = [store.apply(r) for r in recs]
    store.finish_run(run_id)
    return recs, results


def test_servicenow_records_keep_their_sys_id_through_the_pipeline(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    recs, results = run(store, "servicenow")
    assert all(r.ci_sys_id == rec.source_key for rec, r in zip(recs, results, strict=True))
    assert fetch(as_reconciler, """SELECT method FROM ingest.identity_link
                                   WHERE data_source = 'ServiceNow' AND source_key = %s AND active""", APP) == [
        ("import",)]
    assert fetch(as_reconciler, "SELECT name, sys_class_name, support_group FROM cmdb.cmdb_ci WHERE sys_id = %s",
                 APP) == [("ist-app-02", "cmdb_ci_linux_server", "a1b2c3d4e5f60718293a4b5c6d7e8f90")]
    again = run(store, "servicenow")[1]  # ikinci çalışma: bağ var, yeni CI açılmaz
    assert {r.outcome for r in again} == {"unchanged"}
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.cmdb_ci WHERE sys_id = %s", APP) == [(1,)]


def test_discovery_sources_attach_to_the_servicenow_ci(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    for name in ("servicenow", "zabbix", "vcenter", "bmc", "defender"):
        run(store, name)
    links = sorted(f"{ds}:{key}" for ds, key in fetch(as_reconciler, """
        SELECT data_source, source_key FROM ingest.identity_link WHERE ci_sys_id = %s AND active""", APP))
    assert links == ["BMC:b7c2e94a1d3f5a6b7c8d9e0f1a2b3c4d/Host", "Defender:3f9a2c41d07e5b6a8c9d0e1f2a3b4c5d6e7f8091",
                     f"ServiceNow:{APP}", "Zabbix:20502",
                     "vCenter:guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D7E"]  # beş kaynak, tek CI
    esx = fetch(as_reconciler, """SELECT l.data_source FROM ingest.identity_link l
                                  WHERE l.ci_sys_id = '7d3e4f5061728394a5b6c7d8e9f0a1b2' AND l.active""")
    assert sorted(x[0] for x in esx) == ["BMC", "ServiceNow", "vCenter"]  # ESXi seri numarasıyla SN CI'ına bağlandı
    # işletim sistemini Keşif rolü (BMC) yazar; ITSM'in eski değeri ezilir (uzlaştırma kuralı)
    assert fetch(as_reconciler, "SELECT class_attributes->>'os' FROM cmdb.cmdb_ci WHERE sys_id = %s", APP) == [
        ("Red Hat Enterprise Linux 9.4 (Plow)",)]
    # yönetişim alanı ITSM'den kalır, keşif kaynakları ezmez (kural 5)
    assert fetch(as_reconciler, "SELECT support_group FROM cmdb.cmdb_ci WHERE sys_id = %s", APP) == [
        ("a1b2c3d4e5f60718293a4b5c6d7e8f90",)]


def test_record_for_an_existing_ci_links_without_creating_a_new_one(as_reconciler, metadata):
    """O sys_id'li CI Evren'de zaten varsa (ör. sys_id'si önceden belirlenerek replikaya giden CI) ona bağlanır."""
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ds, run_id, recs = records("servicenow")
    rec = next(r for r in recs if r.source_key == APP)
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("INSERT INTO cmdb.cmdb_ci (sys_id, sys_class_name, name) VALUES (%s, 'cmdb_ci_linux_server', "
                          "'ist-app-02')", (APP,))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    store.start_run(run_id, ds, started_at=NOW)
    res = store.apply(rec)
    assert res.ci_sys_id == APP
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.cmdb_ci WHERE name = 'ist-app-02'") == [(1,)]


def test_replica_record_of_an_evren_born_ci_links_through_sn_ci_map(as_reconciler, metadata):
    """Evren'de doğan CI replikaya IRE ile gider ve ServiceNow yeni sys_id verir (sync.sn_ci_map). O kayıt ServiceNow
    çekmesiyle geri geldiğinde Evren CI'ına bağlanır; SN sys_id'siyle mükerrer CI açılmaz."""
    evren_ci = "e" * 31 + "1"
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ds, run_id, recs = records("servicenow")
    rec = next(r for r in recs if r.source_key == APP)
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("INSERT INTO cmdb.cmdb_ci (sys_id, sys_class_name, name) VALUES (%s, 'cmdb_ci_linux_server', "
                          "'ist-app-02')", (evren_ci,))
    as_reconciler.execute("INSERT INTO sync.sn_ci_map (evren_sys_id, sn_sys_id, sn_class, state) "
                          "VALUES (%s, %s, 'cmdb_ci_linux_server', 'in_sync')", (evren_ci, APP))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    store.start_run(run_id, ds, started_at=NOW)
    assert store.apply(rec).ci_sys_id == evren_ci
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.cmdb_ci WHERE sys_id = %s", APP) == [(0,)]
    assert fetch(as_reconciler, """SELECT ci_sys_id FROM ingest.identity_link
                                   WHERE data_source = 'ServiceNow' AND source_key = %s AND active""", APP) == [
        (evren_ci,)]


def test_shadow_servicenow_does_not_create_cis(as_reconciler, metadata):
    md = dataclasses.replace(metadata, data_sources=[{**d, "lifecycle": "shadow"} if d["name"] == "ServiceNow" else d
                                                     for d in metadata.data_sources])
    store = ReconcilerStore(as_reconciler, md, clock=lambda: NOW)
    _recs, results = run(store, "servicenow")
    assert not any(r.ci_sys_id for r in results)
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.cmdb_ci WHERE sys_id = %s", APP) == [(0,)]
