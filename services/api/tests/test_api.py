"""API: güvenlik başlıkları, statik sınırlar, okuma uç noktaları, yetki, sayfalama, kuyruk kararı."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from evren_core.envelope import IngestRecord
from evren_core.metadata import load_metadata
from evren_reconciler import ReconcilerStore

from .conftest import CSRF, REPO, make_client


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
    assert me["can"] == {"review_match": True, "read_ci": True}
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


def _make_review_item() -> tuple[str, str, str]:
    """Kendi mükerrer senaryosu: iki SN kaydı aynı adla, izleme kaydı ad ile ikisine de uyar → inceleme."""
    md = load_metadata(REPO / "metadata")
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    now = datetime.now(UTC)
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, md)
        sn = store.start_run(uuid.uuid4(), "ServiceNow")
        for sid, serial in ((a, f"CZJ{tag}A"), (b, f"CZJ{tag}B")):
            store.import_record(IngestRecord(data_source="ServiceNow", run_id=sn, source_key=sid,
                                             class_hint="cmdb_ci_linux_server",
                                             attributes={"name": f"dup-{tag}", "serial_number": serial}, observed_at=now), sid)
        zb = store.start_run(uuid.uuid4(), "Zabbix")
        res = store.apply(IngestRecord(data_source="Zabbix", run_id=zb, source_key=f"z-{tag}",
                                       class_hint="cmdb_ci_linux_server", attributes={"name": f"dup-{tag}"}, observed_at=now))
        assert res.outcome == "review"
        item = conn.execute("SELECT sys_id FROM ingest.match_candidate WHERE source_key = %s AND status = 'open'",
                            (f"z-{tag}",)).fetchone()[0]
    return item, a, f"z-{tag}"


def test_decision_requires_csrf_role_and_is_idempotent(client, reader):
    item, target, key = _make_review_item()
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
