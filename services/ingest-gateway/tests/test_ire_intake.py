"""IRE uyumlu alım ucu (U9.3b, ADR-0045): ServiceNow IRE yükü → delta çalışması → Kafka → uzlaştırıcı → CI.

Kimlik (bağlayıcı belirteci), kaynak yetkisi (sysparm_data_source), doğrulama (ya hep ya hiç, girdi yansıtılmaz),
yalnızca doğrulama (/query), Kafka hatasında eksik kapanış, ServiceNow hata biçimi ve uçtan uca: yük uzlaştırıcıdan
geçip CI ve ilişki olur (golden record'a geçit yazmaz). Kafka yerine taklit üretici; veriler sentetiktir."""
from __future__ import annotations

import json
import sys
import uuid

import psycopg
from conftest import REPO, db, issue

from evren_core.messaging import TOPIC_RECORD, TOPIC_RUN, decode_record

sys.path.insert(0, str(REPO / "services/reconciler"))

URL = "/api/now/identifyreconcile?sysparm_data_source=Zabbix"


def payload(tag: str, n: int = 2, *, rel: bool = True) -> dict:
    items = [{"className": "cmdb_ci_linux_server",
              "values": {"name": f"{tag}-{i}", "serial_number": f"{tag.upper()}-SN-{i}", "ip_address": f"10.77.0.{i + 1}"},
              "sys_object_source_info": {"source_name": "Zabbix", "source_native_key": f"{tag}-key-{i}",
                                         "source_recency_timestamp": "2026-11-28 09:00:00"}} for i in range(n)]
    items.append({"className": "cmdb_ci_appl", "values": {"name": f"{tag}-uygulama"},
                  "sys_object_source_info": {"source_native_key": f"{tag}-app"}})
    doc = {"items": items}
    if rel:
        doc["relations"] = [{"parent": n, "child": 0, "type": "Runs on::Runs"}]
    return doc


def records(pub):
    return [decode_record(v, k).data for t, k, v, _h in pub.sent if t == TOPIC_RECORD]


def test_payload_becomes_one_delta_run_on_kafka_with_ire_shaped_answer(gateway):
    client, pub = gateway
    name, headers = issue("Zabbix")
    tag = f"ire{uuid.uuid4().hex[:6]}"
    r = client.post(URL, json=payload(tag), headers=headers)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert [i["operation"] for i in res["items"]] == ["QUEUED"] * 3 and res["items"][0]["sysId"] == "Unknown"
    assert res["relations"][0]["className"] == "Runs on::Runs" and res["evren"]["accepted"] == 3
    run_id = res["evren"]["run_id"]
    recs = records(pub)
    assert sorted(x.source_key for x in recs) == [f"{tag}-app", f"{tag}-key-0", f"{tag}-key-1"]
    assert {str(x.run_id) for x in recs} == {run_id} and {x.data_source for x in recs} == {"Zabbix"}
    app = next(x for x in recs if x.class_hint == "cmdb_ci_appl")
    assert app.relations[0].target_source_key == f"{tag}-key-0"
    assert [json.loads(v)["type"] for t, _k, v, _h in pub.sent if t == TOPIC_RUN] == [
        "evren.ingest.run.started.v1", "evren.ingest.run.completed.v1"]
    row = db("""SELECT data_source, mode, opened_by, accepted_count, reported_count, reported_completed,
                       close_requested_at IS NOT NULL FROM ingest.source_run WHERE run_id = %s""", run_id)[0]
    assert row == ("Zabbix", "delta", name, 3, 3, True, True)
    assert db("SELECT decision FROM audit.security_event WHERE action = 'ire_ingest' AND resource = %s",
              f"source_run:{run_id}") == [("allow",)]
    st = client.get(res["evren"]["status_path"], headers=headers).json()
    assert st["state"] == "closing" and st["accepted"] == 3


def test_query_only_validates_and_writes_nothing(gateway):
    client, pub = gateway
    _name, headers = issue("Zabbix")
    tag = f"irq{uuid.uuid4().hex[:6]}"
    before = db("SELECT count(*) FROM ingest.source_run")[0][0]
    r = client.post(URL.replace("identifyreconcile", "identifyreconcile/query"),
                    json=payload(tag) | {"referenceItems": [{"className": "core_company"}]}, headers=headers)
    assert r.status_code == 200
    res = r.json()["result"]
    assert {i["operation"] for i in res["items"]} == {"VALIDATED"} and res["evren"]["accepted"] == 0
    assert res["evren"]["warnings"] == ["referenceItems bu sürümde işlenmez"]
    assert not pub.sent and db("SELECT count(*) FROM ingest.source_run")[0][0] == before


def test_invalid_payload_is_rejected_whole_in_servicenow_format(gateway):
    client, pub = gateway
    _name, headers = issue("Zabbix")
    bad = payload("irb")
    bad["items"][1]["sys_object_source_info"] = {"source_name": "Zabbix"}          # anahtar yok
    bad["items"][0]["values"]["name"] = {"gizli": "SIZDIRILMAMALI"}                 # tek değer değil
    r = client.post(URL, json=bad, headers=headers)
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "failure" and "hiçbir öğe alınmadı" in body["error"]["message"]
    assert "items[1].sys_object_source_info.source_native_key" in body["error"]["detail"]
    assert "SIZDIRILMAMALI" not in r.text and not pub.sent
    assert client.post(URL, json={"items": []}, headers=headers).status_code == 400
    too_many = payload("irm", n=5)  # testte en çok 5 öğe; 6 öğe
    assert "en çok 5" in client.post(URL, json=too_many, headers=headers).json()["error"]["detail"]


def test_identity_source_binding_and_errors_follow_servicenow_format(gateway):
    client, pub = gateway
    r = client.post(URL, json=payload("ira"))
    assert r.status_code == 401 and r.json()["status"] == "failure"
    _name, headers = issue("Defender")
    denied = client.post(URL, json=payload("ira"), headers=headers)               # belirteç Defender'a bağlı
    assert denied.status_code == 403 and denied.json()["error"]["message"].startswith("Bu belirteç yalnızca")
    _n, zh = issue("Zabbix")
    unknown = client.post("/api/now/identifyreconcile?sysparm_data_source=Yok", json=payload("ira"), headers=zh)
    assert unknown.status_code == 403
    missing = client.post("/api/now/identifyreconcile", json=payload("ira"), headers=zh)
    assert missing.status_code == 400 and "sysparm_data_source" in missing.json()["error"]["detail"]
    assert client.get("/api/now/identifyreconcile", headers=zh).json()["status"] == "failure"
    assert not pub.sent


def test_kafka_failure_closes_the_run_as_incomplete(gateway):
    client, pub = gateway
    _name, headers = issue("Zabbix")
    pub.fail = True
    r = client.post(URL, json=payload(f"irk{uuid.uuid4().hex[:6]}"), headers=headers)
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    row = db("""SELECT accepted_count, reported_count, reported_completed, reported_error_count
                  FROM ingest.source_run WHERE notes LIKE 'IRE%%' ORDER BY started_at DESC LIMIT 1""")[0]
    assert row == (0, 0, False, 3)


def test_end_to_end_reconciler_turns_the_payload_into_cis_and_relation(gateway, metadata):
    from evren_reconciler.store import ReconcilerStore

    client, pub = gateway
    _name, headers = issue("Zabbix")
    tag = f"ire2e{uuid.uuid4().hex[:5]}"
    res = client.post(URL, json=payload(tag, n=1), headers=headers).json()["result"]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("SET ROLE evren_app_reconciler")
        store = ReconcilerStore(conn, metadata)
        for rec in records(pub):
            store.apply(rec)
        closed = [c for c in store.finalize_ready_runs() if str(c.run_id) == res["evren"]["run_id"]]
        conn.execute("RESET ROLE")
    assert closed and closed[0].reason == "completed"
    cis = db("""SELECT c.sys_class_name, c.name FROM ingest.identity_link l JOIN cmdb.cmdb_ci c ON c.sys_id = l.ci_sys_id
                 WHERE l.data_source = 'Zabbix' AND l.source_key LIKE %s AND l.active ORDER BY 1""", f"{tag}-%")
    assert cis == [("cmdb_ci_appl", f"{tag}-uygulama"), ("cmdb_ci_linux_server", f"{tag}-0")]
    rel = db("""SELECT t.name FROM cmdb.cmdb_rel_ci r JOIN cmdb.cmdb_rel_type t ON t.sys_id = r.type
                  JOIN cmdb.cmdb_ci p ON p.sys_id = r.parent JOIN cmdb.cmdb_ci c ON c.sys_id = r.child
                 WHERE p.name = %s AND c.name = %s""", f"{tag}-uygulama", f"{tag}-0")
    assert rel == [("Runs on::Runs",)]
