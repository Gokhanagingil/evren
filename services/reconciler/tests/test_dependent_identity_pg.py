"""Bağımlı CI kimliği (U7.2, F1-07, ADR-0029) — gerçek uzlaştırıcı ve PostgreSQL, en az yetkili rol.

Ağ adaptörü bağlı olduğu CI içinde MAC ile, uygulama üzerinde çalıştığı sunucu içinde adla tekildir: aynı MAC iki
cihazda iki ayrı adaptördür; iki kaynak aynı sunucunun aynı adaptörünü bildirirse tek CI olur. Ana kaydı çalışmada sonra
gelen bağımlı kayıt çalışma sonunda çözülür. Anahtarlar küresel kimlik dizinine girmez. Veriler sentetiktir.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from evren_core.envelope import IngestRecord, ParentRef
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def rec(ds, key, run, cls, *, parent=None, parent_ds=None, **attrs):
    return IngestRecord(data_source=ds, run_id=run, source_key=key, class_hint=cls, attributes=attrs, observed_at=NOW,
                        parent=ParentRef(source_key=parent, data_source=parent_ds) if parent else None)


def nic(ds, key, run, parent, mac, **kw):
    return rec(ds, key, run, "cmdb_ci_network_adapter", parent=parent, mac_address=mac, name=key.split("/")[-1], **kw)


def test_network_adapters_are_unique_per_device_and_parent_may_arrive_later(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6].upper()
    run = store.start_run(uuid.uuid4(), "NNMi", started_at=NOW)
    shared = "00:1A:2B:3C:4D:5E"  # iki cihazda aynı MAC (ör. HSRP sanal MAC'i): iki ayrı adaptör
    # Arayüz, ana düğümden ÖNCE gelir: bekletilir
    early = store.apply(nic("NNMi", f"sw-{tag}-1/Gi0/1", run, f"sw-{tag}-1", shared))
    assert (early.outcome, early.ci_sys_id) == ("deferred", None)
    sw1 = store.apply(rec("NNMi", f"sw-{tag}-1", run, "cmdb_ci_ip_switch", name=f"sw-{tag}-1",
                          serial_number=f"SW{tag}1")).ci_sys_id
    sw2 = store.apply(rec("NNMi", f"sw-{tag}-2", run, "cmdb_ci_ip_switch", name=f"sw-{tag}-2",
                          serial_number=f"SW{tag}2")).ci_sys_id
    other = store.apply(nic("NNMi", f"sw-{tag}-2/Gi0/1", run, f"sw-{tag}-2", shared.lower().replace(":", "-")))
    assert other.outcome == "new" and other.ci_sys_id
    store.finish_run(run)
    (nic1,), = fetch(as_reconciler, "SELECT ci_sys_id FROM ingest.identity_link WHERE data_source = 'NNMi' "
                                    "AND source_key = %s AND active", f"sw-{tag}-1/Gi0/1")
    assert nic1 != other.ci_sys_id  # aynı MAC, farklı cihaz → farklı adaptör
    rows = fetch(as_reconciler, "SELECT sys_id, sys_class_name, class_attributes->>'cmdb_ci', mac_address FROM cmdb.cmdb_ci "
                                "WHERE sys_id = ANY(%s::bpchar[]) ORDER BY class_attributes->>'cmdb_ci'",
                 [nic1, other.ci_sys_id])
    # Golden record kaynağın bildirdiği biçimi tutar; kimlik anahtarı biçimden bağımsızdır
    assert {(r[2], r[1], r[3].lower().replace("-", ":")) for r in rows} == {
        (sw1, "cmdb_ci_network_adapter", "00:1a:2b:3c:4d:5e"), (sw2, "cmdb_ci_network_adapter", "00:1a:2b:3c:4d:5e")}
    # Bağımlı anahtar küresel dizine girmez; ana CI bağlamında saklanır
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_key WHERE ci_sys_id = ANY(%s::bpchar[])",
                 [nic1, other.ci_sys_id])[0][0] == 0
    assert {r[0] for r in fetch(as_reconciler, "SELECT parent_ci_sys_id FROM ingest.dependent_key "
                                               "WHERE ci_sys_id = ANY(%s::bpchar[])", [nic1, other.ci_sys_id])} == {sw1, sw2}
    # İkinci çalışma: aynı adaptörler, yeni CI yok
    run2 = store.start_run(uuid.uuid4(), "NNMi", started_at=NOW)
    store.apply(rec("NNMi", f"sw-{tag}-1", run2, "cmdb_ci_ip_switch", name=f"sw-{tag}-1", serial_number=f"SW{tag}1"))
    again = store.apply(nic("NNMi", f"sw-{tag}-1/Gi0/1", run2, f"sw-{tag}-1", shared))
    assert again.ci_sys_id == nic1


def test_two_sources_report_the_same_adapter_of_the_same_server(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6].upper()
    b = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    z = store.start_run(uuid.uuid4(), "Zabbix", started_at=NOW)
    host = store.apply(rec("BMC", f"h-{tag}", b, "cmdb_ci_linux_server", name=f"srv-{tag}", serial_number=f"CZ{tag}"))
    same = store.apply(rec("Zabbix", f"z-{tag}", z, "cmdb_ci_linux_server", name=f"srv-{tag}", serial_number=f"CZ{tag}"))
    assert host.ci_sys_id == same.ci_sys_id  # sunucu seriyle tek CI
    a = store.apply(nic("BMC", f"h-{tag}/eth0", b, f"h-{tag}", "00:50:56:aa:00:01"))
    c = store.apply(nic("Zabbix", f"z-{tag}/ens192", z, f"z-{tag}", "00:50:56:AA:00:01"))
    assert a.outcome == "new" and c.outcome == "rule_match" and a.ci_sys_id == c.ci_sys_id
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_link WHERE ci_sys_id = %s AND active",
                 a.ci_sys_id)[0][0] == 2


def test_application_runs_on_its_server_and_relation_direction_follows_rules(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6].upper()
    run = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    s1 = store.apply(rec("BMC", f"a-{tag}", run, "cmdb_ci_linux_server", name=f"app-{tag}-1",
                         serial_number=f"AP{tag}1")).ci_sys_id
    s2 = store.apply(rec("BMC", f"b-{tag}", run, "cmdb_ci_linux_server", name=f"app-{tag}-2",
                         serial_number=f"AP{tag}2")).ci_sys_id
    n1 = store.apply(rec("BMC", f"a-{tag}/nginx", run, "cmdb_ci_appl", parent=f"a-{tag}", name="nginx", version="1.24"))
    n2 = store.apply(rec("BMC", f"b-{tag}/nginx", run, "cmdb_ci_appl", parent=f"b-{tag}", name="NGINX"))
    assert n1.ci_sys_id != n2.ci_sys_id  # aynı ad, farklı sunucu
    store.finish_run(run)
    rels = fetch(as_reconciler, """SELECT r.parent, t.name, r.child FROM cmdb.cmdb_rel_ci r
                                     JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                                    WHERE r.parent = ANY(%s::bpchar[])""", [n1.ci_sys_id, n2.ci_sys_id])
    assert sorted(rels) == sorted([(n1.ci_sys_id, "Runs on::Runs", s1), (n2.ci_sys_id, "Runs on::Runs", s2)])


def test_incomplete_dependent_records_are_not_guessed(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6].upper()
    run = store.start_run(uuid.uuid4(), "NNMi", started_at=NOW)
    store.apply(rec("NNMi", f"r-{tag}", run, "cmdb_ci_ip_router", name=f"r-{tag}", serial_number=f"RT{tag}"))
    no_parent = store.apply(rec("NNMi", f"r-{tag}/x", run, "cmdb_ci_network_adapter", mac_address="00:11:22:33:44:55"))
    no_mac = store.apply(rec("NNMi", f"r-{tag}/lo0", run, "cmdb_ci_network_adapter", parent=f"r-{tag}", name="lo0"))
    orphan = store.apply(nic("NNMi", f"yok-{tag}/Gi0/2", run, f"yok-{tag}", "00:11:22:33:44:66"))
    assert (no_parent.outcome, no_mac.outcome, orphan.outcome) == ("insufficient", "insufficient", "deferred")
    assert "ana kaydını bildirmedi" in no_parent.summary and "mac_address" in no_mac.summary
    counts = store.resolve_deferred_dependents(run)
    assert counts == {"resolved": 0, "orphaned": 1}
    store.finish_run(run)
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.identity_link WHERE source_key = %s AND active",
                 f"yok-{tag}/Gi0/2")[0][0] == 0  # ana kaydı olmayan adaptör tahminle açılmaz
    assert fetch(as_reconciler, "SELECT kind FROM ingest.match_candidate WHERE source_key = %s",
                 f"r-{tag}/x") == [("insufficient",)]
