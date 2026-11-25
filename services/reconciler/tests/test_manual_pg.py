"""Elle düzenleme (göç 0008): istek API rolüyle yazılır, uzlaştırıcı Manual kaynak kaydı olarak uygular."""
from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_store_pg import NOW, fetch, fetch_audit, rec

from evren_reconciler import ReconcilerStore

GROUP = "c" * 31 + "1"


def _ci(store: ReconcilerStore, tag: str) -> str:
    run = store.start_run(uuid.uuid4(), "BMC", started_at=NOW)
    return store.apply(rec("BMC", f"me-{tag}", run, name=f"srv-{tag}", serial_number=f"CZJME{tag}",
                           os="RHEL 9.4")).ci_sys_id


def _edit(conn, ci: str, attributes: dict, by: str = "analist@kurum") -> str:
    conn.execute("SET ROLE evren_app_api")
    try:
        return fetch(conn, "INSERT INTO ingest.manual_edit (ci_sys_id, attributes, note, requested_by) "
                           "VALUES (%s, %s, 'test', %s) RETURNING sys_id", ci, Jsonb(attributes), by)[0][0]
    finally:
        conn.execute("SET ROLE evren_app_reconciler")


def test_manual_edit_goes_through_reconciliation_with_role_priority(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ci = _ci(store, "01")
    req = _edit(as_reconciler, ci, {"used_for": "Production", "support_group": GROUP, "os": "Elle yazılan"})
    [res] = store.apply_manual_edits()
    assert res.outcome == "manual" and res.ci_sys_id == ci

    row = fetch(as_reconciler, "SELECT used_for, support_group, class_attributes->>'os' FROM cmdb.cmdb_ci "
                               "WHERE sys_id = %s", ci)[0]
    assert row == ("Production", GROUP, "RHEL 9.4")  # keşif sahipli alanda Manual yedektir
    status, result, note = fetch(as_reconciler, "SELECT status, result, result_note FROM ingest.manual_edit "
                                                "WHERE sys_id = %s", req)[0]
    assert status == "applied"
    assert result == {"used_for": {"status": "applied", "source": "Manual"},
                      "support_group": {"status": "applied", "source": "Manual"},
                      "os": {"status": "overridden", "source": "BMC"}}
    assert note == "2 alan uygulandı; 1 alan daha öncelikli kaynakta; elle değer yedek olarak saklandı"

    # Denetim: neden 'manual', yapan insan; bağ 'manual' yöntemiyle aynı CI'a, kimlik anahtarı eklemez
    audit = fetch_audit(as_reconciler, "SELECT attribute, reason, actor, data_source FROM audit.ci_change "
                                       "WHERE ci_sys_id = %s AND reason = 'manual' ORDER BY attribute", ci)
    assert audit == [("support_group", "manual", "analist@kurum", "Manual"),
                     ("used_for", "manual", "analist@kurum", "Manual")]
    link = fetch(as_reconciler, "SELECT ci_sys_id, method, decided_by FROM ingest.identity_link "
                                "WHERE data_source = 'Manual' AND source_key = %s AND active", ci)
    assert link == [(ci, "manual", "analist@kurum")]
    # Elle değer kaynak kaydında saklanır (keşif kaynağı kaybolursa geçerli olur)
    stored = fetch(as_reconciler, "SELECT attributes FROM ingest.source_record WHERE data_source = 'Manual' "
                                  "AND source_key = %s", ci)[0][0]
    assert stored == {"used_for": "Production", "support_group": GROUP, "os": "Elle yazılan"}


def test_second_edit_merges_and_null_removes_manual_value(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ci = _ci(store, "02")
    _edit(as_reconciler, ci, {"used_for": "Production", "short_description": "Muhasebe veritabanı"})
    store.apply_manual_edits()
    req = _edit(as_reconciler, ci, {"used_for": None}, by="ikinci@kurum")
    store.apply_manual_edits()
    row = fetch(as_reconciler, "SELECT used_for, short_description FROM cmdb.cmdb_ci WHERE sys_id = %s", ci)[0]
    assert row == (None, "Muhasebe veritabanı")  # kaldırılan elle değer golden record'da sahipsiz kalmaz
    assert fetch(as_reconciler, "SELECT result FROM ingest.manual_edit WHERE sys_id = %s", req)[0][0] == \
        {"used_for": {"status": "cleared", "source": None}}
    cleared = fetch_audit(as_reconciler, "SELECT old_value, new_value, actor FROM audit.ci_change WHERE ci_sys_id = %s "
                                         "AND attribute = 'used_for' ORDER BY id DESC LIMIT 1", ci)[0]
    assert cleared == ("Production", None, "ikinci@kurum")
    winners = fetch(as_reconciler, "SELECT winners FROM cmdb.ci_reconciliation_state WHERE ci_sys_id = %s", ci)[0][0]
    assert "used_for" not in winners and winners["short_description"]["source"] == "Manual"


def test_identical_edit_is_harmless(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ci = _ci(store, "03")
    _edit(as_reconciler, ci, {"used_for": "Test"})
    store.apply_manual_edits()
    req = _edit(as_reconciler, ci, {"used_for": "Test"})
    [res] = store.apply_manual_edits()
    assert res.changes == []
    assert fetch(as_reconciler, "SELECT result FROM ingest.manual_edit WHERE sys_id = %s", req)[0][0] == \
        {"used_for": {"status": "applied", "source": "Manual"}}


@pytest.mark.parametrize(("role", "statement"), [
    ("evren_app_api", "UPDATE ingest.manual_edit SET status = 'applied'"),
    ("evren_app_api", "INSERT INTO ingest.source_record (data_source, source_key, class_hint, content_hash) "
                      "VALUES ('Manual', 'x', 'cmdb_ci', 'x')"),
    ("evren_app_reconciler", "UPDATE ingest.manual_edit SET attributes = '{\"a\": 1}'"),
    ("evren_app_reconciler", "INSERT INTO ingest.manual_edit (ci_sys_id, attributes, requested_by) "
                             "VALUES ('x', '{\"a\": 1}', 'x')"),
])
def test_manual_edit_least_privilege(conn, role, statement):
    conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute(statement)
    conn.execute("RESET ROLE")


def test_manual_link_split_request_is_rejected_by_reconciler(as_reconciler, metadata):
    """API'yi atlayan bir istek de uzlaştırıcıda reddedilir (savunma derinliği)."""
    from test_split_pg import _request

    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    ci = _ci(store, "04")
    _edit(as_reconciler, ci, {"used_for": "Test"})
    store.apply_manual_edits()
    link = fetch(as_reconciler, "SELECT sys_id FROM ingest.identity_link WHERE data_source = 'Manual' "
                                "AND source_key = %s AND active", ci)[0][0]
    _request(as_reconciler, link, ci, "Manual", ci)
    [res] = store.apply_splits()
    assert res.outcome == "split_rejected" and "Elle giriş" in res.summary
