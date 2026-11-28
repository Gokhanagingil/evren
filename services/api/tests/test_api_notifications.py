"""Bildirim merkezi, platform uyarıları ve /metrics (U8.9, ADR-0040): kişi, grup ve rol bildirimleri; okundu bilgisi
kişi başına; başkasının bildirimi okunamaz; yönlendirme ve sınama yalnızca ops_admin; uyarı onayı gerekçeli ve bir
kez; /metrics belirteçsiz kapalı. Bildirim ve uyarıları görev çalışanı üretir; burada tablo sahibi rolüyle
hazırlanır. Veriler sentetiktir."""
from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from evren_api import create_app
from evren_api.config import ConfigError, Settings

from .conftest import CSRF, make_client

OPERATOR = ("evren_operator", "evren_reader")


def owner():
    return psycopg.connect(autocommit=True)


def note(conn, aud_kind, aud, title, severity="info"):
    return conn.execute(
        """INSERT INTO notify.notification (kind, severity, audience_kind, audience, title, body, link, source, dedup_key)
           VALUES ('task_assigned', %s, %s, %s, %s, 'Gövde', '#/gorevler', 'test', %s) RETURNING sys_id""",
        (severity, aud_kind, aud, title, f"test:{uuid.uuid4().hex}")).fetchone()[0]


@pytest.fixture(scope="module")
def operator():
    with make_client(OPERATOR, "operator-bildirim") as c:
        yield c


def test_inbox_shows_mine_my_groups_and_roles_only_and_tracks_reads_per_person():
    tag = uuid.uuid4().hex[:8]
    user = f"bil-{tag}"
    with owner() as c:
        me, other, grp = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        c.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, %s, 'evren')",
                  (me, user, f"Bildirim {tag}"))
        c.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, source) VALUES (%s, %s, 'Başkası', 'evren')",
                  (other, f"baska-{tag}"))
        c.execute("INSERT INTO ref.sys_user_group (sys_id, name, source) VALUES (%s, %s, 'evren')", (grp, f"Grup {tag}"))
        c.execute('INSERT INTO ref.sys_user_grmember ("group", "user") VALUES (%s, %s)', (grp, me))
        mine = note(c, "user", me, f"Bana {tag}", "warning")
        to_group = note(c, "group", grp, f"Grubuma {tag}")
        to_role = note(c, "role", "evren_reader", f"Rolüme {tag}", "critical")
        theirs = note(c, "user", other, f"Başkasına {tag}")
    with make_client(("evren_reader",), user) as cl:
        items = [n for n in cl.get("/api/v1/notifications").json()["items"] if tag in n["title"]]
        assert {n["sys_id"] for n in items} == {mine, to_group, to_role}
        assert {n["via"] for n in items} == {"user", "group", "role"} and not any(n["read"] for n in items)
        assert cl.get("/api/v1/notifications/count").json()["critical_unread"] >= 1
        r = cl.post("/api/v1/notifications/read", json={"ids": [mine, theirs]}, headers=CSRF)
        assert r.status_code == 200 and r.json()["marked"] == 1          # başkasınınki atlanır
        unread = [n["sys_id"] for n in cl.get("/api/v1/notifications", params={"unread_only": True}).json()["items"]]
        assert mine not in unread and to_group in unread
        assert cl.post("/api/v1/notifications/read", json={"ids": [mine]}).status_code == 403   # CSRF
        cl.post("/api/v1/notifications/read-all", headers=CSRF)
        assert cl.get("/api/v1/notifications/count").json()["unread"] == 0
    # Rol bildirimini her alıcı ayrı okur: başka bir okuyucu için hâlâ okunmamış
    with make_client(("evren_reader",), f"diger-{tag}") as other_cl:
        items = {n["sys_id"]: n for n in other_cl.get("/api/v1/notifications").json()["items"]}
        assert to_role in items and not items[to_role]["read"] and mine not in items and to_group not in items


def test_routes_require_ops_admin_and_validate_targets(operator):
    with make_client(("evren_reader",), "bil-okuyucu") as reader:
        assert reader.get("/api/v1/notifications/routes").status_code == 403
    with make_client(("evren_data_steward",), "bil-veri") as steward:          # görür, değiştiremez
        assert steward.get("/api/v1/notifications/routes").status_code == 200
        assert steward.put("/api/v1/notifications/routes", headers=CSRF, json={
            "audience_kind": "role", "audience": "evren_operator", "channel": "email",
            "target": "ops@kurum.ornek"}).status_code == 403
    d = operator.get("/api/v1/notifications/routes").json()
    assert d["can_admin"] is True and any(r["value"] == "evren_operator" for r in d["roles"])
    bad = operator.put("/api/v1/notifications/routes", headers=CSRF, json={
        "audience_kind": "role", "audience": "evren_operator", "channel": "email", "target": "kotu adres"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "eposta_gecersiz"
    assert operator.put("/api/v1/notifications/routes", headers=CSRF, json={
        "audience_kind": "role", "audience": "evren_yok", "channel": "email", "target": "a@b.cd"}).status_code == 422
    tag = uuid.uuid4().hex[:6]
    body = {"audience_kind": "role", "audience": "evren_operator", "channel": "email", "target": f"ops-{tag}@kurum.ornek",
            "min_severity": "warning"}
    assert operator.put("/api/v1/notifications/routes", headers=CSRF, json=body).status_code == 200
    assert operator.put("/api/v1/notifications/routes", headers=CSRF, json={**body, "active": False}).status_code == 200
    route = next(r for r in operator.get("/api/v1/notifications/routes").json()["routes"]
                 if r["target"] == body["target"])
    assert route["active"] is False and route["audience_label"] == "Platform operatörü"
    with owner() as c:
        assert c.execute("SELECT count(*) FROM audit.security_event WHERE action = 'notify_route_update' "
                         "AND detail->>'target' = %s", (body["target"],)).fetchone()[0] == 2


def test_teams_target_must_be_one_the_worker_reports(operator):
    with owner() as c:
        c.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind, details)
                     VALUES (%s, now(), now(), 30, 'governance', %s)""",
                  (f"test-bil-{uuid.uuid4().hex[:6]}",
                   psycopg.types.json.Jsonb({"notify": {"email": "smtp", "teams": "açık", "teams_targets": ["platform"],
                                                        "pending": 0, "failed_24h": 0}})))
    ch = operator.get("/api/v1/notifications/routes").json()["channels"]
    assert ch["live"] and ch["teams_targets"] == ["platform"] and "https" not in str(ch)
    r = operator.put("/api/v1/notifications/routes", headers=CSRF, json={
        "audience_kind": "role", "audience": "evren_operator", "channel": "teams", "target": "yok"})
    assert r.status_code == 422 and "platform" in r.json()["error"]["message"]
    assert operator.put("/api/v1/notifications/routes", headers=CSRF, json={
        "audience_kind": "role", "audience": "evren_operator", "channel": "teams", "target": "platform"}).status_code == 200


def test_test_notification_is_created_and_audited(operator):
    r = operator.post("/api/v1/notifications/test", headers=CSRF, json={"audience_kind": "role", "audience": "evren_operator"})
    assert r.status_code == 202
    items = operator.get("/api/v1/notifications").json()["items"]
    assert items[0]["kind"] == "test" and items[0]["title"] == "Sınama bildirimi"
    with make_client(("evren_reader",), "bil-sinayan") as reader:
        assert reader.post("/api/v1/notifications/test", headers=CSRF,
                           json={"audience_kind": "role", "audience": "evren_reader"}).status_code == 403


def alert(state="firing", severity="warning"):
    rid = f"test_api_{uuid.uuid4().hex[:10]}"
    with owner() as c:
        return c.execute(
            """INSERT INTO ops.alert (rule, subject, severity, state, title, detail, audience, fired_at)
               VALUES (%s, '', %s, %s, 'Sınama uyarısı', 'Ayrıntı', '{evren_operator}',
                       CASE WHEN %s = 'firing' THEN now() END) RETURNING sys_id""",
            (rid, severity, state, state)).fetchone()[0]


def test_alerts_list_and_ack_once_with_reason(operator):
    a = alert(severity="critical")
    pending = alert(state="pending")
    res = operator.get("/api/v1/ops/alerts").json()
    item = next(i for i in res["items"] if i["sys_id"] == a)
    assert item["state_label"] == "Etkin" and item["severity_label"] == "Kritik" and res["can_ack"]
    assert pending not in {i["sys_id"] for i in res["items"]}               # bekleyen gösterilmez
    assert {r["id"] for r in res["rules"]} >= {"servis_yanit_vermiyor", "denetim_muhru_bozuk"}
    att = {i["id"] for i in operator.get("/api/v1/admin/attention").json()["items"]}
    assert "alerts_critical" in att
    assert operator.post(f"/api/v1/ops/alerts/{a}/ack", headers=CSRF, json={"note": "kısa"}).status_code == 422
    ok = operator.post(f"/api/v1/ops/alerts/{a}/ack", headers=CSRF, json={"note": "Kafka düğümü yeniden başlatılıyor."})
    assert ok.status_code == 200
    assert operator.post(f"/api/v1/ops/alerts/{a}/ack", headers=CSRF,
                         json={"note": "İkinci onay denemesi yapılıyor."}).status_code == 409
    assert operator.post(f"/api/v1/ops/alerts/{pending}/ack", headers=CSRF,
                         json={"note": "Ateşlenmemiş uyarı onaylanmaz."}).status_code == 409
    acked = next(i for i in operator.get("/api/v1/ops/alerts").json()["items"] if i["sys_id"] == a)
    assert acked["ack"]["by"] == "dev:operator-bildirim"
    with make_client(("evren_data_steward",), "bil-veri2") as steward:
        assert steward.get("/api/v1/ops/alerts").status_code == 200
        assert steward.post(f"/api/v1/ops/alerts/{alert()}/ack", headers=CSRF,
                            json={"note": "Veri sorumlusu onaylayamaz."}).status_code == 403
    with make_client(("evren_reader",), "bil-okuyucu2") as reader:
        assert reader.get("/api/v1/ops/alerts").status_code == 403


def test_metrics_endpoint_is_closed_without_token_and_requires_bearer(operator, tmp_path):
    assert operator.get("/metrics").status_code == 404
    tok = tmp_path / "metrik"
    tok.write_text("m" * 40)
    settings = Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_DEV_ROLES": "evren_reader",
                                  "EVREN_DB_POOL_SIZE": "2", "EVREN_METRICS_TOKEN_FILE": str(tok)})
    with TestClient(create_app(settings)) as c:
        c.get("/api/v1/me")
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer yanlis"}).status_code == 401
        r = c.get("/metrics", headers={"Authorization": f"Bearer {'m' * 40}"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
        assert 'evren_http_requests_total{method="GET",route="/api/v1/me",status="2xx"} 1' in r.text
        assert "evren_db_up 1" in r.text and "evren_alerts_firing{severity=\"critical\"}" in r.text
        assert "dev:" not in r.text                                           # kişi etiketi yok
    short = Path(tmp_path / "kisa")
    short.write_text("abc")
    with pytest.raises(ConfigError, match="24 karakter"):
        Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_METRICS_TOKEN_FILE": str(short)})
