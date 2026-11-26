"""Elle ilişki ekleme/kaldırma isteği (U6.2, ADR-0026 §5): seçenekler, ilişki kuralı, izin, yalnızca elle ilişki
kaldırılır, istek durumu, CI ayrıntısında kaynak ve bekleyen istek. Uzlaştırıcının uygulaması uzlaştırıcı testlerinde."""
from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from .conftest import CSRF, make_client

RUNS_ON = "Runs on::Runs"


@pytest.fixture(scope="module")
def editor():
    with make_client(("evren_reader", "evren_cmdb_editor"), "iliski-ekleyen") as c:
        yield c


@pytest.fixture
def pair():
    """Sentetik uygulama ve sunucu CI'ı; sonunda silinir."""
    tag = uuid.uuid4().hex[:6]
    with psycopg.connect(autocommit=True) as c:
        app = c.execute("INSERT INTO cmdb.cmdb_ci (sys_class_name, name) VALUES ('cmdb_ci_appl', %s) RETURNING sys_id",
                        (f"api-app-{tag}",)).fetchone()[0]
        srv = c.execute("INSERT INTO cmdb.cmdb_ci (sys_class_name, name) VALUES ('cmdb_ci_linux_server', %s) "
                        "RETURNING sys_id", (f"api-srv-{tag}",)).fetchone()[0]
    yield app, srv
    with psycopg.connect(autocommit=True) as c:
        c.execute("DELETE FROM ingest.manual_relation WHERE ci_sys_id = ANY(%s::bpchar[])", ([app, srv],))
        c.execute("DELETE FROM ingest.source_record WHERE data_source = 'Manual' AND source_key = ANY(%s)", ([app, srv],))
        c.execute("DELETE FROM cmdb.cmdb_rel_ci WHERE parent = ANY(%s::bpchar[])", ([app, srv],))
        c.execute("DELETE FROM cmdb.cmdb_ci WHERE sys_id = ANY(%s::bpchar[])", ([app, srv],))


def body(target, **kw):
    return {"action": "add", "type": RUNS_ON, "direction": "parent", "target": target,
            "idempotency_key": f"test-{uuid.uuid4().hex}", **kw}


def test_options_follow_relation_rules_and_permissions(editor, reader, pair):
    app, srv = pair
    opts = editor.get(f"/api/v1/ci/{app}/relation-options").json()
    assert opts["can_add"] and opts["can_remove"]
    runs = [o for o in opts["options"] if o["type"] == RUNS_ON and o["direction"] == "parent"]
    assert {o["other_class"] for o in runs} >= {"cmdb_ci_computer"} and runs[0]["label"] == "Üzerinde çalışır"
    srv_opts = reader.get(f"/api/v1/ci/{srv}/relation-options").json()
    assert not srv_opts["can_add"] and any(o["type"] == RUNS_ON and o["direction"] == "child"
                                           and o["label"] == "Çalıştırır" for o in srv_opts["options"])


def test_add_request_validation_and_status(editor, reader, pair):
    app, srv = pair
    assert reader.post(f"/api/v1/ci/{app}/relations", json=body(srv), headers=CSRF).status_code == 403
    bad = editor.post(f"/api/v1/ci/{srv}/relations", json=body(app), headers=CSRF)  # sunucu → uygulama: yön ters
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "iliski_kurali"
    assert "Yön ters" in bad.json()["error"]["message"]
    assert editor.post(f"/api/v1/ci/{app}/relations", json=body(app), headers=CSRF).json()["error"]["code"] == "ayni_ci"
    assert editor.post(f"/api/v1/ci/{app}/relations", json=body(srv, type="Yok::Yok"),
                       headers=CSRF).json()["error"]["code"] == "iliski_tipi_yok"
    res = editor.post(f"/api/v1/ci/{app}/relations", json=body(srv), headers=CSRF)
    assert res.status_code == 202 and res.json()["status"] == "pending"
    again = editor.post(f"/api/v1/ci/{app}/relations", json=body(srv), headers=CSRF)
    assert again.status_code == 409 and again.json()["error"]["code"] == "bekleyen_istek"
    rid = res.json()["id"]
    st = reader.get(f"/api/v1/relation-requests/{rid}").json()
    assert (st["status"], st["action"], st["type"], st["target"]) == ("pending", "add", RUNS_ON, srv)
    # CI ayrıntısında bekleyen istek iki uçta da görünür (karşı uçta yön çevrilir)
    pend_app = reader.get(f"/api/v1/ci/{app}").json()["pending_relations"]
    pend_srv = reader.get(f"/api/v1/ci/{srv}").json()["pending_relations"]
    assert [(p["direction"], p["target"]) for p in pend_app] == [("parent", srv)]
    assert [(p["direction"], p["target"]) for p in pend_srv] == [("child", app)]


def test_remove_only_manual_relations(editor, pair):
    app, srv = pair
    with psycopg.connect(autocommit=True) as c:  # keşfin yazdığı ilişki
        c.execute("""INSERT INTO cmdb.cmdb_rel_ci (parent, child, type, discovery_source)
                     SELECT %s, %s, sys_id, 'BMC' FROM cmdb.cmdb_rel_type WHERE name = %s""", (app, srv, RUNS_ON))
    detail = editor.get(f"/api/v1/ci/{app}").json()
    (rel,) = [r for r in detail["relations"] if r["target"] == srv]
    assert (rel["source"], rel["manual"]) == ("BMC", False) and detail["can_unrelate"]
    res = editor.post(f"/api/v1/ci/{app}/relations", json=body(srv, action="remove"), headers=CSRF)
    assert res.status_code == 409 and res.json()["error"]["code"] == "elle_iliski_degil"
    with psycopg.connect(autocommit=True) as c:  # uzlaştırıcının yazdığı gibi: ilişki sunucunun Manual kaydında
        c.execute("""INSERT INTO ingest.source_record (data_source, source_key, class_hint, attributes, relations,
                                                       content_hash, first_seen, last_seen)
                     VALUES ('Manual', %s, 'cmdb_ci_linux_server', '{}', %s, 'x', now(), now())""",
                  (srv, Jsonb([{"type": RUNS_ON, "direction": "child", "target_source_key": app}])))
    (rel,) = [r for r in editor.get(f"/api/v1/ci/{app}").json()["relations"] if r["target"] == srv]
    assert rel["manual"] is True  # karşı uçtan girilmiş elle ilişki de tanınır
    res = editor.post(f"/api/v1/ci/{app}/relations", json=body(srv, action="remove"), headers=CSRF)
    assert res.status_code == 202
