"""Birleştirmeyi ayırma (F0-12, göç 0007): istek API rolüyle yazılır, uzlaştırıcı uygular."""
from __future__ import annotations

import uuid

import psycopg
import pytest
from test_store_pg import NOW, fetch, fetch_audit, rec

from evren_reconciler import ReconcilerStore

MAC = "00:50:56:aa:00:10"


def _wrong_merge(store: ReconcilerStore) -> tuple[str, dict[str, str]]:
    """Keşif kaydı ve aynı seriyi bildiren sanallaştırma kaydı aynı CI'a bağlanır (yanlış birleştirme senaryosu)."""
    bmc = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    ci = store.apply(rec("BMC", "sp-b1", bmc, name="srv-sp", serial_number="CZJ0000710", os="RHEL 9.4")).ci_sys_id
    vc = store.start_run(uuid.uuid4(), "vCenter", started_at=NOW)
    res = store.apply(rec("vCenter", "sp-v1", vc, name="srv-sp", serial_number="CZJ0000710", ram=16384, cpu_count=4,
                          _lookup={"mac_addresses": [MAC]}))
    assert res.ci_sys_id == ci, res
    links = dict(fetch(store.conn, "SELECT source_key, sys_id FROM ingest.identity_link WHERE ci_sys_id = %s AND active",
                       ci))
    return ci, links


def _request(conn, link: str, ci: str, ds: str, key: str, *, by: str = "analist@kurum", target: str | None = None) -> str:
    """İsteği API'nin rolüyle yazar (yalnızca istek sütunlarına INSERT yetkisi var)."""
    conn.execute("SET ROLE evren_app_api")
    try:
        return fetch(conn, """INSERT INTO ingest.split_request (link_sys_id, ci_sys_id, data_source, source_key,
                                                                 target_ci, note, requested_by)
                              VALUES (%s, %s, %s, %s, %s, 'test', %s) RETURNING sys_id""",
                     link, ci, ds, key, target, by)[0][0]
    finally:
        conn.execute("SET ROLE evren_app_reconciler")


def test_split_moves_record_to_new_ci_rebuilds_identity_and_clears_its_values(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ci, links = _wrong_merge(store)
    assert fetch(as_reconciler, "SELECT class_attributes->>'ram' FROM cmdb.cmdb_ci WHERE sys_id = %s", ci)[0][0] == "16384"
    req = _request(as_reconciler, links["sp-v1"], ci, "vCenter", "sp-v1")

    [result] = store.apply_splits()
    assert result.outcome == "split" and result.ci_sys_id != ci
    new = result.ci_sys_id

    # Bağ geçmişi korunur; yeni bağ 'split' yöntemiyle, isteyen kişinin adıyla
    rows = fetch(as_reconciler, """SELECT ci_sys_id, active, method, decided_by, end_reason FROM ingest.identity_link
                                    WHERE data_source = 'vCenter' AND source_key = 'sp-v1' ORDER BY decided_at, active""")
    assert (rows[0][0], rows[0][1], rows[0][4]) == (ci, False, "ayrıldı: analist@kurum")
    assert (rows[1][0], rows[1][1], rows[1][2], rows[1][3]) == (new, True, "split", "analist@kurum")

    # Kimlik dizini: ayrılan kaydın MAC'i artık asıl CI'a aday üretmez; seri her iki kayıtta olduğu için ikisinde de var
    owners = {r[0] for r in fetch(as_reconciler, "SELECT ci_sys_id FROM ingest.identity_key WHERE key_type = 'mac' "
                                                 "AND value = %s", MAC.replace(":", ""))} | \
             {r[0] for r in fetch(as_reconciler, "SELECT ci_sys_id FROM ingest.identity_key WHERE key_type = 'mac' "
                                                 "AND value = %s", MAC)}
    assert owners == {new}

    # Asıl CI: yalnızca ayrılan kaynaktan gelen değerler temizlenir; keşif değerleri kalır
    attrs = fetch(as_reconciler, "SELECT name, serial_number, class_attributes FROM cmdb.cmdb_ci WHERE sys_id = %s", ci)[0]
    assert attrs[0] == "srv-sp" and attrs[1] == "CZJ0000710" and attrs[2].get("os") == "RHEL 9.4"
    assert "ram" not in attrs[2] and "cpu_count" not in attrs[2]
    cleared = fetch_audit(as_reconciler, "SELECT attribute, new_value, data_source, actor FROM audit.ci_change "
                                         "WHERE ci_sys_id = %s AND reason = 'split' ORDER BY attribute", ci)
    assert [(a, n, s) for a, n, s, _ in cleared] == [("cpu_count", None, "vCenter"), ("ram", None, "vCenter")]
    assert fetch(as_reconciler, "SELECT class_attributes->>'ram' FROM cmdb.cmdb_ci WHERE sys_id = %s", new)[0][0] == "16384"

    # İstek ve olay
    assert fetch(as_reconciler, "SELECT status, result_ci FROM ingest.split_request WHERE sys_id = %s", req)[0] == \
        ("applied", new)
    ev = fetch(as_reconciler, "SELECT payload FROM events.outbox WHERE event_type = 'evren.ci.split.v1' "
                              "AND aggregate_id = %s", ci)[0][0]
    assert ev["new_ci"] == new and ev["created"] is True and ev["by"] == "analist@kurum"

    # Sonraki çalışmada kayıt değişse bile asıl CI'a geri birleşmez (bağ kalıcı)
    vc = store.start_run(uuid.uuid4(), "vCenter", started_at=NOW)
    again = store.apply(rec("vCenter", "sp-v1", vc, name="srv-sp", serial_number="CZJ0000710", ram=32768, cpu_count=4,
                            _lookup={"mac_addresses": [MAC]}))
    assert (again.outcome, again.ci_sys_id) == ("linked", new)
    assert store.apply_splits() == []


def test_split_requests_that_would_harm_identity_are_rejected(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ci, links = _wrong_merge(store)
    # 1) Bağ istekten sonra değişti: aynı bağ için iki istek, ilki uygulanınca ikincisi eskir
    _request(as_reconciler, links["sp-v1"], ci, "vCenter", "sp-v1")
    first = store.apply_splits()
    assert first[0].outcome == "split"
    stale = _request(as_reconciler, links["sp-v1"], ci, "vCenter", "sp-v1", by="baska@kurum")
    # 2) CI'ın tek kaynak kaydı (artık yalnızca BMC kaldı)
    lonely = _request(as_reconciler, links["sp-b1"], ci, "BMC", "sp-b1")
    results = {r.summary for r in store.apply_splits()}
    assert results == {"Bağ istekten sonra değişti; ayırma uygulanmadı", "CI'ın tek kaynak kaydı ayrılamaz"}
    status = dict(fetch(as_reconciler, "SELECT sys_id, status FROM ingest.split_request WHERE sys_id IN (%s, %s)",
                        stale, lonely))
    assert set(status.values()) == {"rejected"}


def test_import_link_is_not_split(as_reconciler, metadata):
    """ServiceNow ilk yükleme bağı CI'ın sys_id'sini taşır; o kayıt ayrılmaz, diğerleri ayrılır."""
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    sn = store.start_run(uuid.uuid4(), "ServiceNow", started_at=NOW)
    sid = "b" * 31 + "7"
    store.import_record(rec("ServiceNow", sid, sn, name="srv-imp", serial_number="CZJ0000720"), sid)
    bmc = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    assert store.apply(rec("BMC", "imp-b1", bmc, name="srv-imp", serial_number="CZJ0000720")).ci_sys_id == sid
    link = fetch(as_reconciler, "SELECT sys_id FROM ingest.identity_link WHERE source_key = %s AND active", sid)[0][0]
    _request(as_reconciler, link, sid, "ServiceNow", sid)
    [res] = store.apply_splits()
    assert res.outcome == "split_rejected" and "ilk yükleme" in res.summary


@pytest.mark.parametrize(("role", "statement"), [
    ("evren_app_api", "UPDATE ingest.split_request SET status = 'applied'"),
    ("evren_app_api", "UPDATE ingest.identity_link SET active = false"),
    ("evren_app_reconciler", "UPDATE ingest.split_request SET requested_by = 'x'"),
    ("evren_app_reconciler", "INSERT INTO ingest.split_request (link_sys_id, ci_sys_id, data_source, source_key, "
                             "requested_by) VALUES ('x', 'x', 'x', 'x', 'x')"),
    ("evren_app_api", "SELECT ingest.replace_identity_keys('0', '', '{}', '{}')"),
    ("evren_readonly", "SELECT ingest.replace_identity_keys('0', '', '{}', '{}')"),
])
def test_split_least_privilege(conn, role, statement):
    conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute(statement)
    conn.execute("RESET ROLE")
