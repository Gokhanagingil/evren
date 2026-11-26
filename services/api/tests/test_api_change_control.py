"""Değişiklik denetimi (ADR-0025): CI ayrıntısında gözlenen değişiklikler ve değişiklik kayıtları, özet sayısı, liste
süzgeci ve GRC kontrolü CMDB-05. Veriler test içinde sahip rolüyle eklenir ve sonunda silinir (sentetik)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

AT = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
CHG = "c9" * 16
TASK_CI = "d9" * 16
EVREN_BORN_SN = "e9" * 16


@pytest.fixture
def change_ci():
    """Bir sunucuya: yetkisiz bir gözlem (açık anomali), onaylı bir gözlem, değişiklik kaydı ve task_ci; bir başka CI
    Evren'de doğmuş gibi ServiceNow sys_id'siyle (sn_ci_map) değişiklik kaydına bağlanır."""
    with psycopg.connect(autocommit=True) as c:
        ci, other = [r[0] for r in c.execute(
            """SELECT ci.sys_id FROM cmdb.cmdb_ci ci WHERE ci.sys_class_name LIKE 'cmdb_ci_%%server'
                AND NOT EXISTS (SELECT 1 FROM sync.sn_ci_map m WHERE m.evren_sys_id = ci.sys_id)
                ORDER BY ci.sys_id LIMIT 2""").fetchall()]
        anomaly = c.execute(
            """INSERT INTO insight.anomaly (kind, severity, ci_sys_id, data_source, window_start, window_end,
                                            explanation) VALUES ('unauthorized_change', 'high', %s, 'BMC', %s, %s,
                                            'test: RAM değişti, onaylı değişiklik kaydı yok') RETURNING sys_id""",
            (ci, AT - timedelta(hours=6), AT)).fetchone()[0]
        c.execute("""INSERT INTO insight.config_change (ci_sys_id, sys_class_name, attribute, old_value, new_value,
                                                         data_source, observed_from, observed_at, verdict,
                                                         anomaly_sys_id, change_number, detail)
                     VALUES (%s, 'cmdb_ci_server', 'ram', '16384', '32768', 'BMC', %s, %s, 'unauthorized', %s, NULL,
                             '{"explanation": "Gözlem aralığını kapsayan onaylı değişiklik kaydı yok"}'),
                            (%s, 'cmdb_ci_server', 'os_version', '"9.3"', '"9.4"', 'BMC', %s, %s, 'authorized', NULL,
                             'CHG0039999',
                             '{"explanation": "CHG0039999 (planlı pencere) gözlem aralığını kapsıyor."}')""",
                  (ci, AT - timedelta(hours=6), AT, anomaly, ci, AT - timedelta(days=2), AT - timedelta(days=1)))
        c.execute("""INSERT INTO ref.sn_change_request (sys_id, number, short_description, type, state, approval,
                                                         start_date, end_date, cmdb_ci, sys_updated_on)
                     VALUES (%s, 'CHG0039999', 'test değişikliği', 'normal', 'closed', 'approved', %s, %s, %s, now())""",
                  (CHG, AT - timedelta(days=2), AT - timedelta(days=1), EVREN_BORN_SN))
        c.execute("INSERT INTO ref.sn_task_ci (sys_id, task, ci_item) VALUES (%s, %s, %s)", (TASK_CI, CHG, ci))
        c.execute("INSERT INTO sync.sn_ci_map (evren_sys_id, sn_sys_id, state) VALUES (%s, %s, 'in_sync')",
                  (other, EVREN_BORN_SN))
    yield ci, other
    with psycopg.connect(autocommit=True) as c:
        c.execute("DELETE FROM insight.config_change WHERE ci_sys_id = %s", (ci,))
        c.execute("DELETE FROM insight.anomaly WHERE ci_sys_id = %s AND kind = 'unauthorized_change'", (ci,))
        c.execute("DELETE FROM ref.sn_task_ci WHERE sys_id = %s", (TASK_CI,))
        c.execute("DELETE FROM ref.sn_change_request WHERE sys_id = %s", (CHG,))
        c.execute("DELETE FROM sync.sn_ci_map WHERE evren_sys_id = %s", (other,))


def test_ci_detail_shows_observations_and_change_records(change_ci, reader):
    ci, other = change_ci
    doc = reader.get(f"/api/v1/ci/{ci}").json()
    cc = doc["change_control"]
    assert doc["unauthorized_changes"] == 1 and cc["open_unauthorized"] == 1
    by_attr = {o["attribute"]: o for o in cc["observations"]}
    ram = by_attr["ram"]
    assert (ram["verdict"], ram["verdict_label"], ram["source"]) == ("unauthorized", "Yetkisiz değişiklik", "BMC")
    assert ram["old"] == 16384 and ram["new"] == 32768 and ram["label"] == "RAM (MB)"
    assert by_attr["os_version"]["change_number"] == "CHG0039999"
    (chg,) = cc["changes"]  # task_ci üzerinden
    assert (chg["number"], chg["state_label"], chg["approval_label"], chg["type_label"]) == (
        "CHG0039999", "kapandı", "onaylandı", "normal")
    # Evren'de doğan CI: değişiklik kaydı ServiceNow sys_id'sini taşır (sync.sn_ci_map)
    assert [c["number"] for c in reader.get(f"/api/v1/ci/{other}").json()["change_control"]["changes"]] == [
        "CHG0039999"]


def test_summary_scope_and_grc_control(change_ci, reader):
    ci, _ = change_ci
    assert reader.get("/api/v1/summary").json()["unauthorized_changes"] >= 1
    items = reader.get("/api/v1/ci", params={"scope": "yetkisiz-degisiklik", "limit": 500}).json()["items"]
    assert ci in {i["sys_id"] for i in items}
    control = next(c for c in reader.get("/api/v1/controls").json() if c["id"] == "CMDB-05")
    assert ci in {e["sys_id"] for e in control["evidence"]}
    assert control["provenance"]["depends_on"] == "ServiceNowChange"
    # değişiklik kaynağının sağlıklı bir çalışması yoksa sonuç kesin değildir (GRC Platformu: inconclusive)
    assert control["status"] in ("fail", "inconclusive")


def test_resolved_anomaly_leaves_the_scope(change_ci, reader):
    ci, _ = change_ci
    with psycopg.connect(autocommit=True) as c:
        c.execute("UPDATE insight.anomaly SET status = 'resolved' WHERE ci_sys_id = %s", (ci,))
    assert reader.get(f"/api/v1/ci/{ci}").json()["unauthorized_changes"] == 0
    items = reader.get("/api/v1/ci", params={"scope": "yetkisiz-degisiklik", "limit": 500}).json()["items"]
    assert ci not in {i["sys_id"] for i in items}


def test_review_requires_permission_note_and_valid_transition(change_ci, reader):
    from .conftest import make_client

    ci, _ = change_ci
    (anomaly,) = [a["sys_id"] for a in reader.get(f"/api/v1/ci/{ci}").json()["change_control"]["anomalies"]]
    assert reader.get(f"/api/v1/ci/{ci}").json()["change_control"]["can_review"] is False
    hdr = {"x-evren-request": "1"}
    url = f"/api/v1/anomalies/{anomaly}/review"
    assert reader.post(url, json={"status": "acknowledged"}, headers=hdr).status_code == 403
    with make_client(("evren_reader", "evren_data_steward"), "degisiklik-sorumlusu") as steward:
        assert steward.get(f"/api/v1/ci/{ci}").json()["change_control"]["can_review"] is True
        assert steward.post(url, json={"status": "acknowledged"}).status_code == 403  # CSRF başlığı yok
        assert steward.post(url, json={"status": "false_positive"}, headers=hdr).status_code in (400, 422)  # gerekçe
        assert steward.post(url, json={"status": "resolved"}, headers=hdr).status_code in (400, 422)  # sistemin işi
        res = steward.post(url, json={"status": "acknowledged"}, headers=hdr).json()
        assert res == {"sys_id": anomaly, "status": "acknowledged", "status_label": "İncelendi", "changed": True}
        assert steward.post(url, json={"status": "acknowledged"}, headers=hdr).json()["changed"] is False
        res = steward.post(url, json={"status": "false_positive", "note": "Kayıt başka sistemde"}, headers=hdr)
        assert res.status_code == 200
        doc = steward.get(f"/api/v1/ci/{ci}").json()["change_control"]
        (a,) = doc["anomalies"]
        assert (a["status"], a["reviewed_by"], a["review_note"]) == ("false_positive", "dev:degisiklik-sorumlusu",
                                                                     "Kayıt başka sistemde")
        assert doc["open_unauthorized"] == 0
        with psycopg.connect(autocommit=True) as c:
            c.execute("UPDATE insight.anomaly SET status = 'resolved' WHERE sys_id = %s", (anomaly,))
        r = steward.post(url, json={"status": "open"}, headers=hdr)
        assert r.status_code == 409 and r.json()["error"]["code"] == "durum_uygun_degil"
        assert steward.post("/api/v1/anomalies/yok/review", json={"status": "open"}, headers=hdr).status_code == 400
    with psycopg.connect(autocommit=True) as c:
        n = c.execute("""SELECT count(*) FROM audit.security_event WHERE action = 'anomaly_review'
                          AND resource = %s""", (f"anomaly:{anomaly}",)).fetchone()[0]
    assert n == 2
