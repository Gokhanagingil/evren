"""Elle ilişki ekleme/kaldırma (U6.2, ADR-0026 §5) — gerçek uzlaştırıcı, PostgreSQL, en az yetkili rol.

İlişki CI'ın Manual kaynak kaydındaki listede durur ve cmdb_rel_ci'a "Manual" kaynağıyla yazılır; izinli ilişki
kuralı zorunludur; yalnızca elle girilmiş ilişki kaldırılır, keşfin satırı kalır. Elle düzenleme ilişkileri silmez.
Veriler sentetiktir.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 11, 0, tzinfo=UTC)
RUNS_ON = "Runs on::Runs"


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def as_owner(conn, sql, *params):
    """Sahip rolüyle (API'nin ve kaynakların yazdığı gibi) çalıştırır; RETURNING varsa satırları döner."""
    conn.execute("RESET ROLE")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []
    finally:
        conn.execute("SET ROLE evren_app_reconciler")


def make_ci(conn, cls, name) -> str:
    (ci,), = as_owner(conn, "INSERT INTO cmdb.cmdb_ci (sys_class_name, name) VALUES (%s, %s) RETURNING sys_id", cls, name)
    return ci


def relate(conn, ci, target, *, action="add", direction="parent", rel_type=RUNS_ON, by="mehmet") -> str:
    (rid,), = as_owner(conn, """INSERT INTO ingest.manual_relation (ci_sys_id, target_sys_id, type, direction, action,
                                                                requested_by)
                                VALUES (%s, %s, %s, %s, %s, %s) RETURNING sys_id""",
                       ci, target, rel_type, direction, action, by)
    return rid


def status(conn, rid):
    return fetch(conn, "SELECT status, result_note FROM ingest.manual_relation WHERE sys_id = %s", rid)[0]


def rels(conn, parent, child):
    return fetch(conn, """SELECT t.name, r.discovery_source FROM cmdb.cmdb_rel_ci r
                            JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                           WHERE r.parent = %s AND r.child = %s""", parent, child)


def test_add_and_remove_manual_relation_with_provenance(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6]
    app, srv = make_ci(as_reconciler, "cmdb_ci_appl", f"bordro-{tag}"), make_ci(as_reconciler, "cmdb_ci_linux_server",
                                                                                f"srv-{tag}")
    rid = relate(as_reconciler, app, srv)
    (res,) = store.apply_manual_relations()
    assert res.outcome == "relation" and status(as_reconciler, rid) == ("applied", "İlişki eklendi.")
    assert rels(as_reconciler, app, srv) == [(RUNS_ON, "Manual")]
    (relations,), = fetch(as_reconciler, """SELECT relations FROM ingest.source_record
                                           WHERE data_source = 'Manual' AND source_key = %s""", app)
    assert relations == [{"type": RUNS_ON, "direction": "parent", "target_source_key": srv,
                          "target_data_source": "Manual", "target_class_hint": "cmdb_ci_linux_server"}]
    as_reconciler.execute("RESET ROLE")
    audit = fetch(as_reconciler, """SELECT ci_sys_id, actor, reason, new_value->>'type' FROM audit.ci_change
                                     WHERE attribute = 'relation' AND ci_sys_id = ANY(%s::bpchar[])
                                     ORDER BY ci_sys_id""", [app, srv])
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    assert sorted(audit) == sorted([(app, "mehmet", "manual", RUNS_ON), (srv, "mehmet", "manual", RUNS_ON)])

    # elle düzenleme Manual kaydını yeniden yazar ama ilişki listesini korur
    as_owner(as_reconciler, """INSERT INTO ingest.manual_edit (ci_sys_id, attributes, requested_by)
                               VALUES (%s, %s, 'mehmet')""", app, Jsonb({"short_description": "Bordro uygulaması"}))
    store.apply_manual_edits()
    assert fetch(as_reconciler, """SELECT jsonb_array_length(relations) FROM ingest.source_record
                                   WHERE data_source = 'Manual' AND source_key = %s""", app) == [(1,)]

    # kaldırma karşı uçtan (sunucunun panelinden, yön "child"): uygulamanın Manual kaydında bulunur
    rid = relate(as_reconciler, srv, app, action="remove", direction="child")
    store.apply_manual_relations()
    assert status(as_reconciler, rid) == ("applied", "İlişki kaldırıldı.")
    assert rels(as_reconciler, app, srv) == []
    assert fetch(as_reconciler, """SELECT jsonb_array_length(relations) FROM ingest.source_record
                                   WHERE data_source = 'Manual' AND source_key = %s""", app) == [(0,)]


def test_rule_violation_and_reversed_direction_are_rejected(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6]
    app, srv = make_ci(as_reconciler, "cmdb_ci_appl", f"app-{tag}"), make_ci(as_reconciler, "cmdb_ci_linux_server",
                                                                             f"srv-{tag}")
    rid = relate(as_reconciler, srv, app)  # sunucu → uygulama "Runs on": yön ters
    store.apply_manual_relations()
    st, note = status(as_reconciler, rid)
    assert st == "rejected" and note.startswith("Yön ters")
    rid = relate(as_reconciler, app, srv, rel_type="Connects to::Connected by")  # kural yok
    store.apply_manual_relations()
    st, note = status(as_reconciler, rid)
    assert st == "rejected" and note.startswith("İzinli ilişki kuralı yok")
    assert rels(as_reconciler, app, srv) == [] and rels(as_reconciler, srv, app) == []


def test_discovered_relation_is_not_removed_manually(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6]
    app, srv = make_ci(as_reconciler, "cmdb_ci_appl", f"app-{tag}"), make_ci(as_reconciler, "cmdb_ci_linux_server",
                                                                             f"srv-{tag}")
    as_owner(as_reconciler, """INSERT INTO cmdb.cmdb_rel_ci (parent, child, type, discovery_source)
                               SELECT %s, %s, sys_id, 'BMC' FROM cmdb.cmdb_rel_type WHERE name = %s""",
             app, srv, RUNS_ON)
    # Keşfin bildirdiği ilişkinin bildirimi (ADR-0031): elle kaldırma bu satırı silmez
    as_owner(as_reconciler, """INSERT INTO cmdb.cmdb_rel_ci_source (rel_sys_id, data_source, source_key)
                               SELECT sys_id, 'BMC', %s FROM cmdb.cmdb_rel_ci WHERE parent = %s AND child = %s""",
             f"bmc-{tag}", app, srv)
    rid = relate(as_reconciler, app, srv, action="remove")
    store.apply_manual_relations()
    st, note = status(as_reconciler, rid)
    assert st == "rejected" and "keşfin bildirdiği ilişki elle kaldırılamaz" in note
    assert rels(as_reconciler, app, srv) == [(RUNS_ON, "BMC")]
    # elle aynı ilişki eklenirse keşfin satırı korunur; elle kaydı kaldırmak satırı silmez
    relate(as_reconciler, app, srv)
    store.apply_manual_relations()
    rid = relate(as_reconciler, app, srv, action="remove")
    store.apply_manual_relations()
    assert status(as_reconciler, rid)[1].startswith("Elle kayıt kaldırıldı")
    assert rels(as_reconciler, app, srv) == [(RUNS_ON, "BMC")]
