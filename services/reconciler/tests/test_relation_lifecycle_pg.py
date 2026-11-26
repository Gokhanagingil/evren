"""İlişki yaşam döngüsü (U7.4, ADR-0031) — gerçek uzlaştırıcı ve PostgreSQL, en az yetkili rol.

İlişkiyi hangi kaynak kaydının bildirdiği tutulur. Tam (snapshot) ve devre kesicisi sağlıklı bir çalışmada kaynak kaydı
geldiği hâlde ilişkiyi artık listelemiyorsa bildirim düşer; hiçbir kaynağın (elle giriş dahil) bildirmediği ilişki
kalkar. Açılma ve kalkma iki ucun değişim geçmişine yazılır. Delta veya eksik çalışma, görülmeyen kayıt ve başka bir
kaynağın bildirimi ilişkiyi korur. Veriler sentetiktir.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from evren_core.envelope import IngestRecord, RelationRef
from evren_reconciler import ReconcilerStore

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
DEPENDS = "Depends on::Used by"


def fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_owner(conn, sql, *params):
    """Denetim izini sahip rolüyle okur (uzlaştırıcı yalnızca ekler, okuyamaz)."""
    conn.execute("RESET ROLE")
    try:
        return fetch(conn, sql, *params)
    finally:
        conn.execute("SET ROLE evren_app_reconciler")


def server(ds, key, run, tag, n, *, depends_on=None):
    rels = [RelationRef(type=DEPENDS, direction="parent", target_source_key=depends_on)] if depends_on else []
    return IngestRecord(data_source=ds, run_id=run, source_key=key, class_hint="cmdb_ci_linux_server",
                        attributes={"name": f"rel-{tag}-{n}", "serial_number": f"RL{tag}{n}"}, relations=rels,
                        observed_at=NOW)


def rel_rows(conn, a, b):
    return fetch(conn, """SELECT r.sys_id FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                           WHERE r.parent = %s AND r.child = %s AND t.name = %s""", a, b, DEPENDS)


def reporters(conn, a, b):
    return sorted(fetch(conn, """SELECT p.data_source, p.source_key FROM cmdb.cmdb_rel_ci_source p
                                   JOIN cmdb.cmdb_rel_ci r ON r.sys_id = p.rel_sys_id WHERE r.parent = %s AND r.child = %s""",
                        a, b))


def relation_events(conn, ci):
    return fetch_owner(conn, """SELECT coalesce(old_value, 'null') = 'null', coalesce(new_value, 'null') = 'null',
                                   data_source, reason, correlation->>'ended'
                              FROM audit.ci_change WHERE ci_sys_id = %s AND attribute = 'relation'
                           ORDER BY changed_at, id""", ci)


FILLER = 10  # devre kesici değişim oranına bakar: tek ilişkinin kalkması filonun küçük bir kısmıdır


def run_bmc(store, tag, *, a_depends=True, include_a=True, mode="snapshot", completed=True, ds="BMC", prefix="b",
            warmup=False):
    """Kaynağın gördüğü filo: dolgu sunucuları + a (b'ye bağımlı) + b. ``warmup``: önce taban çizgisi için üç tam
    çalışma (devre kesici ancak taban çizgisiyle OK verir; ilk çalışmalarda yıkıcı işlem bekletilir)."""
    for _ in range(3 if warmup else 1):
        run = store.start_run(uuid.uuid4(), ds, mode=mode, started_at=NOW)
        out = {}
        for i in range(FILLER):
            store.apply(server(ds, f"{prefix}-{tag}-f{i}", run, tag, f"f{i}"))
        if include_a:
            out["a"] = store.apply(server(ds, f"{prefix}-{tag}-a", run, tag, "a",
                                          depends_on=f"{prefix}-{tag}-b" if a_depends else None)).ci_sys_id
        out["b"] = store.apply(server(ds, f"{prefix}-{tag}-b", run, tag, "b")).ci_sys_id
        verdict = store.finish_run(run, completed=completed)
    return out, verdict, run


def test_relation_ends_when_the_source_stops_reporting_it_in_a_healthy_full_run(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6].upper()
    cis, _, _ = run_bmc(store, tag, warmup=True)
    a, b = cis["a"], cis["b"]
    assert len(rel_rows(as_reconciler, a, b)) == 1
    assert reporters(as_reconciler, a, b) == [("BMC", f"b-{tag}-a")]
    # Açılma iki ucun geçmişinde (zaman içinde sorgu bu izden çalışır)
    assert relation_events(as_reconciler, a) == [(True, False, "BMC", "reconciliation", None)]
    assert relation_events(as_reconciler, b) == [(True, False, "BMC", "reconciliation", None)]
    # Kaynak aynı ilişkiyi yeniden bildirince yeni olay yazılmaz
    run_bmc(store, tag)
    assert len(relation_events(as_reconciler, a)) == 1
    # Kayıt geldi ama ilişki listesinde yok: bildirim düşer, ilişki kalkar
    _, verdict, run = run_bmc(store, tag, a_depends=False)
    assert verdict.allows_destructive
    assert rel_rows(as_reconciler, a, b) == []
    assert relation_events(as_reconciler, a)[-1] == (False, True, "BMC", "reconciliation", "not_reported")
    assert relation_events(as_reconciler, b)[-1] == (False, True, "BMC", "reconciliation", "not_reported")
    (notes,), = fetch_owner(as_reconciler, "SELECT notes FROM ingest.source_run WHERE run_id = %s", run)
    assert "biten ilişki: 1" in notes
    # CI'lar silinmez (kural 3)
    assert fetch(as_reconciler, "SELECT count(*) FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])", [a, b])[0][0] == 2
    # Kaynak ilişkiyi yeniden bildirirse yeni satır ve yeni açılma olayı
    run_bmc(store, tag)
    assert len(rel_rows(as_reconciler, a, b)) == 1
    assert [e[0] for e in relation_events(as_reconciler, a)] == [True, False, True]


def test_delta_incomplete_run_and_missing_holder_keep_the_relation(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6].upper()
    cis, _, _ = run_bmc(store, tag, warmup=True)
    a, b = cis["a"], cis["b"]
    # Delta çalışmasında ilişki listesi eksik olabilir (ör. ServiceNow yalnızca güncellenenleri verir)
    run_bmc(store, tag, a_depends=False, mode="delta")
    assert len(rel_rows(as_reconciler, a, b)) == 1
    # Eksik çalışma: devre kesici açık, yıkıcı işlem yok (kural 4)
    _, verdict, _ = run_bmc(store, tag, a_depends=False, completed=False)
    assert not verdict.allows_destructive
    assert len(rel_rows(as_reconciler, a, b)) == 1
    # İlişkiyi bildiren kayıt bu çalışmada hiç gelmedi: görülmeyen kaydın ilişkisi eskime kararına kadar korunur
    run_bmc(store, tag, include_a=False)
    assert len(rel_rows(as_reconciler, a, b)) == 1
    assert reporters(as_reconciler, a, b) == [("BMC", f"b-{tag}-a")]
    assert [e[0] for e in relation_events(as_reconciler, a)] == [True]


def test_relation_stays_while_another_source_or_a_manual_entry_reports_it(as_reconciler, metadata):
    store = ReconcilerStore(as_reconciler, metadata, clock=lambda: NOW)
    tag = uuid.uuid4().hex[:6].upper()
    cis, _, _ = run_bmc(store, tag, warmup=True)
    a, b = cis["a"], cis["b"]
    # İkinci bir keşif kaynağı aynı sunucuları (seri numarasıyla aynı CI'lar) ve aynı ilişkiyi bildirir
    other, _, _ = run_bmc(store, tag, ds="vCenter", prefix="v", warmup=True)
    assert (other["a"], other["b"]) == (a, b)
    assert reporters(as_reconciler, a, b) == [("BMC", f"b-{tag}-a"), ("vCenter", f"v-{tag}-a")]
    run_bmc(store, tag, a_depends=False)  # BMC bıraktı: vCenter hâlâ bildiriyor
    assert reporters(as_reconciler, a, b) == [("vCenter", f"v-{tag}-a")]
    assert len(rel_rows(as_reconciler, a, b)) == 1
    # Elle de girilmiş ilişki: keşfin son bildirimi düşse de kalır
    (rel_id,), = rel_rows(as_reconciler, a, b)
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("""INSERT INTO ingest.manual_relation (ci_sys_id, target_sys_id, type, direction, action,
                                                                requested_by)
                             VALUES (%s, %s, %s, 'parent', 'add', 'ayse')""", (a, b, DEPENDS))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    assert [r.outcome for r in store.apply_manual_relations()] == ["relation"]
    run_bmc(store, tag, a_depends=False, ds="vCenter", prefix="v")
    assert rel_rows(as_reconciler, a, b) == [(rel_id,)]
    assert reporters(as_reconciler, a, b) == [("Manual", a)]
    # Elle kayıt da kaldırılınca bildiren kalmaz: satır silinir
    as_reconciler.execute("RESET ROLE")
    as_reconciler.execute("""INSERT INTO ingest.manual_relation (ci_sys_id, target_sys_id, type, direction, action,
                                                                requested_by)
                             VALUES (%s, %s, %s, 'parent', 'remove', 'ayse')""", (a, b, DEPENDS))
    as_reconciler.execute("SET ROLE evren_app_reconciler")
    store.apply_manual_relations()
    assert rel_rows(as_reconciler, a, b) == []
    # Elle eklemede satır zaten vardı: olay "etkisiz" işaretlidir; zaman içinde sorgu bunu atlar
    effects = fetch_owner(as_reconciler, """SELECT correlation->>'effect', coalesce(old_value, 'null') = 'null'
                                             FROM audit.ci_change
                                       WHERE ci_sys_id = %s AND attribute = 'relation' AND reason = 'manual'
                                       ORDER BY changed_at, id""", a)
    assert effects == [("none", True), (None, False)]
