"""ServiceNow replikası API'si (U9.1, ADR-0042): okuma yetkisi, mod değişikliği gerekçeli ve etkin moddan önce gölge
turu şart, kapsam açma/kapama, bekletilen toplu itmenin onayı bir kez, çelişki kararları (kabul, dışarıda tut,
yeniden dene) ve başka CI'a eşli kaydın kabul edilmemesi; hepsi denetim izine. Çalışmaları ve eşlemeleri sn-sync
yazar; burada tablo sahibi rolüyle hazırlanır. Veriler sentetiktir."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from .conftest import CSRF, make_client

CM = ("evren_config_manager", "evren_reader")
OPERATOR = ("evren_operator", "evren_reader")
NOTE = "Test: replika ayarı gerekçesi"


def owner():
    return psycopg.connect(autocommit=True)


@pytest.fixture(scope="module")
def cm():
    with make_client(CM, "cm-replika") as c:
        yield c
    with owner() as o:
        o.execute("UPDATE sync.replica_setting SET mode = 'off', max_changes = 200")
        o.execute("UPDATE sync.replica_run SET status = 'done', finished_at = now() "
                  "WHERE status IN ('held', 'approved', 'running')")


def ci(conn, name):
    sys_id = uuid.uuid4().hex
    conn.execute("INSERT INTO cmdb.cmdb_ci (sys_id, sys_class_name, name) VALUES (%s, 'cmdb_ci_linux_server', %s)",
                 (sys_id, name))
    return sys_id


def conflict(conn, ci_id, identified, state="conflict"):
    conn.execute("""INSERT INTO sync.sn_ci_map (evren_sys_id, sn_class, state, identified_sn_sys_id, message)
                    VALUES (%s, 'cmdb_ci_linux_server', %s, %s, 'ServiceNow başka kayıt buldu')""",
                 (ci_id, state, identified))


def events(action):
    with owner() as o:
        return o.execute("SELECT count(*) FROM audit.security_event WHERE action = %s", (action,)).fetchone()[0]


def test_read_requires_platform_or_replica_permission(cm):
    with make_client(("evren_reader",), "okur-replika") as r:
        assert r.get("/api/v1/replica").status_code == 403
        me = r.get("/api/v1/me").json()["can"]
        assert me["replica"] is False and me["replica_admin"] is False
    with make_client(OPERATOR, "op-replika") as op:
        body = op.get("/api/v1/replica").json()
        assert body["can_admin"] is False and body["setting"]["mode"] in ("off", "shadow", "active")
        assert op.put("/api/v1/replica/settings", json={"mode": "shadow", "max_changes": 10, "note": NOTE},
                      headers=CSRF).status_code == 403
    body = cm.get("/api/v1/replica").json()
    assert body["can_admin"] is True and cm.get("/api/v1/me").json()["can"]["replica_admin"] is True
    assert {s["class"] for s in body["scope"]} >= {"cmdb_ci_server", "cmdb_ci_netgear"}
    assert set(body["counts"]) >= {"in_sync", "conflict", "error"} and body["labels"]["modes"]["shadow"] == "Gölge"


def test_mode_change_needs_reason_and_shadow_before_active(cm):
    with owner() as o:
        o.execute("UPDATE sync.replica_setting SET mode = 'off'")
    short = cm.put("/api/v1/replica/settings", json={"mode": "shadow", "max_changes": 50, "note": "kısa"}, headers=CSRF)
    assert short.status_code == 422 and short.json()["error"]["code"] == "gerekce_gerekli"
    before = events("replica_setting")
    assert cm.put("/api/v1/replica/settings", json={"mode": "shadow", "max_changes": 50, "note": NOTE},
                  headers=CSRF).status_code == 200
    assert events("replica_setting") == before + 1
    shadow_done = cm.get("/api/v1/replica").json()["shadow_done"]
    r = cm.put("/api/v1/replica/settings", json={"mode": "active", "max_changes": 50, "note": NOTE}, headers=CSRF)
    if not shadow_done:
        assert r.status_code == 409 and r.json()["error"]["code"] == "golge_gerekli"
        with owner() as o:
            o.execute("INSERT INTO sync.replica_run (mode, status, finished_at) VALUES ('shadow', 'done', now())")
        r = cm.put("/api/v1/replica/settings", json={"mode": "active", "max_changes": 50, "note": NOTE}, headers=CSRF)
    assert r.status_code == 200 and "Etkin" in r.json()["message"]
    assert cm.get("/api/v1/replica").json()["setting"]["mode"] == "active"
    assert cm.put("/api/v1/replica/settings", json={"mode": "off", "max_changes": 0, "note": NOTE},
                  headers=CSRF).status_code == 422


def test_scope_toggle_is_audited(cm):
    r = cm.put("/api/v1/replica/scope/cmdb_ci_appl", json={"enabled": False, "note": NOTE}, headers=CSRF)
    assert r.status_code == 200
    scope = {s["class"]: s["enabled"] for s in cm.get("/api/v1/replica").json()["scope"]}
    assert scope["cmdb_ci_appl"] is False
    assert cm.put("/api/v1/replica/scope/cmdb_ci_appl", json={"enabled": True, "note": NOTE},
                  headers=CSRF).status_code == 200
    assert cm.put("/api/v1/replica/scope/olmayan_sinif", json={"enabled": True, "note": NOTE},
                  headers=CSRF).status_code == 404


def test_held_push_is_approved_once_and_shows_in_attention(cm):
    with owner() as o:
        o.execute("UPDATE sync.replica_run SET status = 'done', finished_at = now() "
                  "WHERE status IN ('held', 'approved', 'running')")
        run = o.execute("""INSERT INTO sync.replica_run (mode, status, candidates, held_reason)
                           VALUES ('active', 'held', 777, 'sınır aşıldı') RETURNING id""").fetchone()[0]
    items = {i["id"]: i for i in cm.get("/api/v1/admin/attention").json()["items"]}
    assert items["replica_held"]["count"] == 777 and items["replica_held"]["href"] == "#/replika"
    assert cm.get("/api/v1/replica").json()["open_run"]["id"] == run
    with make_client(OPERATOR, "op-replika-2") as op:
        assert op.post(f"/api/v1/replica/runs/{run}/approve", json={"note": NOTE}, headers=CSRF).status_code == 403
    r = cm.post(f"/api/v1/replica/runs/{run}/approve", json={"note": NOTE}, headers=CSRF)
    assert r.status_code == 200 and "777" in r.json()["message"]
    again = cm.post(f"/api/v1/replica/runs/{run}/approve", json={"note": NOTE}, headers=CSRF)
    assert again.status_code == 409
    with owner() as o:
        assert o.execute("SELECT status, approved_by FROM sync.replica_run WHERE id = %s", (run,)).fetchone() == (
            "approved", "dev:cm-replika")


def test_conflict_decisions(cm):
    with owner() as o:
        a, b, c = ci(o, "replika-cakisma-a"), ci(o, "replika-cakisma-b"), ci(o, "replika-cakisma-c")
        other = ci(o, "replika-baska-evren-ci")
        found = uuid.uuid4().hex
        conflict(o, a, found)
        conflict(o, b, other)                        # ServiceNow'un bulduğu kayıt başka bir Evren CI'ı
        conflict(o, c, None, state="error")
    view = cm.get("/api/v1/replica/items", params={"view": "attention", "limit": 200}).json()
    assert {a, b, c} <= {i["sys_id"] for i in view["items"]}
    before = events("replica_resolve")
    r = cm.post(f"/api/v1/replica/items/{a}/resolve", json={"action": "accept", "note": NOTE}, headers=CSRF)
    assert r.status_code == 200
    r = cm.post(f"/api/v1/replica/items/{b}/resolve", json={"action": "accept", "note": NOTE}, headers=CSRF)
    assert r.status_code == 409 and r.json()["error"]["code"] == "baska_ci_eslesik"
    assert cm.post(f"/api/v1/replica/items/{b}/resolve", json={"action": "exclude", "note": NOTE},
                   headers=CSRF).status_code == 200
    assert cm.post(f"/api/v1/replica/items/{c}/resolve", json={"action": "accept", "note": NOTE},
                   headers=CSRF).status_code == 409             # hata kaydında ServiceNow kaydı yok
    assert cm.post(f"/api/v1/replica/items/{c}/resolve", json={"action": "retry", "note": NOTE},
                   headers=CSRF).status_code == 200
    assert cm.post(f"/api/v1/replica/items/{c}/resolve", json={"action": "retry", "note": NOTE},
                   headers=CSRF).status_code == 409             # artık bekliyor: yeniden denenecek bir şey yok
    with owner() as o:
        rows = dict(o.execute("SELECT evren_sys_id, (state, accepted_sn_sys_id) FROM sync.sn_ci_map "
                              "WHERE evren_sys_id = ANY(%s)", ([a, b, c],)).fetchall())
    assert rows[a] == ("pending_push", found) and rows[b][0] == "excluded" and rows[c][0] == "pending_push"
    assert events("replica_resolve") == before + 3
    assert cm.post(f"/api/v1/replica/items/{uuid.uuid4().hex}/resolve", json={"action": "retry", "note": NOTE},
                   headers=CSRF).status_code == 404
