"""Bağlayıcı çalıştırıcı → gerçek alım geçidi (U5, ADR-0020). Kaynak: Zabbix simülatörü; geçit: PostgreSQL'li uygulama.

Geçide ağ yerine ``TestClient`` üzerinden gidilir (``Transport`` sözleşmesi); kimlik doğrulama, kaynak yetkisi, zarf
doğrulaması, toplu gönderim sınırı, sayım ve kapanış geçidin gerçek koduyla sınanır. Kafka yerine taklit üretici.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import REPO, db, issue

from evren_core.connectors.gateway import GatewayClient
from evren_core.connectors.http import HttpClient, HttpError, HttpResponse
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.zabbix import ZabbixClient, ZabbixSimulator
from evren_core.messaging import TOPIC_RECORD, TOPIC_RUN

SAMPLES = REPO / "connectors" / "zabbix" / "samples"


class AsgiTransport:
    """``HttpClient`` isteklerini FastAPI ``TestClient``'a yönlendirir."""

    def __init__(self, client) -> None:
        self.client = client

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        r = self.client.request(method, path, content=body, headers=dict(headers))
        return HttpResponse(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content)


def connector(client, headers: dict[str, str], *, batch_size: int = 2, fail_on_call: int | None = None,
              clock=lambda: datetime.now(UTC)) -> ConnectorRunner:
    sim = ZabbixSimulator.from_dir(SAMPLES, fail_on_call=fail_on_call)
    source = ZabbixClient(HttpClient("https://zabbix.test", transport=sim, auth_header="Bearer z", retries=0),
                          page_size=2)
    gw = GatewayClient(HttpClient("https://gateway.test", transport=AsgiTransport(client),
                                  auth_header=headers["Authorization"], retries=0), "zabbix-e2e",
                       batch_size=batch_size)
    return ConnectorRunner(source, load_mappings(Path(REPO) / "connectors" / "zabbix", "Zabbix"), gw, clock=clock)


def test_connector_run_reaches_kafka_and_closes_the_run(gateway):
    client, pub = gateway
    name, headers = issue("Zabbix")
    report = connector(client, headers).run()
    assert report.completed and report.sent == 5 and report.gateway_state == "closing", report.as_json()
    records = [json.loads(v) for t, _k, v, _h in pub.sent if t == TOPIC_RECORD]
    assert sorted(r["data"]["source_key"] for r in records) == ["20501", "20502", "20503", "20504", "20505"]
    assert {r["source"] for r in records} == {"urn:evren:connector:zabbix-e2e"}
    assert [json.loads(v)["type"] for t, _k, v, _h in pub.sent if t == TOPIC_RUN] == [
        "evren.ingest.run.started.v1", "evren.ingest.run.completed.v1"]
    assert db("""SELECT data_source, opened_by, accepted_count, reported_count, reported_completed,
                        reported_error_count, close_requested_at IS NOT NULL
                   FROM ingest.source_run WHERE run_id = %s""", report.run_id) == [
        ("Zabbix", name, 5, 5, True, 0, True)]


def test_partial_source_fetch_is_reported_as_not_completed(gateway):
    client, _pub = gateway
    _name, headers = issue("Zabbix")
    report = connector(client, headers, fail_on_call=3).run()
    assert not report.completed and report.sent == 2
    assert db("SELECT accepted_count, reported_count, reported_completed FROM ingest.source_run WHERE run_id = %s",
              report.run_id) == [(2, 2, False)]


def test_rejected_batch_stops_sending_and_run_is_closed_incomplete(gateway):
    client, pub = gateway
    _name, headers = issue("Zabbix")
    skewed = connector(client, headers, clock=lambda: datetime.now(UTC) + timedelta(days=1))  # saat kayması
    report = skewed.run()
    assert not report.completed and report.sent == 0 and report.failure == "geçit reddetti (durum 400)"
    assert not [m for m in pub.sent if m[0] == TOPIC_RECORD]
    assert db("SELECT accepted_count, reported_completed, reported_error_count FROM ingest.source_run "
              "WHERE run_id = %s", report.run_id) == [(0, False, 2)]


def test_credential_of_another_source_cannot_run_the_connector(gateway):
    client, pub = gateway
    _name, headers = issue("vCenter")
    rid = uuid.uuid4()
    with pytest.raises(HttpError) as exc:  # çalışma açılamaz
        connector(client, headers).run(run_id=rid)
    assert exc.value.status == 403 and not pub.sent
    assert not db("SELECT 1 FROM ingest.source_run WHERE run_id = %s", rid)
