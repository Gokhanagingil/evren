"""Platform uyarıları ve bildirimler (U8.9, ADR-0040): uyarı süre koşuluyla ateşlenir, koşul kalkınca çözülür ve
rollere bildirilir; görev atama, süre aşımı ve tırmanma bildirimleri; e-posta teslimi (ServiceNow'a teslim edilen
görevde kişiye e-posta yok), yönlendirme, yeniden deneme ve başarısızlık; tekrar üretim yok; en az yetki.

Her test tek işlemde çalışır ve geri alınır; zaman ``now`` ile ileri alınır. E-posta ve Teams bellek içi
simülatördür."""
from __future__ import annotations

import uuid
from datetime import timedelta

import psycopg
import pytest

from evren_core.metadata import load_metadata
from evren_core.ops import AlertRule, load_rules
from evren_governance.delivery import MemoryEmail, MemoryTeams
from evren_governance.notifier import Notifier
from evren_governance.ops_watch import watch

from .conftest import REPO

ROLE = "evren_app_governance"


@pytest.fixture
def tx():
    with psycopg.connect() as conn, conn.transaction(force_rollback=True):
        yield conn


def as_worker(conn):
    conn.execute(f"SET LOCAL ROLE {ROLE}")


def as_owner(conn):
    conn.execute("RESET ROLE")


def start_cursors(conn):
    """Bu testin olayları işlensin: üreticiler mevcut verinin sonundan başlar (işlem geri alınır)."""
    conn.execute("""UPDATE notify.cursor SET last_id = (SELECT coalesce(max(id), 0) FROM gov.task_event)
                     WHERE stream = 'task_event'""")
    conn.execute("""UPDATE notify.cursor SET last_id = (SELECT coalesce(max(seq), 0) FROM notify.notification)
                     WHERE stream = 'delivery_plan'""")


def person(conn, email):
    sid = uuid.uuid4().hex
    conn.execute("INSERT INTO ref.sys_user (sys_id, user_name, name, email, source) VALUES (%s, %s, %s, %s, 'evren')",
                 (sid, f"bil.{sid[:8]}", f"Bildirim Kişi {sid[:4]}", email))
    return sid


def group(conn, manager=None, email=None):
    sid = uuid.uuid4().hex
    conn.execute("INSERT INTO ref.sys_user_group (sys_id, name, manager, email, source) VALUES (%s, %s, %s, %s, 'evren')",
                 (sid, f"Bildirim Grup {sid[:4]}", manager, email))
    return sid


def task(conn, *, user=None, grp=None, channel="evren", external_ref=None):
    return conn.execute(
        """INSERT INTO gov.task (kind, title, assignee_user, assignee_group, assignee_basis, due_at, source, channel,
                                 delivery_status, external_ref, created_by)
           VALUES ('manual', %s, %s, %s, %s, now() + interval '7 days', 'manual', %s, %s, %s, 'test')
           RETURNING sys_id, number""",
        (f"Bildirim görevi {uuid.uuid4().hex[:4]}", user, grp, "assigned" if user or grp else "unassigned", channel,
         "not_needed" if channel == "evren" else ("delivered" if external_ref else "pending"), external_ref)).fetchone()


def event(conn, t, action):
    conn.execute("INSERT INTO gov.task_event (task, actor, action) VALUES (%s, 'test', %s)", (t, action))


def notifier(email=None, teams=None, **kw):
    return Notifier(email=email, teams=teams, base_url="https://evren.ornek.test", **kw)


def test_rule_audiences_exist_as_roles(tx):
    roles = {r[0] for r in tx.execute("SELECT name FROM iam.role").fetchall()}
    for r in load_rules(load_metadata(REPO / "metadata").alerts):
        assert set(r.audience) <= roles, r.id


def test_alert_waits_for_duration_fires_notifies_and_resolves(tx):
    rule = AlertRule(id="test_gecit_kapali", signal="service_down", title="{subject} yanıt vermiyor",
                     severity="critical", for_minutes=5, audience=("evren_operator",), kinds=("gateway",),
                     advice="Günlüğü denetleyin.")
    t0 = tx.execute("SELECT now()").fetchone()[0]
    wid = f"test-gw-{uuid.uuid4().hex[:6]}"
    tx.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind, details)
                  VALUES (%s, %s, %s, 30, 'gateway', '{}')""", (wid, t0, t0))
    start_cursors(tx)
    as_worker(tx)
    assert watch(tx, [rule], now=t0 + timedelta(minutes=1)) == []          # canlı: koşul yok
    assert watch(tx, [rule], now=t0 + timedelta(minutes=10)) == []         # koşul başladı: bekliyor
    state = tx.execute("SELECT state FROM ops.alert WHERE rule = %s", (rule.id,)).fetchone()[0]
    assert state == "pending"
    fired = watch(tx, [rule], now=t0 + timedelta(minutes=16))
    assert [(t.action, t.title) for t in fired] == [("fired", "Alım geçidi yanıt vermiyor")]
    n = notifier(email=MemoryEmail())
    assert n.from_alerts(tx, fired) == 1
    assert n.from_alerts(tx, fired) == 0                                    # aynı geçiş ikinci kez bildirilmez
    title, aud, sev = tx.execute("""SELECT title, audience, severity FROM notify.notification
                                     WHERE source = %s""", (f"alert:{fired[0].alert_id}",)).fetchone()
    assert (title, aud, sev) == ("Alım geçidi yanıt vermiyor", "evren_operator", "critical")
    # Servis geri geldi: uyarı çözülür ve bildirilir
    as_owner(tx)
    tx.execute("UPDATE ingest.worker_heartbeat SET seen_at = %s WHERE worker_id = %s", (t0 + timedelta(minutes=17), wid))
    as_worker(tx)
    resolved = watch(tx, [rule], now=t0 + timedelta(minutes=17, seconds=30))
    assert [t.action for t in resolved] == ["resolved"]
    assert tx.execute("SELECT state, resolved_at IS NOT NULL FROM ops.alert WHERE sys_id = %s",
                      (fired[0].alert_id,)).fetchone() == ("resolved", True)
    n.from_alerts(tx, resolved)
    assert tx.execute("SELECT count(*) FROM notify.notification WHERE source = %s AND kind = 'alert_resolved'",
                      (f"alert:{fired[0].alert_id}",)).fetchone()[0] == 1


def test_short_lived_condition_never_fires(tx):
    rule = AlertRule(id="test_kisa", signal="service_down", title="{subject} yanıt vermiyor", severity="warning",
                     for_minutes=10, audience=("evren_operator",), kinds=("gateway",))
    t0 = tx.execute("SELECT now()").fetchone()[0]
    wid = f"test-gw-{uuid.uuid4().hex[:6]}"
    tx.execute("""INSERT INTO ingest.worker_heartbeat (worker_id, started_at, seen_at, poll_seconds, kind, details)
                  VALUES (%s, %s, %s, 30, 'gateway', '{}')""", (wid, t0, t0))
    as_worker(tx)
    watch(tx, [rule], now=t0 + timedelta(minutes=5))
    as_owner(tx)
    tx.execute("UPDATE ingest.worker_heartbeat SET seen_at = %s WHERE worker_id = %s", (t0 + timedelta(minutes=6), wid))
    as_worker(tx)
    assert watch(tx, [rule], now=t0 + timedelta(minutes=6, seconds=10)) == []
    assert tx.execute("SELECT state, fired_at FROM ops.alert WHERE rule = 'test_kisa'").fetchone() == ("resolved", None)


def test_task_notifications_escalation_and_email_delivery(tx):
    start_cursors(tx)
    me = person(tx, "kisi@kurum.ornek")
    boss = person(tx, "yonetici@kurum.ornek")
    sn_person = person(tx, "sn@kurum.ornek")
    grp = group(tx, manager=boss, email="grup@kurum.ornek")
    a, a_no = task(tx, user=me)
    b, _ = task(tx, grp=grp)
    c, _ = task(tx, user=sn_person, channel="servicenow", external_ref="f" * 32)
    for t in (a, b, c):
        event(tx, t, "created")
    event(tx, b, "overdue")
    event(tx, a, "synced")                                                  # bildirim üretmeyen olay
    mail = MemoryEmail()
    n = notifier(email=mail)
    as_worker(tx)
    assert n.from_tasks(tx) == 5          # a atandı, b atandı, c atandı, b süresi geçti, b tırmanma (yöneticiye)
    assert n.from_tasks(tx) == 0          # imleç ilerledi; tekrar yok
    kinds = {(k, ak, aud) for k, ak, aud in tx.execute(
        """SELECT kind, audience_kind, audience FROM notify.notification
            WHERE source = ANY(%s)""", ([f"task:{t}" for t in (a, b, c)],)).fetchall()}
    assert ("task_escalated", "user", boss) in kinds and ("task_overdue", "group", grp) in kinds
    assert n.plan(tx) == 4                # c ServiceNow'da: kişiye e-posta yok (ServiceNow bildirir)
    assert n.send_due(tx) == (4, 0)
    to = sorted(m[0] for m in mail.sent)
    assert to == ["grup@kurum.ornek", "grup@kurum.ornek", "kisi@kurum.ornek", "yonetici@kurum.ornek"]
    first = next(m for m in mail.sent if m[0] == "kisi@kurum.ornek")
    assert a_no in first[1] and f"https://evren.ornek.test/#/gorevler/{a}" in first[2]
    assert tx.execute("SELECT count(*) FROM notify.delivery WHERE status = 'sent' AND notification IN "
                      "(SELECT sys_id FROM notify.notification WHERE source = ANY(%s))",
                      ([f"task:{t}" for t in (a, b, c)],)).fetchone()[0] == 4


def test_role_routes_teams_and_retry_until_failed(tx):
    start_cursors(tx)
    tx.execute("""INSERT INTO notify.route (audience_kind, audience, channel, target, min_severity, updated_by)
                  VALUES ('role', 'evren_config_manager', 'email', 'konf@kurum.ornek', 'warning', 'test'),
                         ('role', 'evren_config_manager', 'teams', 'yonetisim', 'info', 'test')""")
    orphan, _ = task(tx)
    event(tx, orphan, "created")          # sorumlusu yok → Konfigürasyon yöneticisi rolüne (uyarı önemi)
    mail, teams = MemoryEmail(fail_for={"konf@kurum.ornek"}), MemoryTeams(targets=frozenset({"yonetisim"}))
    n = notifier(email=mail, teams=teams, max_attempts=2)
    as_worker(tx)
    assert n.from_tasks(tx) == 1
    assert n.plan(tx) == 2
    assert n.send_due(tx) == (1, 0)       # Teams gitti, e-posta reddedildi (yeniden denenecek)
    assert teams.sent[0][0] == "yonetisim"
    card = teams.sent[0][1]["attachments"][0]["content"]
    assert card["body"][0]["text"].startswith("Sorumlusu olmayan görev")
    row = tx.execute("SELECT status, attempts, last_error, next_try_at > now() FROM notify.delivery "
                     "WHERE target = 'konf@kurum.ornek'").fetchone()
    assert row == ("pending", 1, "Simülatör: e-posta reddedildi", True)
    as_owner(tx)
    tx.execute("UPDATE notify.delivery SET next_try_at = now() - interval '1 minute' WHERE target = 'konf@kurum.ornek'")
    as_worker(tx)
    assert n.send_due(tx) == (0, 1)       # en çok deneme: başarısız
    assert tx.execute("SELECT status FROM notify.delivery WHERE target = 'konf@kurum.ornek'").fetchone()[0] == "failed"


@pytest.mark.parametrize("stmt", [
    "UPDATE ops.alert SET acked_by = 'x', acked_at = now(), ack_note = 'kendi kendine onay' WHERE false",
    "DELETE FROM ops.alert WHERE false",
    "UPDATE notify.notification SET title = 'x' WHERE false",
    "INSERT INTO notify.route (audience_kind, audience, channel, target, updated_by) "
    "VALUES ('role', 'evren_operator', 'email', 'x@y.zz', 'calisan')",
    "SELECT count(*) FROM notify.receipt",
    "INSERT INTO audit.security_event (principal, action, decision) VALUES ('x', 'alert_ack', 'allow')",
])
def test_worker_cannot_ack_alerts_edit_routes_or_read_receipts(tx, stmt):
    as_worker(tx)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), tx.transaction():
        tx.execute(stmt)


def test_notification_settings_are_validated_and_secrets_stay_out_of_messages(tmp_path):
    from evren_governance.config import ConfigError, GovernanceConfig

    base = {"EVREN_DATABASE_URL": "postgresql://x"}
    assert GovernanceConfig.from_env(base).email_mode == "kapalı"
    assert GovernanceConfig.from_env({**base, "EVREN_NOTIFY_SIMULATE": "1"}).teams_mode == "simülatör"
    with pytest.raises(ConfigError, match="geri döngü"):
        GovernanceConfig.from_env({**base, "EVREN_SMTP_HOST": "smtp.kurum.ornek", "EVREN_SMTP_TLS": "none",
                                   "EVREN_MAIL_FROM": "evren@kurum.ornek"})
    with pytest.raises(ConfigError, match="EVREN_MAIL_FROM"):
        GovernanceConfig.from_env({**base, "EVREN_SMTP_HOST": "smtp.kurum.ornek"})
    with pytest.raises(ConfigError, match="SMTP parolası"):
        GovernanceConfig.from_env({**base, "EVREN_SMTP_HOST": "smtp.kurum.ornek", "EVREN_MAIL_FROM": "evren@kurum.ornek",
                                   "EVREN_SMTP_USER": "evren"})
    hooks = tmp_path / "teams"
    hooks.write_text("# yorum\nplatform=https://kanca.ornek/gizli-yol-123\n")
    cfg = GovernanceConfig.from_env({**base, "EVREN_TEAMS_WEBHOOKS_FILE": str(hooks)})
    assert cfg.teams_webhooks == {"platform": "https://kanca.ornek/gizli-yol-123"} and "gizli" not in repr(cfg)
    hooks.write_text("platform=http://kanca.ornek/gizli-yol-123\n")
    with pytest.raises(ConfigError) as exc:
        GovernanceConfig.from_env({**base, "EVREN_TEAMS_WEBHOOKS_FILE": str(hooks)})
    assert "gizli" not in str(exc.value) and "https" in str(exc.value)
