"""API: güvenlik başlıkları, statik sınırlar, okuma uç noktaları, yetki, sayfalama, kuyruk kararı."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from evren_api import create_app
from evren_api.config import Settings
from evren_core.metadata import load_metadata
from evren_reconciler import ReconcilerStore

from .conftest import CSRF, REPO, make_client
from .scenarios import make_review_item


def test_security_headers_static_scope_and_no_cdn_docs(client):
    r = client.get("/")
    assert r.status_code == 200 and "Evren CMDB" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY"
    api = client.get("/api/v1/me")
    assert api.headers["cache-control"] == "no-store" and "content-security-policy" in api.headers
    for path in ("/content/architecture.yaml", "/../README.md", "/%2e%2e/README.md", "/tests/test_web_smoke.py",
                 "/docs", "/redoc"):
        assert client.get(path).status_code == 404, path
    assert client.get("/api/v1/openapi.json").json()["info"]["title"] == "Evren CMDB API"
    assert client.get("/api/v1/yok").json()["error"]["code"] == "bulunamadi"


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}


def test_me_reports_roles_and_capabilities(client, reader):
    me = client.get("/api/v1/me").json()
    assert {r["name"] for r in me["roles"]} == {"evren_reader", "evren_match_reviewer"}
    assert me["can"] == {"review_match": True, "read_ci": True, "create_ci": False,  # CI açma: Konfigürasyon analisti
                         "attest": False,  # sahiplik onayı: CI sahibi rolü (ADR-0027)
                         "retire": False,  # eskime kararı: Veri sorumlusu (ADR-0028)
                         "system": False,  # sistem durumu: Platform operatörü (ADR-0032)
                         "replica": False, "replica_admin": False,  # ServiceNow replikası (ADR-0042)
                         "relate": False, "schema_draft": False, "schema_approve": False, "connector_read": False,
                         "connector_admin": False, "anomaly_review": False, "iam_admin": False, "audit_read": False,
                         "iam_read": False, "task_admin": False}
    assert me["sys_user"] == {"mapped": False, "name": None}  # test kullanıcısının sys_user kaydı yok
    assert reader.get("/api/v1/me").json()["can"]["review_match"] is False


def test_ci_list_pages_through_everything_without_duplicates(client):
    first = client.get("/api/v1/ci", params={"limit": 7}).json()
    total = first["count"]
    seen, page = [], first
    while True:
        seen += [i["sys_id"] for i in page["items"]]
        if not page["next_cursor"]:
            break
        page = client.get("/api/v1/ci", params={"limit": 7, "cursor": page["next_cursor"]}).json()
    assert len(seen) == len(set(seen)) == total
    assert client.get("/api/v1/ci", params={"cursor": "bozuk"}).json()["error"]["code"] == "gecersiz_imlec"


def test_ci_filters_use_roles_not_products(client):
    unmon = client.get("/api/v1/ci", params={"scope": "izlenmeyen", "limit": 1000}).json()
    assert unmon["count"] == client.get("/api/v1/summary").json()["unmonitored"] > 0
    assert all(not i["coverage"]["monitoring"] for i in unmon["items"])
    hit = client.get("/api/v1/ci", params={"q": "ist-db-01"}).json()
    assert hit["count"] >= 1 and all("ist-db-01" in (i["name"] or "").lower() for i in hit["items"])
    assert client.get("/api/v1/ci", params={"scope": "bilinmeyen"}).status_code == 400
    assert client.get("/api/v1/ci", params={"class": "yok_sinif"}).status_code == 400
    servers = client.get("/api/v1/ci", params={"class": "cmdb_ci_server", "limit": 1000}).json()
    assert {i["class"] for i in servers["items"]} <= {"cmdb_ci_win_server", "cmdb_ci_linux_server", "cmdb_ci_server",
                                                      "cmdb_ci_esx_server"}


def test_ci_detail_has_provenance_and_rejected_attempts(client):
    item = client.get("/api/v1/ci", params={"scope": "yetkisiz"}).json()["items"][0]
    ci = client.get(f"/api/v1/ci/{item['sys_id']}").json()
    name = next(a for a in ci["attributes"] if a["attribute"] == "name")
    assert name["source"] and name["role"] and name["reason"].startswith(("Uzlaştırma", "Veri tazeleme", "Kural yok"))
    assert ci["rejected"] and ci["rejected"][0]["attempts"]
    assert ci["links"] and all(link["role"] for link in ci["links"])
    assert client.get("/api/v1/ci/" + "0" * 32).status_code == 404
    assert client.get("/api/v1/ci/kisa").json()["error"]["code"] == "gecersiz_sys_id"
    hist = client.get(f"/api/v1/ci/{item['sys_id']}/history").json()["items"]
    assert hist and {"attribute", "source", "reason"} <= set(hist[0])


def test_role_without_ci_read_is_denied_and_audited():
    with make_client(("evren_iam_admin",), "yetki-yoneticisi") as c:
        r = c.get("/api/v1/ci")
        assert r.status_code == 403 and r.json()["error"]["code"] == "yetki_yok"
    with psycopg.connect() as conn:
        row = conn.execute("SELECT decision, action FROM audit.security_event WHERE principal = 'dev:yetki-yoneticisi' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ("deny", "read:cmdb_ci")


def test_sources_and_controls(client):
    src = {s["name"]: s for s in client.get("/api/v1/sources").json()["sources"]}
    assert src["BMC"]["role"] == "Discovery" and src["BMC"]["product"]["vendor"] == "BMC"
    assert src["NNMi"]["breaker"] == "open" and src["NNMi"]["runs"][0]["completed"] is False
    controls = {c["id"]: c for c in client.get("/api/v1/controls").json()}
    assert controls["CMDB-01"]["provenance"]["depends_on"] == "Zabbix"
    assert all(c["status"] in ("pass", "fail", "inconclusive") for c in controls.values())
    rules = client.get("/api/v1/rules").json()
    assert all(set(g["priorities"]) <= {"Discovery", "Virtualization", "Network", "Monitoring", "EndpointSecurity",
                                        "ITSM", "Manual", "Platform"} for g in rules["reconciliation"])


def test_connector_credentials_are_visible_only_with_connector_read_and_never_secret(client):
    """U5.3 (0018): kimlik listesi ve çalışmayı açan kimlik yalnızca data_source/connector_read ile; sır yok."""
    from evren_core.credentials import new_token, token_hint, token_sha256

    token = new_token()
    name = f"apitest-{uuid.uuid4().hex[:8]}"
    run_id = uuid.uuid4()
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO ingest.connector_credential (name, data_source, token_sha256, token_hint,
                            created_by, expires_at) VALUES (%s, 'Zabbix', %s, %s, 'api-testi', now() + interval '1 day')""",
                     (name, token_sha256(token), token_hint(token)))
        conn.execute("""INSERT INTO ingest.source_run (run_id, data_source, mode, started_at, opened_by, accepted_count,
                            reported_count, reported_completed, notes)
                        VALUES (%s, 'Zabbix', 'snapshot', now() + interval '1 hour', %s, 5, 5, false,
                                'kaynak veya geçit erişimi: durum 503')""", (run_id, name))
    try:
        plain = client.get("/api/v1/sources").json()
        zbx = next(s for s in plain["sources"] if s["name"] == "Zabbix")
        assert plain["can"]["connector_read"] is False and zbx["credentials"] is None
        assert "opened_by" not in zbx["runs"][0] and zbx["runs"][0]["mode"] == "snapshot"
        assert zbx["runs"][0]["via_gateway"] is True and zbx["runs"][0]["accepted"] == 5
        assert zbx["runs"][0]["notes"] == "kaynak veya geçit erişimi: durum 503"
        with make_client(("evren_reader", "evren_data_steward"), "veri-sorumlusu") as steward:
            doc = steward.get("/api/v1/sources").json()
        zbx = next(s for s in doc["sources"] if s["name"] == "Zabbix")
        assert doc["can"]["connector_read"] is True and zbx["runs"][0]["opened_by"] == name
        cred = next(c for c in zbx["credentials"] if c["name"] == name)
        assert cred["state"] == "active" and cred["created_by"] == "api-testi" and cred["token_hint"] == token_hint(token)
        text = str(doc)
        assert token not in text and token_sha256(token) not in text and "token_sha256" not in text
    finally:
        with psycopg.connect(autocommit=True) as conn:
            conn.execute("DELETE FROM ingest.source_run WHERE run_id = %s", (run_id,))
            conn.execute("DELETE FROM ingest.connector_credential WHERE name = %s", (name,))


def test_decision_requires_csrf_role_and_is_idempotent(client, reader):
    item, target, key = make_review_item()
    url = f"/api/v1/match-queue/{item}/decision"
    body = {"decision": "merge", "ci_sys_id": target, "idempotency_key": f"t-{uuid.uuid4().hex}", "note": "seri doğrulandı"}
    assert client.post(url, json=body).json()["error"]["code"] == "csrf"
    assert client.post(url, json=body, headers={**CSRF, "Origin": "https://kotu.example"}).json()["error"]["code"] == "csrf"
    assert reader.post(url, json=body, headers=CSRF).status_code == 403
    detail = client.get(f"/api/v1/match-queue/{item}").json()
    assert detail["kind"] == "review" and {c["sys_id"] for c in detail["candidates"]} >= {target}

    ok = client.post(url, json=body, headers=CSRF)
    assert ok.status_code == 200 and ok.json()["status"] == "accepted" and ok.json()["replayed"] is False
    again = client.post(url, json=body, headers=CSRF).json()
    assert again["replayed"] is True and again["status"] == "accepted"
    conflict = client.post(url, json={**body, "note": "başka"}, headers=CSRF)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_cakismasi"
    second = client.post(url, json={**body, "idempotency_key": f"t-{uuid.uuid4().hex}"}, headers=CSRF)
    assert second.status_code == 409 and second.json()["error"]["code"] == "karar_verilmis"

    # Uzlaştırıcı kararı uygular: bağ insan kararıyla kurulur, CI uzlaştırılır
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        results = ReconcilerStore(conn, load_metadata(REPO / "metadata")).apply_decisions()
        assert any(r.outcome == "decision_merged" and r.ci_sys_id == target for r in results)
        link = conn.execute("SELECT ci_sys_id, method, decided_by FROM ingest.identity_link "
                            "WHERE data_source = 'Zabbix' AND source_key = %s AND active", (key,)).fetchone()
    assert link == (target, "manual", "dev:test-user")
    assert client.get(f"/api/v1/match-queue/{item}").json()["decision"]["applied"] is True


@pytest.mark.parametrize("body,code", [
    ({"decision": "merge", "idempotency_key": "kisa"}, 422),
    ({"decision": "sil", "idempotency_key": "abcdefgh1"}, 422),
    ({"decision": "merge", "ci_sys_id": "X" * 32, "idempotency_key": "abcdefgh2"}, 422),
    ({"decision": "defer", "idempotency_key": "abcdefgh3", "beklenmeyen": 1}, 422),
])
def test_decision_body_is_strictly_validated(client, body, code):
    r = client.post(f"/api/v1/match-queue/{uuid.uuid4().hex}/decision", json=body, headers=CSRF)
    assert r.status_code == code and r.json()["error"]["code"] == "gecersiz_istek"
    assert "X" * 32 not in r.text  # girdi değerleri yanıta yansıtılmaz


def test_api_db_role_cannot_write_golden_record_or_links():
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_api")
        for stmt in ("UPDATE cmdb.cmdb_ci SET name = 'x'", "INSERT INTO ingest.identity_link (data_source, source_key, "
                     "ci_sys_id, method, decided_by) VALUES ('Zabbix', 'x', 'x', 'manual', 'x')",
                     "UPDATE ingest.match_candidate SET kind = 'review'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)


def test_dev_container_mode_is_explicit_and_filters_clients():
    """ADR-0016: geliştirme konteyneri yalnızca açıkça istenirse tüm arayüzlerde dinler; genel adresten gelen
    istekler reddedilir (compose portu zaten yalnız 127.0.0.1'e yayınlar)."""
    from evren_api.config import ConfigError

    all_interfaces = "0.0.0.0"  # noqa: S104 - sınanan yapılandırma
    with pytest.raises(ConfigError):
        Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_HOST": all_interfaces})
    with pytest.raises(ConfigError):
        Settings.from_env({"EVREN_AUTH_MODE": "entra", "EVREN_DEV_CONTAINER": "1"})
    settings = Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_DEV_CONTAINER": "1", "EVREN_HOST": all_interfaces,
                                  "EVREN_DB_POOL_SIZE": "1"})
    assert settings.dev_container is True
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings), client=("8.8.8.8", 50000)) as public:
        r = public.get("/healthz")
        assert r.status_code == 403 and r.json()["error"]["code"] == "erisim_yok"
    with TestClient(create_app(settings), client=("172.18.0.1", 50000)) as bridge:  # compose köprü ağı (ana makine)
        assert bridge.get("/healthz").status_code == 200
