"""Uzlaştırıcı deposu: gerçek PostgreSQL, en az yetkili rol, uçtan uca davranış."""
from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from evren_core.envelope import IngestRecord
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)


def rec(ds: str, key: str, run: uuid.UUID, cls: str = "cmdb_ci_linux_server", observed: datetime = NOW, **attrs):
    lookup = attrs.pop("_lookup", {})
    return IngestRecord(data_source=ds, run_id=run, source_key=key, class_hint=cls, attributes=attrs,
                        lookup=lookup, observed_at=observed)


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_audit(conn, sql, *params):
    """Denetim izi salt ekleme: uzlaştırıcı yazar ama okuyamaz; denetçi rolüyle okunur ve role geri dönülür."""
    conn.execute("SET ROLE evren_auditor_ro")
    try:
        return fetch(conn, sql, *params)
    finally:
        conn.execute("SET ROLE evren_app_reconciler")


def test_demo_estate_matches_in_memory_pipeline(as_reconciler):
    """Aynı sentetik girdi: bellek içi hat (demo.json) ile PostgreSQL uzlaştırıcısı aynı golden record'u üretir."""
    import build_demo
    import load_demo_pg

    summary = load_demo_pg.load(as_reconciler, role=None)  # rol fikstürle zaten evren_app_reconciler
    demo = build_demo.build()
    assert summary["nnmi_partial"] == "open"

    by_class = dict(fetch(as_reconciler, "SELECT sys_class_name, count(*) FROM cmdb.cmdb_ci GROUP BY 1"))
    expected: dict[str, int] = {}
    for c in demo["cis"]:
        expected[c["class"]] = expected.get(c["class"], 0) + 1
    assert by_class == expected

    kinds = dict(fetch(as_reconciler, "SELECT kind, count(*) FROM ingest.match_candidate WHERE status = 'open' GROUP BY 1"))
    assert kinds == {"review": demo["summary"]["queue_review"], "insufficient": demo["summary"]["queue_insufficient"]}

    # ServiceNow'dan içe aktarılan CI'lar aynı sys_id'yi taşır: öznitelik değerleri birebir karşılaştırılır
    compared = 0
    for c in demo["cis"]:
        row = fetch(as_reconciler, "SELECT to_jsonb(ci) FROM cmdb.cmdb_ci ci WHERE sys_id = %s", c["sys_id"])
        if not row:
            continue  # yeni CI'ların sys_id'si iki uygulamada farklı üretilir
        doc = row[0][0]
        for a in c["attributes"]:
            got = doc.get(a["attribute"], doc["class_attributes"].get(a["attribute"]))
            if got is None:
                continue  # sözlükte bu sınıf için alan yok (ör. sunucuda bios_uuid): uzlaştırma durumunda raporlanır
            assert got == a["value"], (c["name"], a["attribute"])
            compared += 1
    assert compared > 300


def test_unchanged_record_only_touches_last_seen(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    run = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    r = rec("BMC", "k1", run, name="srv-a", serial_number="CZJ0000001", os="RHEL 9")
    first = store.apply(r)
    assert first.outcome == "new" and first.changes
    audit_before = fetch_audit(as_reconciler, "SELECT count(*) FROM audit.ci_change WHERE ci_sys_id = %s",
                               first.ci_sys_id)
    again = store.apply(r.model_copy(update={"observed_at": NOW + timedelta(hours=1)}))
    assert again.outcome == "unchanged" and again.ci_sys_id == first.ci_sys_id
    assert fetch_audit(as_reconciler, "SELECT count(*) FROM audit.ci_change WHERE ci_sys_id = %s",
                       first.ci_sys_id) == audit_before
    assert fetch(as_reconciler, "SELECT last_seen FROM ingest.source_record WHERE data_source = 'BMC' AND source_key = 'k1'"
                 )[0][0] == NOW + timedelta(hours=1)


def test_change_writes_audit_outbox_and_provenance(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    run = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    ci = store.apply(rec("BMC", "k2", run, name="srv-b", serial_number="CZJ0000002", os="RHEL 9.3")).ci_sys_id
    res = store.apply(rec("BMC", "k2", run, name="srv-b", serial_number="CZJ0000002", os="RHEL 9.4"))
    assert [(c["attribute"], c["old"], c["new"]) for c in res.changes] == [("os", "RHEL 9.3", "RHEL 9.4")]
    assert fetch_audit(as_reconciler, "SELECT new_value, data_source, reason FROM audit.ci_change WHERE ci_sys_id = %s "
                 "AND attribute = 'os' ORDER BY id DESC LIMIT 1", ci)[0] == ("RHEL 9.4", "BMC", "reconciliation")
    winners = fetch(as_reconciler, "SELECT winners FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s", ci)[0][0]
    assert winners["os"]["source"] == "BMC" and winners["os"]["role"] == "Discovery"
    events = fetch(as_reconciler, "SELECT event_type, payload FROM events.outbox WHERE aggregate_id = %s ORDER BY id", ci)
    assert events[0][0] == "cmdb.ci.created.v1" and events[-1][0] == "cmdb.ci.changed.v1"
    assert events[-1][1]["changes"] == [{"attribute": "os", "old": "RHEL 9.3", "new": "RHEL 9.4", "source": "BMC"}]


def test_monitoring_tool_cannot_write_governance_field(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    run = store.start_run(uuid.uuid4(), "Zabbix", started_at=NOW)
    res = store.apply(rec("Zabbix", "z1", run, name="srv-c", asset_tag="DMB-1",
                          _lookup={"mac_addresses": ["00:50:56:aa:bb:cc"]}))
    assert fetch(as_reconciler, "SELECT asset_tag, name FROM cmdb.cmdb_ci WHERE sys_id = %s", res.ci_sys_id)[0] == (None, "srv-c")
    conflicts = fetch(as_reconciler, "SELECT conflicts FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s",
                      res.ci_sys_id)[0][0]
    assert conflicts["asset_tag"] == [["Zabbix", "DMB-1"]]


def test_shadow_product_compares_but_never_writes_or_creates(conn, metadata):
    """ADR-0009: yeni keşif ürünü gölge modda; mevcut CI'a bağlanır ama değer yazmaz, eşleşmeyen kayıt CI açmaz."""
    conn.execute("""INSERT INTO ingest.data_source (name, label_tr, kind, role, role_rank, lifecycle, connector)
                    VALUES ('YeniKesif', 'Yeni keşif ürünü (test)', 'discovery', 'Discovery', 2, 'shadow', 'yeni')""")
    md = copy.deepcopy(metadata)
    md.data_sources.append({"name": "YeniKesif", "label_tr": "Yeni keşif ürünü (test)", "role": "Discovery",
                            "role_rank": 2, "lifecycle": "shadow", "connector": "yeni", "mappings": []})
    conn.execute("SET ROLE evren_app_reconciler")
    store = ReconcilerStore(conn, md, clock=lambda: NOW)
    bmc = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    ci = store.apply(rec("BMC", "b1", bmc, name="srv-d", serial_number="CZJ0000004", os="RHEL 9.4")).ci_sys_id
    shadow = store.start_run(uuid.uuid4(), "YeniKesif", started_at=NOW)
    matched = store.apply(rec("YeniKesif", "y1", shadow, name="srv-d", serial_number="CZJ0000004", os="RHEL 9.5"))
    assert matched.ci_sys_id == ci
    assert fetch(conn, "SELECT class_attributes->>'os' FROM cmdb.cmdb_ci WHERE sys_id = %s", ci)[0][0] == "RHEL 9.4"  # BMC kazanır
    winners = fetch(conn, "SELECT winners FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s", ci)[0][0]
    assert winners["os"]["shadow"] == [["YeniKesif", "RHEL 9.5"]]
    unmatched = store.apply(rec("YeniKesif", "y2", shadow, name="srv-yeni", serial_number="CZJ0000099"))
    assert unmatched.outcome == "unmatched_shadow" and unmatched.ci_sys_id is None
    assert fetch(conn, "SELECT kind FROM ingest.match_candidate WHERE source_key = 'y2'")[0][0] == "unmatched_shadow"
    conn.execute("RESET ROLE")


def test_partial_run_never_marks_missing_but_complete_run_does(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    first = store.start_run(uuid.uuid4(), "Zabbix", started_at=NOW - timedelta(days=1))
    for i in range(3):
        store.apply(rec("Zabbix", f"p{i}", first, name=f"srv-p{i}", _lookup={"mac_addresses": [f"00:50:56:00:00:0{i}"]}))
    store.finish_run(first)
    partial = store.start_run(uuid.uuid4(), "Zabbix", started_at=NOW)
    store.apply(rec("Zabbix", "p0", partial, name="srv-p0", _lookup={"mac_addresses": ["00:50:56:00:00:00"]}))
    verdict = store.finish_run(partial, completed=False)
    assert verdict.state.value == "open"
    assert fetch(as_reconciler, "SELECT count(*) FROM ingest.source_record WHERE data_source = 'Zabbix' "
                 "AND source_key LIKE 'p%%' AND status = 'missing'")[0][0] == 0
    assert fetch(as_reconciler, "SELECT kind FROM insight.anomaly WHERE data_source = 'Zabbix' ORDER BY detected_at DESC "
                 "LIMIT 1")[0][0] == "source_volume"


def test_import_keeps_servicenow_sys_id_and_review_goes_to_queue(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    sn = store.start_run(uuid.uuid4(), "ServiceNow", started_at=NOW)
    a = "a" * 31 + "1"
    b = "a" * 31 + "2"
    store.import_record(rec("ServiceNow", a, sn, name="dup-01", serial_number="CZJ00000A1"), a)
    store.import_record(rec("ServiceNow", b, sn, name="dup-01", serial_number="CZJ00000B2"), b)
    assert {r[0] for r in fetch(as_reconciler, "SELECT sys_id FROM cmdb.cmdb_ci WHERE name = 'dup-01'")} == {a, b}
    zb = store.start_run(uuid.uuid4(), "Zabbix", started_at=NOW)
    res = store.apply(rec("Zabbix", "zz", zb, name="DUP_01"))
    assert res.outcome == "review"
    cands = fetch(as_reconciler, "SELECT candidates, kind FROM ingest.match_candidate WHERE source_key = 'zz' AND status = 'open'")
    assert {c["sys_id"] for c in cands[0][0]} == {a, b} and cands[0][1] == "review"


@pytest.mark.parametrize("statement", [
    "DELETE FROM ingest.identity_key",
    "DELETE FROM cmdb.cmdb_ci",
    "SELECT count(*) FROM audit.ci_change",
    "UPDATE audit.ci_change SET actor = 'x'",
])
def test_reconciler_least_privilege(as_reconciler, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_reconciler.transaction():
        as_reconciler.execute(statement)
