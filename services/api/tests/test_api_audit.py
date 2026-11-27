"""Denetim izi API'si (U8.8, ADR-0039): istek kimliği ve kaynak IP, süzgeç ve sayfalama, dışa aktarma (CSV formül
koruması, aktarmanın kendisi denetlenir), mühür doğrulama (kurcalama yakalanır), inceleme kaydı, yetki ayrımı.

Mühürleri görev çalışanının mühürleyicisi üretir (``evren_governance.audit_seal``). Bu modül, mühürleme hiç başlamamışsa
güvenlik akışına üç saat önceki bir kayıt ekler (mühürleme öncesi geçmiş); başka geriye tarihli kayıt yazılmaz.
Kurcalama testi kendi eklediği satırı tablonun sahibi rolüyle geri alır (yalnızca test). Veriler sentetiktir."""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from evren_api import create_app
from evren_api.config import ConfigError, Settings
from evren_governance.audit_seal import seal_streams

from .conftest import CSRF, make_client

AUDITOR = ("evren_auditor",)


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


@pytest.fixture(scope="module")
def auditor():
    with make_client(AUDITOR, "denetci-test") as c:
        yield c


@pytest.fixture(scope="module")
def sealed():
    """En az bir mühürlü dönem: mühürleme başlamamışsa geçmişe bir kayıt eklenir, sonra mühürleyici çalışır."""
    with psycopg.connect(autocommit=True) as conn:
        if conn.execute("SELECT count(*) FROM audit.seal WHERE stream = 'security'").fetchone()[0] == 0:
            conn.execute("""INSERT INTO audit.security_event (at, principal, action, decision, detail)
                            VALUES (now() - interval '3 hours', 'test:muhur-oncesi', 'login', 'allow', '{}')""")
        conn.execute("SET ROLE evren_app_governance")
        seal_streams(conn)
        conn.execute("RESET ROLE")
        row = conn.execute("""SELECT period_start, period_end FROM audit.seal WHERE stream = 'security'
                               ORDER BY period_start DESC LIMIT 1""").fetchone()
    assert row is not None, "güvenlik akışında mühür olmalı"
    return row


def deny_events(user: str, n: int) -> list[str]:
    """Okuyucu ``n`` kez denetim iznine takılır; her ret bir güvenlik olayıdır. İstek kimlikleri döner."""
    rids = []
    with TestClient(create_app(Settings.from_env({"EVREN_AUTH_MODE": "dev", "EVREN_DEV_ROLES": "evren_reader",
                                                  "EVREN_DEV_USER": user, "EVREN_DB_POOL_SIZE": "2"})),
                    client=("192.0.2.10", 50123)) as c:
        for _ in range(n):
            r = c.get("/api/v1/audit/events")
            assert r.status_code == 403
            rids.append(r.headers["x-request-id"])
    return rids


def test_every_response_carries_a_request_id(auditor):
    a, b = auditor.get("/api/v1/audit/catalog"), auditor.get("/api/v1/audit/catalog")
    assert a.status_code == 200 and len(a.headers["x-request-id"]) == 32
    assert a.headers["x-request-id"] != b.headers["x-request-id"]
    cat = a.json()
    assert {c["value"] for c in cat["categories"]} >= {"access", "iam", "cmdb", "audit"} and cat["can_review"] is True


def test_deny_event_records_source_ip_and_request_id_and_filters_find_it(auditor):
    user = f"aud-{uuid.uuid4().hex[:8]}"
    rids = deny_events(user, 3)
    r = auditor.get("/api/v1/audit/events", params={"actor": user})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 3
    e = body["items"][0]
    assert e["outcome"] == "deny" and e["category"] == "access" and e["action_label"].startswith("Yetki reddi")
    assert e["client_ip"] == "192.0.2.10" and e["request_id"] in rids
    # İstek kimliğiyle tek olay; IP ve kategori süzgeci; izin sonucu yok
    one = auditor.get("/api/v1/audit/events", params={"request_id": rids[0]}).json()["items"]
    assert [x["request_id"] for x in one] == [rids[0]]
    assert auditor.get("/api/v1/audit/events", params={"actor": user, "ip": "192.0.2.10",
                                                       "category": "access"}).json()["total"] == 3
    assert auditor.get("/api/v1/audit/events", params={"actor": user, "outcome": "allow"}).json()["total"] == 0
    assert auditor.get("/api/v1/audit/events", params={"actor": user, "category": "cmdb"}).json()["total"] == 0


def test_cursor_pagination_has_no_gaps_or_duplicates(auditor):
    user = f"aud-{uuid.uuid4().hex[:8]}"
    deny_events(user, 5)
    first = auditor.get("/api/v1/audit/events", params={"actor": user, "limit": 2}).json()
    assert len(first["items"]) == 2 and first["next"]
    keys = [e["key"] for e in first["items"]]
    cursor = first["next"]
    while cursor:
        page = auditor.get("/api/v1/audit/events", params={"actor": user, "limit": 2, "cursor": cursor}).json()
        assert page["total"] is None  # sayım yalnızca ilk sayfada
        keys += [e["key"] for e in page["items"]]
        cursor = page["next"]
    assert len(keys) == 5 and len(set(keys)) == 5
    assert auditor.get("/api/v1/audit/events", params={"cursor": "bozuk"}).status_code == 422


def test_event_detail_shows_seal_and_related_events(auditor):
    user = f"aud-{uuid.uuid4().hex[:8]}"
    deny_events(user, 1)
    e = auditor.get("/api/v1/audit/events", params={"actor": user}).json()["items"][0]
    d = auditor.get(f"/api/v1/audit/events/{e['source']}/{e['id']}")
    assert d.status_code == 200 and d.json()["key"] == e["key"]
    assert d.json()["seal"] is None  # içinde bulunulan saat henüz mühürlenmedi
    assert isinstance(d.json()["related"], list)
    assert auditor.get("/api/v1/audit/events/security/999999999").status_code == 404
    assert auditor.get("/api/v1/audit/events/yok/1").status_code == 422
    assert auditor.get(f"/api/v1/audit/events/security/{2**70}").status_code == 422  # bigint taşması 500 değil


def test_ci_changes_and_task_events_are_in_the_same_trail(auditor):
    body = auditor.get("/api/v1/audit/events", params={"source": "ci", "limit": 1}).json()
    if body["items"]:  # demo verisi uzlaştırmayla CI değişikliği üretir
        e = body["items"][0]
        assert e["category"] == "cmdb" and e["object"].startswith("ci:") and e["object_link"].startswith("#/ci/")
        assert "attribute" in e["detail"] and e["masked"] is False  # Denetçi kısıtlı alanları da görür
    s = auditor.get("/api/v1/audit/summary").json()
    assert {x["value"] for x in s["by_source"]} == {"security", "ci", "task"} and s["events"] >= s["denied"]


def test_invalid_ranges_are_rejected(auditor):
    now = datetime.now(UTC)
    assert auditor.get("/api/v1/audit/events", params={"since": now.isoformat(),
                                                       "until": (now - timedelta(hours=1)).isoformat()}).status_code == 422
    assert auditor.get("/api/v1/audit/events", params={"since": (now - timedelta(days=400)).isoformat()}).status_code == 422
    assert auditor.get("/api/v1/audit/events", params={"request_id": "kısa"}).status_code == 422
    r = auditor.post("/api/v1/audit/seals/verify", headers=CSRF,
                     json={"since": (now - timedelta(days=100)).isoformat(), "until": now.isoformat()})
    assert r.status_code == 422 and r.json()["error"]["code"] == "donem_cok_uzun"


def test_export_csv_neutralises_formulas_and_is_itself_audited(auditor):
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO audit.security_event (principal, action, resource, decision, detail)
                        VALUES (%s, 'login', %s, 'deny', '{"reason": "test"}')""",
                     (f"=HYPERLINK(\"http://kotu\")-{tag}", f"+cmd|{tag}"))
    r = auditor.get("/api/v1/audit/export", params={"q": tag, "format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-evren-truncated"] == "0"
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿")), delimiter=";"))
    assert rows[0][0] == "Zaman (UTC)" and len(rows) == 2
    assert rows[1][3].startswith("'=HYPERLINK") and rows[1][7].startswith("'+cmd")
    nd = auditor.get("/api/v1/audit/export", params={"q": tag, "format": "ndjson"})
    line = json.loads(nd.text.splitlines()[0])
    assert line["actor"].startswith("=HYPERLINK") and set(line) >= {"at", "source", "action", "request_id"}
    logged = auditor.get("/api/v1/audit/events", params={"action": "audit_export", "actor": "denetci-test"}).json()
    detail = logged["items"][0]["detail"]
    # İkinci aktarmada ilk aktarmanın kendi olayı da aramayla eşleşir: aktarmalar da izdedir
    assert detail["q"] == tag and detail["format"] == "ndjson" and detail["count"] == 2 == len(nd.text.splitlines())


def test_integrity_lists_streams_and_last_seal(auditor, sealed):
    r = auditor.get("/api/v1/audit/integrity")
    assert r.status_code == 200
    streams = {s["stream"]: s for s in r.json()["streams"]}
    assert set(streams) == {"security", "ci", "task"}
    assert streams["security"]["seals"] >= 1 and streams["security"]["sealed_until"]
    seals = auditor.get("/api/v1/audit/seals", params={"stream": "security", "limit": 5}).json()["items"]
    assert seals and len(seals[0]["chain"]) == 64


def test_verify_detects_a_record_slipped_into_a_sealed_period_and_review_must_say_so(auditor, sealed):
    start, end = sealed
    window = {"since": (start - timedelta(minutes=1)).isoformat(), "until": end.isoformat(), "stream": "security"}
    ok = auditor.post("/api/v1/audit/seals/verify", headers=CSRF, json=window)
    assert ok.status_code == 200, ok.text
    assert ok.json()["ok"] is True and ok.json()["checked"] >= 1
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO audit.security_event (at, principal, action, decision, detail)
                        VALUES (%s, %s, 'login', 'allow', '{}')""", (start + timedelta(minutes=1), f"kurcalama-{tag}"))
    try:
        bad = auditor.post("/api/v1/audit/seals/verify", headers=CSRF, json=window).json()
        assert bad["ok"] is False and bad["failed"] >= 1
        assert "kayıt eklenmiş" in bad["problems"][-1]["problem"]
        # Mühür sorunu varken "bulgu yok" kaydedilmez
        r = auditor.post("/api/v1/audit/reviews", headers=CSRF, json={
            "period_start": window["since"], "period_end": window["until"], "outcome": "no_findings",
            "note": "Haftalık inceleme: olağandışı bir şey yok.", "idempotency_key": key()})
        assert r.status_code == 422 and r.json()["error"]["code"] == "muhur_sorunu"
        att = {i["id"] for i in auditor.get("/api/v1/admin/attention").json()["items"]}
        assert "audit_seal_failed" in att
        integ = auditor.get("/api/v1/audit/integrity").json()
        assert integ["failed_30d"] >= 1 and integ["failed"][0]["stream"] == "security"
    finally:
        with psycopg.connect(autocommit=True) as conn:  # yalnızca test: sahibi rolüyle geri al
            conn.execute("ALTER TABLE audit.security_event DISABLE TRIGGER security_event_append_only")
            try:
                conn.execute("DELETE FROM audit.security_event WHERE principal = %s", (f"kurcalama-{tag}",))
            finally:
                conn.execute("ALTER TABLE audit.security_event ENABLE TRIGGER security_event_append_only")
    again = auditor.post("/api/v1/audit/seals/verify", headers=CSRF, json=window).json()
    assert again["ok"] is True
    logged = auditor.get("/api/v1/audit/events", params={"action": "audit_verify", "actor": "denetci-test"}).json()
    assert logged["total"] >= 3


def test_review_is_recorded_once_with_counts_and_replays(auditor, sealed):
    start, end = sealed
    payload = {"period_start": (start - timedelta(minutes=1)).isoformat(), "period_end": end.isoformat(),
               "outcome": "no_findings", "note": "Ret olayları ve yetki değişiklikleri gözden geçirildi; olağan.",
               "idempotency_key": key()}
    r = auditor.post("/api/v1/audit/reviews", headers=CSRF, json=payload)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["seals_ok"] is True and rec["seals_checked"] >= 1 and rec["events"] >= 1
    again = auditor.post("/api/v1/audit/reviews", headers=CSRF, json=payload)
    assert again.status_code == 201 and again.json()["replayed"] is True and again.json()["id"] == rec["id"]
    clash = auditor.post("/api/v1/audit/reviews", headers=CSRF, json={**payload, "note": "Başka bir not yazıldı."})
    assert clash.status_code == 409
    listed = auditor.get("/api/v1/audit/reviews").json()
    assert listed["items"][0]["id"] == rec["id"] and listed["due"] is False
    assert listed["items"][0]["outcome_label"] == "Bulgu yok"
    short = auditor.post("/api/v1/audit/reviews", headers=CSRF, json={**payload, "note": "kısa", "idempotency_key": key()})
    assert short.status_code == 422
    future = auditor.post("/api/v1/audit/reviews", headers=CSRF, json={
        **payload, "period_end": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "idempotency_key": key()})
    assert future.status_code == 422


@pytest.mark.parametrize(("roles", "user"), [(("evren_reader",), "aud-okuyucu"),
                                             (("evren_admin",), "aud-platform"),
                                             (("evren_reader", "evren_config_manager"), "aud-konf")])
def test_only_the_auditor_reads_or_reviews_the_trail(roles, user):
    with make_client(roles, user) as c:
        for path in ("/api/v1/audit/catalog", "/api/v1/audit/events", "/api/v1/audit/export", "/api/v1/audit/integrity",
                     "/api/v1/audit/seals", "/api/v1/audit/reviews", "/api/v1/audit/summary"):
            assert c.get(path).status_code == 403, path
        now = datetime.now(UTC)
        assert c.post("/api/v1/audit/seals/verify", headers=CSRF, json={
            "since": (now - timedelta(days=1)).isoformat(), "until": now.isoformat()}).status_code == 403
        assert c.post("/api/v1/audit/reviews", headers=CSRF, json={
            "period_start": (now - timedelta(days=1)).isoformat(), "period_end": now.isoformat(),
            "outcome": "findings", "note": "Yetkisiz inceleme denemesi.", "idempotency_key": key()}).status_code == 403


def test_csrf_header_is_required_for_verify(auditor):
    now = datetime.now(UTC)
    r = auditor.post("/api/v1/audit/seals/verify", json={"since": (now - timedelta(days=1)).isoformat(),
                                                          "until": now.isoformat()})
    assert r.status_code == 403


def test_trusted_proxies_setting_is_validated():
    base = {"EVREN_AUTH_MODE": "dev"}
    s = Settings.from_env({**base, "EVREN_TRUSTED_PROXIES": "10.20.0.0/16, 192.0.2.5"})
    assert s.trusted_proxies == ("10.20.0.0/16", "192.0.2.5/32")
    assert Settings.from_env(base).trusted_proxies == ()
    for bad in ("0.0.0.0/0", "vekil.kurum.local", "::/0"):
        with pytest.raises(ConfigError, match="EVREN_TRUSTED_PROXIES"):
            Settings.from_env({**base, "EVREN_TRUSTED_PROXIES": bad})
