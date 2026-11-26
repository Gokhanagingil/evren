"""vCenter bağlayıcısının kayıtları → gerçek uzlaştırıcı (U5.2, ADR-0020): ESXi host'ları, VM örnekleri, misafir
sunucular ve aralarındaki ilişkiler (nitelikli anahtarla). Kaynak: ``VCenterSimulator`` (sentetik envanter)."""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from conftest import REPO

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.vcenter import VCenterClient, VCenterSimulator
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 6, 0, tzinfo=UTC)
SIM_PASSWORD = secrets.token_hex(8)  # simülatör parola denetlemez; sabit parola yazılmaz


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


def test_vcenter_records_reconcile_into_hosts_vms_and_relations(as_reconciler, metadata):
    sim = VCenterSimulator.from_dir(REPO / "connectors/vcenter/samples")
    client = VCenterClient(HttpClient("https://vcenter.test", transport=sim, retries=0), user="u",
                           password=SIM_PASSWORD, page_size=2)
    gw = Capture()
    run_id = uuid.uuid4()
    report = ConnectorRunner(client, load_mappings(REPO / "connectors/vcenter", "vCenter"), gw,
                             clock=lambda: NOW).run(run_id=run_id)
    assert report.completed and len(gw.sent) == 10

    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    store.start_run(run_id, "vCenter", started_at=NOW)
    outcomes = [store.apply(r).outcome for r in gw.sent]
    assert "review" not in outcomes and "insufficient" not in outcomes, outcomes
    store.finish_run(run_id)

    classes = dict(fetch(as_reconciler, """
        SELECT c.sys_class_name, count(*) FROM ingest.identity_link l JOIN cmdb.cmdb_ci c ON c.sys_id = l.ci_sys_id
         WHERE l.data_source = 'vCenter' AND l.active AND l.source_key = ANY(%s) GROUP BY 1""",
        [r.source_key for r in gw.sent]))
    assert classes == {"cmdb_ci_esx_server": 3, "cmdb_ci_vmware_instance": 4, "cmdb_ci_linux_server": 2,
                       "cmdb_ci_win_server": 1}
    rels = fetch(as_reconciler, """
        SELECT p.name, t.name, ch.name FROM cmdb.cmdb_rel_ci r
          JOIN cmdb.cmdb_ci p ON p.sys_id = r.parent JOIN cmdb.cmdb_ci ch ON ch.sys_id = r.child
          JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
         WHERE r.discovery_source = 'vCenter' AND p.name IN ('ist-app-02', 'ist-app-02 (Uygulama)')
         ORDER BY 1, 2""")
    assert rels == [("ist-app-02", "Instantiates::Instantiated by", "ist-app-02 (Uygulama)"),
                    ("ist-app-02", "Virtualized by::Virtualizes", "esx01"),
                    ("ist-app-02 (Uygulama)", "Registered on::Has registered", "esx01")]
    assert not fetch(as_reconciler, "SELECT 1 FROM cmdb.cmdb_ci WHERE name = 'sablon-rhel9'")  # şablon CI değil


def test_zabbix_and_vcenter_samples_merge_the_same_server(as_reconciler, metadata):
    """Akıl katmanı: iki bağlayıcının örnekleri aynı sunucuyu tek CI'da birleştirir (seri numarası ve MAC)."""
    from evren_core.connectors.sources.zabbix import ZabbixClient, ZabbixSimulator

    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    sources = [
        ("Zabbix", ZabbixClient(HttpClient("https://z.test", transport=ZabbixSimulator.from_dir(
            REPO / "connectors/zabbix/samples"), auth_header="Bearer z", retries=0)), "zabbix"),
        ("vCenter", VCenterClient(HttpClient("https://v.test", transport=VCenterSimulator.from_dir(
            REPO / "connectors/vcenter/samples"), retries=0), user="u", password=SIM_PASSWORD), "vcenter"),
    ]
    for ds, src, folder in sources:
        gw, run_id = Capture(), uuid.uuid4()
        assert ConnectorRunner(src, load_mappings(REPO / "connectors" / folder, ds), gw,
                               clock=lambda: NOW).run(run_id=run_id).completed
        store.start_run(run_id, ds, started_at=NOW)
        store.apply_all(gw.sent)
        store.finish_run(run_id)
    merged = fetch(as_reconciler, """
        SELECT l.ci_sys_id, array_agg(l.data_source || ':' || l.source_key ORDER BY l.data_source)
          FROM ingest.identity_link l
         WHERE l.active AND (l.data_source, l.source_key) IN (('Zabbix', '20502'),
               ('vCenter', 'guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D7E'), ('Zabbix', '20504'),
               ('vCenter', 'guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D80'))
         GROUP BY 1 ORDER BY 2""")
    assert [links for _ci, links in merged] == [
        ["Zabbix:20502", "vCenter:guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D7E"],
        ["Zabbix:20504", "vCenter:guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D80"]]
