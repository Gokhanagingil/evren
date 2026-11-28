"""Platform uyarıları, bildirim merkezi ve metrikler (U8.9, ADR-0040): kural doğrulaması, sinyal değerlendirmesi,
uyarı yaşam döngüsü, görev ve uyarı bildirimleri (tırmanma dahil), teslim hedefleri, ileti metni (enjeksiyon yok),
Prometheus metin biçimi."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evren_core.metadata import load_metadata
from evren_core.metrics import Counter, Gauge, Histogram, Registry, render_samples
from evren_core.notify import (
    Recipient,
    Route,
    TaskInfo,
    alert_drafts,
    delivery_targets,
    email_message,
    safe_link,
    task_drafts,
    teams_card,
    valid_email,
)
from evren_core.ops import AlertRule, Beat, OpsSnapshot, evaluate, load_rules, next_state, validate_alerts

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)


def rule(signal: str, **kw) -> AlertRule:
    return AlertRule(id=f"k_{signal}", signal=signal, title=kw.pop("title", "{subject} sorunu ({value})"),
                     severity=kw.pop("severity", "warning"), for_minutes=kw.pop("for_minutes", 0),
                     audience=("evren_operator",), **kw)


# ------------------------------------------------------------------------------------------------- kurallar
def test_repository_rules_are_valid_and_product_free():
    md = load_metadata(REPO / "metadata")
    rules = load_rules(md.alerts)
    assert {r.signal for r in rules} >= {"service_down", "kafka_lag", "outbox_backlog", "breaker_open",
                                         "audit_seal_failed"}
    assert all(r.audience and r.title for r in rules)


@pytest.mark.parametrize(("bad", "needle"), [
    ({"id": "x", "signal": "service_down"}, "kimlik"),
    ({"signal": "yok"}, "bilinmeyen sinyal"),
    ({"signal": "kafka_lag"}, "eşik"),
    ({"severity": "acil"}, "önem"),
    ({"for_minutes": 5000}, "for_minutes"),
    ({"audience": ["operator"]}, "audience"),
    ({"kinds": ["web"]}, "servis türü"),
    ({"title": "Zabbix kapalı"}, "ürün adı"),
])
def test_invalid_rules_are_rejected(bad, needle):
    base = {"id": "kural_bir", "signal": "service_down", "title": "{subject} yanıt vermiyor", "severity": "critical",
            "for_minutes": 5, "audience": ["evren_operator"]}
    errors = validate_alerts({"rules": [{**base, **bad}]}, products=["Zabbix"])
    assert any(needle in e for e in errors), errors
    with pytest.raises(ValueError):
        load_rules({"rules": [{"id": "a"}]})


# ------------------------------------------------------------------------------------------------- değerlendirme
def beat(kind, seen_ago_s, *, stopped=False, poll=30, **details) -> Beat:
    seen = NOW - timedelta(seconds=seen_ago_s)
    return Beat(kind, f"{kind}-{seen_ago_s}", seen, poll, seen if stopped else None, details)


def test_service_down_only_when_latest_instance_is_stale_and_not_stopped():
    r = [rule("service_down")]
    snap = OpsSnapshot(NOW, beats=[beat("ingest", 600), beat("indexer", 20), beat("connector", 900, stopped=True),
                                   beat("requests", 900), beat("requests", 10)])
    conds = evaluate(snap, r)
    assert [c.subject for c in conds] == ["ingest"]
    assert conds[0].title.startswith("Alım tüketicisi sorunu") and "10" in conds[0].detail


def test_kafka_signals_and_kind_filter():
    r = [rule("kafka_lag", threshold=1000, kinds=("ingest",)), rule("kafka_unreachable")]
    snap = OpsSnapshot(NOW, beats=[beat("ingest", 5, kafka={"ok": True, "lag": 700}),
                                   beat("ingest", 6, kafka={"ok": True, "lag": 600}),
                                   beat("indexer", 5, kafka={"ok": True, "lag": 99999}),
                                   beat("gateway", 5, kafka={"ok": False})])
    got = {(c.rule.signal, c.subject): c.value for c in evaluate(snap, r)}
    assert got == {("kafka_lag", "ingest"): 1300, ("kafka_unreachable", "gateway"): 1}


def test_outbox_requests_breakers_tasks_and_seals():
    rules = [rule("outbox_backlog", threshold=10), rule("run_request_stuck", threshold=30), rule("breaker_open"),
             rule("task_delivery_failed", threshold=0), rule("audit_seal_lag", threshold=120),
             rule("audit_seal_failed")]
    snap = OpsSnapshot(NOW, beats=[beat("requests", 5, outbox_publisher=True), beat("governance", 5, failed=2)],
                       outbox_backlog=40, outbox_oldest=NOW - timedelta(minutes=25),
                       requests_waiting=[NOW - timedelta(minutes=45), NOW - timedelta(minutes=5)],
                       breakers_open=["Kaynak-B", "Kaynak-A"], seal_last_end=NOW - timedelta(hours=3), seal_failed_24h=1)
    got = sorted((c.rule.signal, c.subject, c.value) for c in evaluate(snap, rules))
    assert got == sorted([("outbox_backlog", "", 25.0), ("run_request_stuck", "", 45.0), ("breaker_open", "Kaynak-A", None),
                          ("breaker_open", "Kaynak-B", None), ("task_delivery_failed", "", 2),
                          ("audit_seal_lag", "", 180.0), ("audit_seal_failed", "", 1)])
    # Yayıncı yoksa (Kafka yapılandırılmamış geliştirme) birikme uyarısı yok; eşiğin altı da yok
    calm = OpsSnapshot(NOW, beats=[beat("requests", 5)], outbox_backlog=40, outbox_oldest=NOW - timedelta(hours=5),
                       requests_waiting=[NOW - timedelta(minutes=5)], seal_last_end=NOW - timedelta(minutes=30))
    assert evaluate(calm, rules) == []


@pytest.mark.parametrize(("state", "present", "since_min", "for_min", "expected"), [
    (None, False, None, 5, None),
    (None, True, None, 5, "pending"),
    (None, True, None, 0, "firing"),
    ("pending", True, 3, 5, None),
    ("pending", True, 5, 5, "firing"),
    ("pending", False, 3, 5, "resolved"),     # süre dolmadan kalktı: kimse rahatsız edilmez
    ("firing", True, 60, 5, None),
    ("firing", False, 60, 5, "resolved"),
    ("resolved", True, 60, 5, None),
])
def test_alert_lifecycle(state, present, since_min, for_min, expected):
    since = NOW - timedelta(minutes=since_min) if since_min is not None else None
    assert next_state(state, present, since, for_min, NOW) == expected


# ------------------------------------------------------------------------------------------------- bildirimler
TASK = TaskInfo("a" * 32, "EGT0001001", "Verilerinizi teyit edin", NOW, assignee_user="u" * 32, assignee_group="g" * 32,
                group_manager="m" * 32, ci_name="app-01")


def test_task_assignment_reminder_and_escalation():
    assigned = task_drafts(7, "created", TASK)
    assert [(d.kind, d.audience_kind, d.audience) for d in assigned] == [("task_assigned", "user", "u" * 32)]
    assert assigned[0].link == f"#/gorevler/{'a' * 32}" and "app-01" in assigned[0].body
    assert task_drafts(8, "reminded", TASK, due_text="30.11.2026")[0].body.endswith("Son tarih: 30.11.2026.")
    overdue = task_drafts(9, "overdue", TASK)
    assert [(d.kind, d.audience_kind, d.audience) for d in overdue] == [
        ("task_overdue", "user", "u" * 32), ("task_escalated", "user", "m" * 32)]
    # Grubun yöneticisi yoksa (veya sorumlu kendisiyse) tırmanma Konfigürasyon yöneticisi rolüne
    no_boss = TaskInfo(**{**TASK.__dict__, "group_manager": None})
    assert task_drafts(10, "overdue", no_boss)[1].audience == "evren_config_manager"
    # Sorumlusu olmayan görev
    orphan = TaskInfo(**{**TASK.__dict__, "assignee_user": None, "assignee_group": None})
    assert task_drafts(11, "created", orphan)[0].kind == "task_unassigned"
    assert task_drafts(12, "completed", TASK) == []
    # Aynı olay ve alıcı için anahtar aynı: ikinci üretim tekrar etmez
    assert task_drafts(7, "created", TASK)[0].dedup_key == assigned[0].dedup_key


def test_alert_drafts_go_to_each_role():
    fired = alert_drafts("x" * 32, "fired", title="Alım tüketicisi yanıt vermiyor", detail="Son nabız 9 dakika önce.",
                         advice="Günlüğü denetleyin.", severity="critical", audience=["evren_operator", "evren_auditor"])
    assert [d.audience for d in fired] == ["evren_operator", "evren_auditor"]
    assert fired[0].severity == "critical" and "Günlüğü" in fired[0].body
    resolved = alert_drafts("x" * 32, "resolved", title="T", detail="", advice="", severity="critical",
                            audience=["evren_operator"])
    assert resolved[0].kind == "alert_resolved" and resolved[0].severity == "info" and resolved[0].title.startswith("Çözüldü")
    assert alert_drafts("x" * 32, "acked", title="T", detail="", advice="", severity="info", audience=["r"]) == []


def test_delivery_targets_respect_channels_severity_and_external_tasks():
    routes = [Route("email", "ekip@kurum.local", "warning"), Route("teams", "platform", "info"),
              Route("teams", "tanimsiz", "info"), Route("email", "ekip@kurum.local", "info")]
    rcpt = Recipient(user_email="kisi@kurum.local", routes=routes)
    assert delivery_targets("user", "info", rcpt, email_on=True, teams_targets={"platform"}) == [
        ("email", "kisi@kurum.local"), ("teams", "platform"), ("email", "ekip@kurum.local")]
    assert delivery_targets("role", "info", Recipient(routes=routes[:1]), email_on=True, teams_targets=()) == []
    assert delivery_targets("user", "info", rcpt, email_on=False, teams_targets=()) == []
    # Görev ServiceNow'a teslim edildiyse kişiye e-posta yok (ServiceNow bildirir); yönlendirme yine çalışır
    ext = Recipient(user_email="kisi@kurum.local", external_task=True)
    assert delivery_targets("user", "info", ext, email_on=True, teams_targets=()) == []
    assert not valid_email("kotu adres@x") and valid_email("a.b@kurum.com.tr")


def test_messages_are_plain_and_links_internal():
    n = {"title": "Görev\r\nBcc: x@y.z", "body": "[tıkla](https://kotu.example) **kalın**", "link": "#/gorevler/abc",
         "severity": "critical"}
    subject, body = email_message(n, "https://evren.kurum.local")
    assert "\n" not in subject and "\r" not in subject and subject.startswith("[Evren] [Kritik]")
    assert "https://evren.kurum.local/#/gorevler/abc" in body
    card = teams_card(n, "https://evren.kurum.local")
    text = card["attachments"][0]["content"]["body"][1]["text"]
    assert "\\[tıkla\\]\\(https://kotu.example\\)" in text and "\\*\\*kalın\\*\\*" in text
    assert card["attachments"][0]["content"]["actions"][0]["url"] == "https://evren.kurum.local/#/gorevler/abc"
    assert safe_link("javascript:alert(1)") is None and safe_link("https://x") is None
    assert "actions" not in teams_card({**n, "link": "https://disari"}, "https://evren.kurum.local")["attachments"][0]["content"]


# ------------------------------------------------------------------------------------------------- metrikler
def test_prometheus_text_format():
    reg = Registry()
    c = reg.counter("evren_test_total", "Sınama sayacı", ("route", "status"))
    hist = reg.histogram("evren_test_seconds", "Süre", ("route",), buckets=(0.1, 1.0))
    c.inc(route='/a"b\\c', status="2xx")
    c.inc(2, route='/a"b\\c', status="2xx")
    hist.observe(0.05, route="/x")
    hist.observe(3, route="/x")
    text = reg.render(render_samples("evren_up", "Ayakta", "gauge", [({"kind": "api"}, 1)]))
    assert '# TYPE evren_test_total counter' in text
    assert 'evren_test_total{route="/a\\"b\\\\c",status="2xx"} 3' in text
    assert 'evren_test_seconds_bucket{route="/x",le="0.1"} 1' in text
    assert 'evren_test_seconds_bucket{route="/x",le="+Inf"} 2' in text
    assert 'evren_test_seconds_count{route="/x"} 2' in text and 'evren_up{kind="api"} 1' in text
    assert text.endswith("\n")
    with pytest.raises(ValueError):
        c.inc(-1, route="/", status="2xx")
    with pytest.raises(ValueError):
        c.inc(route="/")
    with pytest.raises(ValueError):
        Counter("kötü ad", "x")
    with pytest.raises(ValueError):
        reg.counter("evren_test_total", "iki kez")
    g = Gauge("evren_g", "g", ("a",))
    g.set(1.5, a="x")
    assert g.lines()[-1] == 'evren_g{a="x"} 1.5'
    assert Histogram("evren_h", "h").lines() == ["# HELP evren_h h", "# TYPE evren_h histogram"]
