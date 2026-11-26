"""ServiceNow bağlayıcısı → gerçek uzlaştırıcı (U5.2, ADR-0004/0020): sys_id korunur, keşif kaynakları SN CI'ına bağlanır.

Olay omurgasından gelen ServiceNow kaydı (``apply``) bağı yoksa kimlik çözümlemesine girmez: ``sync.sn_ci_map``'te
eşlemesi varsa Evren CI'ına bağlanır, yoksa CI aynı sys_id ile açılır veya o sys_id'li CI'a bağlanır. Gölge kaynak CI
açamaz. Kaynaklar
``ServiceNowSimulator``, ``ZabbixSimulator`` ve ``VCenterSimulator`` (sentetik)."""
from __future__ import annotations

import dataclasses
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from conftest import REPO

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.bmc_discovery import BMCDiscoveryClient, BMCDiscoverySimulator
from evren_core.connectors.sources.defender import DefenderClient, DefenderSimulator
from evren_core.connectors.sources.nnmi import NNMiClient, NNMiSimulator
from evren_core.connectors.sources.servicenow import ServiceNowClient, ServiceNowSimulator
from evren_core.connectors.sources.vcenter import VCenterClient, VCenterSimulator
from evren_core.connectors.sources.zabbix import ZabbixClient, ZabbixSimulator
from evren_core.reference import REFERENCE_TABLES
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
    elif name == "nnmi":
        src = NNMiClient(HttpClient("https://nnmi.test", transport=NNMiSimulator.from_dir(
            REPO / "connectors/nnmi/samples"), auth_header=HttpClient.basic("u", PW), retries=0))
        ds, folder = "NNMi", "nnmi"
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
    assert all(r.ci_sys_id == rec.source_key for rec, r in zip(recs, results, strict=True)
               if rec.class_hint not in REFERENCE_TABLES)  # referans kayıtları CI değildir (ADR-0022)
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
    for name in ("servicenow", "zabbix", "vcenter", "bmc", "defender", "nnmi"):
        run(store, name)
    links = sorted(f"{ds}:{key}" for ds, key in fetch(as_reconciler, """
        SELECT data_source, source_key FROM ingest.identity_link WHERE ci_sys_id = %s AND active""", APP))
    assert links == ["BMC:b7c2e94a1d3f5a6b7c8d9e0f1a2b3c4d/Host", "Defender:3f9a2c41d07e5b6a8c9d0e1f2a3b4c5d6e7f8091",
                     f"ServiceNow:{APP}", "Zabbix:20502",
                     "vCenter:guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D7E"]  # beş kaynak, tek CI
    esx = fetch(as_reconciler, """SELECT l.data_source FROM ingest.identity_link l
                                  WHERE l.ci_sys_id = '7d3e4f5061728394a5b6c7d8e9f0a1b2' AND l.active""")
    assert sorted(x[0] for x in esx) == ["BMC", "ServiceNow", "vCenter"]  # ESXi seri numarasıyla SN CI'ına bağlandı
    switch = fetch(as_reconciler, """SELECT l.data_source FROM ingest.identity_link l
                                     WHERE l.ci_sys_id = '9f5061728394a5b6c7d8e9f0a1b2c3d4' AND l.active""")
    assert sorted(x[0] for x in switch) == ["NNMi", "ServiceNow", "Zabbix"]  # ağ cihazı seri numarasıyla tek CI
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


GROUP = "a1b2c3d4e5f60718293a4b5c6d7e8f90"  # örnek ServiceNow grubu: Linux Sistem Ekibi (CI'ların destek grubu)
GONE = "b2c3d4e5f60718293a4b5c6d7e8f90a3"   # örnek kullanıcı: active=false
MODEL = "2b3c4d5e6f708192a3b4c5d6e7f8019a"


def test_reference_tables_are_loaded_with_same_sys_id_and_skip_identity(as_reconciler, metadata):
    """ADR-0022: referans kayıtları ref.<tablo>'ya aynı sys_id ile yazılır; CI açılmaz, kimlik bağı kurulmaz."""
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    recs, results = run(store, "servicenow")
    refs = {rec.source_key: res.outcome for rec, res in zip(recs, results, strict=True)
            if rec.class_hint in REFERENCE_TABLES}
    assert len(refs) == 12 and set(refs.values()) == {"reference"}
    assert fetch(as_reconciler, "SELECT name, manager, active, source FROM ref.sys_user_group WHERE sys_id = %s",
                 GROUP) == [("Linux Sistem Ekibi (SENTETİK)", "b2c3d4e5f60718293a4b5c6d7e8f90a2", True, "servicenow")]
    assert fetch(as_reconciler, "SELECT user_name, email, active FROM ref.sys_user WHERE sys_id = %s", GONE) == [
        ("ornek.ayrilan", None, False)]
    assert fetch(as_reconciler, "SELECT name, manufacturer FROM ref.cmdb_model WHERE sys_id = %s", MODEL) == [
        ("Örnek Sunucu R760 (SENTETİK)", "3c4d5e6f708192a3b4c5d6e7f8019a2b")]
    assert fetch(as_reconciler, "SELECT model_id FROM cmdb.cmdb_ci WHERE sys_id = '7d3e4f5061728394a5b6c7d8e9f0a1b2'") \
        == [(MODEL,)]
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s)", list(refs)) == [(0,)]
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_link WHERE source_key = ANY(%s)", list(refs)) == \
        [(0,)]
    # çalışma sayaçları referans kayıtlarını da sayar (geçit kapanışı bildirilen sayıyla eşleşir)
    run_id = recs[0].run_id
    assert fetch(as_reconciler, "SELECT record_count FROM ingest.source_run WHERE run_id = %s", run_id) == [(17,)]
    again = run(store, "servicenow")[1]
    assert {r.outcome for r in again} == {"unchanged"}


def test_reference_does_not_overwrite_evren_rows_and_deleted_record_deactivates(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("INSERT INTO ref.sys_user_group (sys_id, name, source) VALUES (%s, 'Evren Grubu', 'evren')",
                          (GROUP,))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    recs, results = run(store, "servicenow")
    out = {rec.source_key: res.outcome for rec, res in zip(recs, results, strict=True)}
    assert out[GROUP] == "reference_protected"
    assert fetch(as_reconciler, "SELECT name, source FROM ref.sys_user_group WHERE sys_id = %s", GROUP) == [
        ("Evren Grubu", "evren")]
    ds, run_id, recs2 = records("servicenow")
    cc = next(r for r in recs2 if r.class_hint == "cmn_cost_center")
    store.start_run(run_id, ds, started_at=NOW)
    assert store.apply(cc.model_copy(update={"deleted": True})).outcome == "reference_deactivated"
    assert fetch(as_reconciler, "SELECT active FROM ref.cmn_cost_center WHERE sys_id = %s", cc.source_key) == [(False,)]
    loc = next(r for r in recs2 if r.class_hint == "cmn_location")  # etkinlik sütunu yok: satır korunur
    assert store.apply(loc.model_copy(update={"deleted": True})).outcome == "reference_kept"


def test_invalid_conflicting_or_shadow_reference_is_not_written(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ds, run_id, recs = records("servicenow")
    store.start_run(run_id, ds, started_at=NOW)
    group = next(r for r in recs if r.source_key == GROUP)
    bad = group.model_copy(update={"source_key": "c" * 32, "attributes": {**group.attributes, "manager": "x' OR 1=1"}})
    assert store.apply(bad).outcome == "reference_invalid"
    assert fetch(as_reconciler, "SELECT count(*) FROM ref.sys_user_group WHERE sys_id = %s", "c" * 32) == [(0,)]
    user = next(r for r in recs if r.class_hint == "sys_user")
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("INSERT INTO ref.sys_user (sys_id, user_name, source) VALUES (%s, %s, 'entra')",
                          ("d" * 32, user.attributes["user_name"]))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    assert store.apply(user).outcome == "reference_conflict"  # aynı kullanıcı adı Entra'dan var
    md = dataclasses.replace(metadata, data_sources=[{**d, "lifecycle": "shadow"} if d["name"] == "ServiceNow" else d
                                                     for d in metadata.data_sources])
    shadow = ReconcilerStore(as_reconciler, md, clock=lambda: NOW)
    model = next(r for r in recs if r.class_hint == "cmdb_model")
    assert shadow.apply(model).outcome == "reference_skipped"
    assert fetch(as_reconciler, "SELECT count(*) FROM ref.cmdb_model WHERE sys_id = %s", model.source_key) == [(0,)]


def test_defender_posture_goes_only_to_restricted_table(as_reconciler, metadata):
    """ADR-0023: duruş yalnızca cmdb.ci_security_posture'a; kaynak kaydında, CI'da ve değişim geçmişinde yok."""
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    run(store, "servicenow")
    recs, _ = run(store, "defender")
    assert fetch(as_reconciler, """SELECT data_source, exposure_level, risk_score, health_status, protection_status
                                     FROM cmdb.ci_security_posture WHERE ci_sys_id = %s""", APP) == [
        ("Defender", "Medium", "Low", "Active", "Onboarded")]
    as_reconciler.execute("RESET ROLE")  # denetim izini uzlaştırıcı okuyamaz (yalnızca ekler)
    leaks = fetch(as_reconciler, """SELECT (SELECT string_agg(attributes::text || lookup::text || external_ids::text, '')
                                              FROM ingest.source_record WHERE data_source = 'Defender'),
                                           (SELECT to_jsonb(ci)::text FROM cmdb.cmdb_ci ci WHERE sys_id = %s),
                                           (SELECT coalesce(string_agg(to_jsonb(c)::text, ''), '')
                                              FROM audit.ci_change c WHERE ci_sys_id = %s)""", APP, APP)[0]
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    for text in leaks:
        assert "exposure" not in text and "risk_score" not in text and "riskScore" not in text
    # değişmeyen kayıt sonraki çalışmada duruşu tazeler (kuyruk kararından sonra ilk çalışmada da yazılır)
    later, run_id = NOW + timedelta(hours=6), uuid.uuid4()
    store.start_run(run_id, "Defender", started_at=later)
    app = next(r for r in recs if r.posture and r.attributes.get("name") == "ist-app-02")
    assert store.apply(app.model_copy(update={"run_id": run_id, "observed_at": later})).outcome == "unchanged"
    assert fetch(as_reconciler, "SELECT observed_at FROM cmdb.ci_security_posture WHERE ci_sys_id = %s", APP) == [
        (later,)]


def test_posture_is_written_only_by_the_posture_role_and_by_rank(as_reconciler, metadata):
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("""INSERT INTO ingest.data_source (name, label_tr, kind, role, role_rank, lifecycle)
                             VALUES ('EDR2', 'Yeni EDR (test)', 'security', 'EndpointSecurity', 2, 'retiring')""")
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    edr2 = {"name": "EDR2", "label_tr": "Yeni EDR (test)", "role": "EndpointSecurity", "role_rank": 2,
            "lifecycle": "retiring", "connector": "defender", "mappings": []}
    md = dataclasses.replace(metadata, data_sources=[*metadata.data_sources, edr2])
    store = ReconcilerStore(as_reconciler, md, clock=lambda: NOW)
    run(store, "servicenow")
    recs = records("defender")[2]
    run(store, "defender")
    app = next(r for r in recs if r.posture and r.attributes.get("name") == "ist-app-02")
    rid = uuid.uuid4()
    store.start_run(rid, "EDR2", started_at=NOW)
    forged = app.model_copy(update={"data_source": "EDR2", "run_id": rid, "source_key": "edr2-app",
                                    "posture": app.posture.model_copy(update={"exposure_level": "High"})})
    assert store.apply(forged).ci_sys_id == APP
    assert fetch(as_reconciler, "SELECT data_source, exposure_level FROM cmdb.ci_security_posture WHERE ci_sys_id = %s",
                 APP) == [("Defender", "Medium")]  # rol içi sırası küçük olan kaynak kazanır
    # duruş kaynağı rolünde olmayan kaynak (izleme) duruş gönderse de yazılmaz
    ds, zrun, zrecs = records("zabbix")
    store.start_run(zrun, ds, started_at=NOW)
    zbx = next(r for r in zrecs if r.attributes.get("name") == "ank-db-14")
    res = store.apply(zbx.model_copy(update={"posture": app.posture}))
    assert res.ci_sys_id and fetch(as_reconciler, "SELECT count(*) FROM cmdb.ci_security_posture WHERE ci_sys_id = %s",
                                   res.ci_sys_id) == [(0,)]


def test_shadow_endpoint_source_writes_no_posture(as_reconciler, metadata):
    md = dataclasses.replace(metadata, data_sources=[{**d, "lifecycle": "shadow"} if d["name"] == "Defender" else d
                                                     for d in metadata.data_sources])
    store = ReconcilerStore(as_reconciler, md, clock=lambda: NOW)
    run(store, "servicenow")
    run(store, "defender")
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.ci_security_posture") == [(0,)]
