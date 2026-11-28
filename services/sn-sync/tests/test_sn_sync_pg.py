"""ServiceNow replikası (U9.1, ADR-0042): gölge mod yazmaz; etkin modda önce doğrula, sonra yaz; aynı içerik tekrar
gitmez; toplu değişiklik onay bekler; aynı kimlikli iki CI ServiceNow'da birleşmez; ilişkiler; hata ve geri çekilme;
en az yetki.

Her test tek işlemde çalışır ve geri alınır. Kapsam testte yalnızca demo verisinde olmayan bir sınıfa daraltılır
(Solaris sunucu). ServiceNow bellek içi simülatördür."""
from __future__ import annotations

import uuid

import psycopg
import pytest
from evren_sn_sync.config import ReplicaConfig
from evren_sn_sync.worker import ReplicaWorker

from evren_core.replica import IreSimulator

ROLE = "evren_app_sync"
CLS = "cmdb_ci_solaris_server"


@pytest.fixture
def tx():
    with psycopg.connect() as conn, conn.transaction(force_rollback=True):
        conn.execute("UPDATE sync.sn_sync_scope SET enabled = false")
        conn.execute("""INSERT INTO sync.sn_sync_scope (sys_class_name, enabled) VALUES (%s, true)
                        ON CONFLICT (sys_class_name) DO UPDATE SET enabled = true""", (CLS,))
        conn.execute("UPDATE sync.replica_run SET status = 'done', finished_at = now() "
                     "WHERE status IN ('held', 'approved', 'running')")
        yield conn


def as_worker(conn):
    conn.execute(f"SET LOCAL ROLE {ROLE}")


def as_owner(conn):
    conn.execute("RESET ROLE")


def mode(conn, value, max_changes=100):
    as_owner(conn)
    conn.execute("UPDATE sync.replica_setting SET mode = %s, max_changes = %s", (value, max_changes))


def ci(conn, name, *, serial=None, sys_id=None, **cols):
    sys_id = sys_id or uuid.uuid4().hex
    names = ["sys_id", "sys_class_name", "name", "serial_number", *cols]
    conn.execute(f"INSERT INTO cmdb.cmdb_ci ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})",
                 (sys_id, CLS, name, serial, *cols.values()))
    return sys_id


def from_servicenow(conn, sys_id):
    """ServiceNow'dan aynı sys_id ile gelmiş CI (ilk yükleme): kaynak anahtarı CI sys_id'si."""
    conn.execute("""INSERT INTO ingest.source_record (data_source, source_key, class_hint, content_hash)
                    VALUES ('ServiceNow', %s, %s, 'x')""", (sys_id, CLS))
    conn.execute("""INSERT INTO ingest.identity_link (data_source, source_key, ci_sys_id, method, decided_by)
                    VALUES ('ServiceNow', %s, %s, 'import', 'test')""", (sys_id, sys_id))


def worker(sim):
    return ReplicaWorker(ReplicaConfig(conninfo="", role="", batch=10, limit=100, worker_id=f"t-{uuid.uuid4().hex[:6]}"),
                         transport=sim)


def cycle(conn, w):
    as_worker(conn)
    out = w.cycle(conn)
    as_owner(conn)
    return out


def row(conn, sys_id):
    return conn.execute("""SELECT state, sn_sys_id, shadow_verdict, shadow_operation, identified_sn_sys_id, message,
                                  skipped_fields, last_operation
                             FROM sync.sn_ci_map WHERE evren_sys_id = %s""", (sys_id,)).fetchone()


def test_off_mode_sends_nothing_but_reports_heartbeat(tx):
    mode(tx, "off")
    ci(tx, "kapali-01")
    sim = IreSimulator()
    w = worker(sim)
    cycle(tx, w)
    assert sim.calls == []
    details = tx.execute("SELECT details FROM ingest.worker_heartbeat WHERE worker_id = %s AND kind = 'sn_sync'",
                         (w.config.worker_id,)).fetchone()[0]
    assert details["mode"] == "off" and details["connection"] == "servicenow" and details["source"] == "Evren"


def test_shadow_predicts_without_writing_and_does_not_resend(tx):
    mode(tx, "shadow")
    a = ci(tx, "sn-kokenli-01", serial="S-A")
    from_servicenow(tx, a)
    b = ci(tx, "evrende-dogan-01", serial="S-B")
    c = ci(tx, "cakisan-01", serial="S-C")
    sim = IreSimulator()
    sim.seed(a, CLS, name="sn-kokenli-01", serial_number="S-A")
    sim.seed("9" * 32, CLS, name="cakisan-01")           # ServiceNow'da var, Evren'de yok
    w = worker(sim)
    out = cycle(tx, w)
    assert sim.commits == 0 and {k for k, _n in sim.calls} == {"query"}
    assert row(tx, a)[:4] == ("pending_push", None, "ok", "NO_CHANGE")
    assert row(tx, b)[2:4] == ("ok", "INSERT")
    rc = row(tx, c)
    assert rc[2] == "conflict" and rc[4] == "9" * 32 and "var olan bir kayıtla" in rc[5]
    run = tx.execute("SELECT mode, status, sent, conflicts FROM sync.replica_run ORDER BY id DESC LIMIT 1").fetchone()
    assert run == ("shadow", "done", 3, 1) and out["sent"] == 3
    calls = list(sim.calls)
    cycle(tx, w)
    assert sim.calls == calls                              # içerik değişmedi: tekrar denenmez


def test_active_verifies_before_writing(tx):
    mode(tx, "active")
    group_sn, group_evren = uuid.uuid4().hex, uuid.uuid4().hex
    tx.execute("INSERT INTO ref.sys_user_group (sys_id, name, source) VALUES (%s, 'SN grubu', 'servicenow'), "
               "(%s, 'Evren grubu', 'evren')", (group_sn, group_evren))
    a = ci(tx, "sn-kokenli-02", serial="S-A2", support_group=group_sn)
    from_servicenow(tx, a)
    b = ci(tx, "evrende-dogan-02", serial="S-B2", support_group=group_evren)
    c = ci(tx, "cakisan-02", serial="S-C2")
    sim = IreSimulator()
    sim.seed(a, CLS, name="sn-kokenli-02", serial_number="S-A2")
    sim.seed("8" * 32, CLS, name="cakisan-02", serial_number="ESKI")
    out = cycle(tx, worker(sim))
    ra, rb, rc = row(tx, a), row(tx, b), row(tx, c)
    assert ra[0] == "in_sync" and ra[1] == a                       # ServiceNow kökenli: aynı sys_id
    assert sim.cis[a].values["support_group"] == group_sn          # ServiceNow grubu gider
    assert rb[0] == "in_sync" and rb[1] in sim.cis and rb[1] != b  # Evren'de doğan: ServiceNow yeni sys_id verdi
    assert rb[6] == ["support_group"] and "support_group" not in sim.cis[rb[1]].values  # Evren grubu gitmez
    assert rc[0] == "conflict" and rc[4] == "8" * 32
    assert sim.cis["8" * 32].values["serial_number"] == "ESKI"     # çelişkili kayıt ServiceNow'da ezilmedi
    assert sim.cis[rb[1]].sources == {("Evren", b)}
    assert out["inserted"] == 1 and out["conflicts"] == 1
    assert ("query", 3) in sim.calls and ("commit", 2) in sim.calls


def test_unchanged_content_is_not_resent_and_changes_are(tx):
    mode(tx, "active")
    b = ci(tx, "degisen-01", serial="S-D1")
    sim = IreSimulator()
    w = worker(sim)
    cycle(tx, w)
    commits = sim.commits
    tx.execute("UPDATE cmdb.cmdb_ci SET attested = NULL WHERE sys_id = %s", (b,))   # kapsamda içerik değişmedi
    cycle(tx, w)
    assert sim.commits == commits                      # sürüm ilerledi ama gönderilen içerik aynı
    assert tx.execute("SELECT m.pushed_version = c.golden_version FROM sync.sn_ci_map m JOIN cmdb.cmdb_ci c "
                      "ON c.sys_id = m.evren_sys_id WHERE m.evren_sys_id = %s", (b,)).fetchone()[0]
    tx.execute("UPDATE cmdb.cmdb_ci SET name = 'degisen-01-yeni' WHERE sys_id = %s", (b,))
    cycle(tx, w)
    sn = row(tx, b)[1]
    assert sim.commits == commits + 1 and sim.cis[sn].values["name"] == "degisen-01-yeni"
    assert row(tx, b)[7] == "UPDATE"


def test_mass_change_waits_for_approval(tx):
    mode(tx, "active", max_changes=2)
    ids = [ci(tx, f"toplu-{i}", serial=f"S-T{i}") for i in range(3)]
    sim = IreSimulator()
    w = worker(sim)
    out = cycle(tx, w)
    assert out["held"] == 1 and sim.calls == []
    held = tx.execute("SELECT id, candidates, held_reason FROM sync.replica_run WHERE status = 'held'").fetchone()
    assert held[1] == 3 and "sınırını (2) aşıyor" in held[2]
    cycle(tx, w)                                         # onaysız ikinci tur da göndermez, ikinci kayıt açmaz
    assert sim.calls == [] and tx.execute("SELECT count(*) FROM sync.replica_run WHERE status = 'held'").fetchone()[0] == 1
    tx.execute("UPDATE sync.replica_run SET status = 'approved', approved_by = 'test', approved_at = now(), "
               "approve_note = 'İlk toplu itme onaylandı' WHERE id = %s", (held[0],))
    cycle(tx, w)
    assert all(row(tx, i)[0] == "in_sync" for i in ids)
    assert tx.execute("SELECT status, sent FROM sync.replica_run WHERE id = %s", (held[0],)).fetchone() == ("done", 3)


def test_two_cis_with_same_identity_are_not_merged_in_servicenow(tx):
    mode(tx, "active")
    first = ci(tx, "ikiz-01", serial="S-IKIZ")
    second = ci(tx, "ikiz-01", serial="S-IKIZ")
    sim = IreSimulator()
    w = worker(sim)
    cycle(tx, w)
    states = {row(tx, first)[0] if row(tx, first) else None, row(tx, second)[0] if row(tx, second) else None}
    assert states == {"in_sync", None}                  # ikincisi ertelendi
    cycle(tx, w)                                         # sonraki tur: deneme çelişkiyi yazmadan önce yakalar
    later = second if row(tx, first)[0] == "in_sync" else first
    assert row(tx, later)[0] == "conflict" and "başka bir Evren CI'ına eşli" in row(tx, later)[5]
    assert sum(1 for x in sim.cis.values() if x.values.get("serial_number") == "S-IKIZ") == 1


def test_shadow_predicts_same_identity_conflict(tx):
    mode(tx, "shadow")
    ci(tx, "ikiz-02", serial="S-IKIZ2")
    second = ci(tx, "ikiz-02", serial="S-IKIZ2")
    cycle(tx, worker(IreSimulator()))
    verdicts = sorted(r[0] for r in tx.execute(
        "SELECT shadow_verdict FROM sync.sn_ci_map m JOIN cmdb.cmdb_ci c ON c.sys_id = m.evren_sys_id "
        "WHERE c.serial_number = 'S-IKIZ2'").fetchall())
    assert verdicts == ["conflict", "ok"] and row(tx, second) is not None


def test_relations_follow_and_removed_relation_is_marked(tx):
    mode(tx, "active")
    p, c = ci(tx, "iliski-p", serial="S-P"), ci(tx, "iliski-c", serial="S-C")
    rel_type = tx.execute("SELECT sys_id FROM cmdb.cmdb_rel_type WHERE name = 'Depends on::Used by'").fetchone()[0]
    rel = tx.execute("INSERT INTO cmdb.cmdb_rel_ci (parent, child, type) VALUES (%s, %s, %s) RETURNING sys_id",
                     (p, c, rel_type)).fetchone()[0]
    sim = IreSimulator()
    w = worker(sim)
    out = cycle(tx, w)
    assert out["relations"] == 1 and len(sim.relations) == 1
    sp, sc = row(tx, p)[1], row(tx, c)[1]
    assert (sp, sc, "Depends on::Used by") in sim.relations
    assert tx.execute("SELECT state FROM sync.sn_rel_map WHERE evren_rel = %s", (rel,)).fetchone()[0] == "in_sync"
    tx.execute("DELETE FROM cmdb.cmdb_rel_ci WHERE sys_id = %s", (rel,))
    cycle(tx, w)
    assert tx.execute("SELECT state FROM sync.sn_rel_map WHERE evren_rel = %s", (rel,)).fetchone()[0] == "removed"


def test_unreachable_servicenow_backs_off(tx):
    mode(tx, "active")
    b = ci(tx, "erisilemez-01", serial="S-E1")
    sim = IreSimulator(fail_next=10)
    w = worker(sim)
    cycle(tx, w)
    r = tx.execute("SELECT state, attempts, next_try_at > now(), message FROM sync.sn_ci_map WHERE evren_sys_id = %s",
                   (b,)).fetchone()
    assert r[:3] == ("error", 1, True) and "ulaşılamadı" in r[3]
    run = tx.execute("SELECT status, last_error FROM sync.replica_run ORDER BY id DESC LIMIT 1").fetchone()
    assert run[0] == "failed" and run[1]
    sim.fail_next = 0
    calls = list(sim.calls)
    cycle(tx, w)
    assert sim.calls == calls                            # geri çekilme süresi dolmadan yeniden denenmez


def test_least_privilege(tx):
    b = ci(tx, "yetki-01")
    as_worker(tx)
    for stmt, args in [("UPDATE cmdb.cmdb_ci SET name = 'x' WHERE sys_id = %s", (b,)),
                       ("UPDATE sync.replica_setting SET mode = 'active'", ()),
                       ("INSERT INTO ingest.source_record (data_source, source_key, class_hint, content_hash) "
                        "VALUES ('ServiceNow', 'k', 'x', 'x')", ()),
                       ("UPDATE sync.replica_run SET approved_by = 'calisan'", ())]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), tx.transaction():
            tx.execute(stmt, args)
    as_owner(tx)


def test_run_guard_blocks_approving_finished_or_running_runs(tx):
    run = tx.execute("INSERT INTO sync.replica_run (mode, status) VALUES ('active', 'running') RETURNING id").fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation), tx.transaction():
        tx.execute("UPDATE sync.replica_run SET status = 'approved', approved_by = 'x', approved_at = now(), "
                   "approve_note = 'onaylanmamalı...' WHERE id = %s", (run,))
    tx.execute("UPDATE sync.replica_run SET status = 'done', finished_at = now() WHERE id = %s", (run,))
    with pytest.raises(psycopg.errors.CheckViolation), tx.transaction():
        tx.execute("UPDATE sync.replica_run SET sent = 5 WHERE id = %s", (run,))
    with pytest.raises(psycopg.errors.CheckViolation), tx.transaction():
        tx.execute("DELETE FROM sync.replica_run WHERE id = %s", (run,))


def test_out_of_scope_mass_update_does_not_trigger_the_brake(tx):
    """Kapsam dışı bir alanın (ör. keşif kaynağı) toplu değişmesi ServiceNow'a gidecek bir şey üretmez: fren tetiklenmez,
    hiçbir şey gönderilmez, görülen sürüm ilerler."""
    mode(tx, "active", max_changes=10)
    ids = [ci(tx, f"kapsam-disi-{i}", serial=f"S-K{i}") for i in range(3)]
    sim = IreSimulator()
    w = worker(sim)
    cycle(tx, w)
    calls = list(sim.calls)
    mode(tx, "active", max_changes=1)
    tx.execute("UPDATE cmdb.cmdb_ci SET discovery_source = 'Evren' WHERE sys_id = ANY(%s)", (ids,))
    out = cycle(tx, w)
    assert out["held"] == 0 and out["skipped_same"] == 3 and sim.calls == calls
    assert tx.execute("SELECT count(*) FROM sync.replica_run WHERE status = 'held'").fetchone()[0] == 0


def test_accepted_conflict_is_verified_then_written(tx):
    """Çelişkide insan ServiceNow'un bulduğu kaydı kabul eder (API ``accepted_sn_sys_id`` yazar, ``sn_sys_id``
    yazamaz); sn-sync o kaydı bekleyerek yeniden dener, doğrulayınca eşlemeye yazar."""
    mode(tx, "active")
    c = ci(tx, "kabul-01", serial="S-KABUL")
    sim = IreSimulator()
    sim.seed("7" * 32, CLS, name="kabul-01", serial_number="ESKI-KABUL")
    w = worker(sim)
    cycle(tx, w)
    assert row(tx, c)[0] == "conflict" and row(tx, c)[4] == "7" * 32
    assert sim.cis["7" * 32].values["serial_number"] == "ESKI-KABUL"
    tx.execute("UPDATE sync.sn_ci_map SET state = 'pending_push', accepted_sn_sys_id = identified_sn_sys_id "
               "WHERE evren_sys_id = %s", (c,))
    cycle(tx, w)
    r = row(tx, c)
    assert r[0] == "in_sync" and r[1] == "7" * 32 and sim.cis["7" * 32].values["serial_number"] == "S-KABUL"
    assert tx.execute("SELECT accepted_sn_sys_id FROM sync.sn_ci_map WHERE evren_sys_id = %s", (c,)).fetchone()[0] is None
