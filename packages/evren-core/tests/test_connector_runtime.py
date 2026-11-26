"""Bağlayıcı çalıştırıcı (U5, ADR-0020): HTTP katmanı, sırlar, kayıt/oynatma, Zabbix istemcisi, çalıştırıcı, CLI.

Kaynak tarafı ``ZabbixSimulator`` ile, geçit tarafı geçidin sözleşmesini taklit eden bir taşımayla sınanır. Gerçek
geçitle uçtan uca sınama ``services/ingest-gateway/tests/test_connector_e2e.py``'dedir. Sentetik veriyle yapılan bu
testler gerçek Zabbix kabulü yerine geçmez.
"""
from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evren_core.connectors.gateway import DryRunGateway, GatewayClient, GatewayRejected
from evren_core.connectors.http import (
    HttpClient,
    HttpError,
    HttpResponse,
    RecordingTransport,
    ReplayTransport,
    UrllibTransport,
    read_secret,
    request_fingerprint,
)
from evren_core.connectors.mapping import MappingError
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources import SOURCES
from evren_core.connectors.sources.zabbix import ZabbixClient, ZabbixSimulator
from evren_core.envelope import IngestEnvelope, IngestRecord, RunEnvelope

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "zabbix" / "samples"
TOKEN = "evr_" + "S" * 43  # test belirteci; gerçek değildir  # gitleaks:allow


class Scripted:
    """Sırayla verilen yanıtları döndüren taşıma; gönderilen istekleri kaydeder."""

    def __init__(self, *responses: HttpResponse | Exception) -> None:
        self.responses = list(responses)
        self.sent: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        self.sent.append((method, url, dict(headers), body))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def resp(status: int, payload: Any = None, **headers: str) -> HttpResponse:
    return HttpResponse(status, {k.replace("_", "-"): v for k, v in headers.items()},
                        b"" if payload is None else json.dumps(payload).encode())


class FakeGateway:
    """Alım geçidi sözleşmesinin taşıma düzeyinde taklidi: zarfları doğrular, sayar, ``reject`` ile reddeder."""

    def __init__(self, *, reject_batch: int | None = None, max_batch: int = 500) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.batches: list[list[dict[str, Any]]] = []
        self.reject_batch, self.max_batch = reject_batch, max_batch
        self.headers: list[dict[str, str]] = []

    def send(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> HttpResponse:
        self.headers.append(dict(headers))
        path = url.split("://", 1)[1].split("/", 1)[1]
        doc = json.loads(body or b"{}")
        if path == "ingest/v1/runs":
            env = RunEnvelope.model_validate(doc)
            assert env.type == "evren.ingest.run.started.v1"
            self.runs[str(env.data.run_id)] = {"accepted": 0, "closed": None}
            return resp(201, {"run_id": str(env.data.run_id), "state": "open"})
        run_id, action = path.split("/")[3:5]
        if action == "records":
            records = [IngestEnvelope.model_validate(r) for r in doc["records"]]
            self.batches.append(doc["records"])
            if len(records) > self.max_batch:
                return resp(413, {"error": {"code": "toplu_buyuk"}})
            if self.reject_batch is not None and len(self.batches) == self.reject_batch:
                return resp(400, {"error": {"code": "gecersiz_kayit", "message": "gizli sunucu ayrıntısı"}})
            self.runs[run_id]["accepted"] += len(records)
            return resp(202, {"run_id": run_id, "accepted": len(records)})
        env = RunEnvelope.model_validate(doc)
        assert env.type == "evren.ingest.run.completed.v1"
        assert env.data.record_count <= self.runs[run_id]["accepted"]
        self.runs[run_id]["closed"] = env.data
        return resp(202, {"run_id": run_id, "state": "closing"})


def zabbix(transport: Any = None, page_size: int = 2) -> ZabbixClient:
    sim = transport or ZabbixSimulator.from_dir(SAMPLES)
    return ZabbixClient(HttpClient("https://zabbix.test", transport=sim, auth_header=HttpClient.bearer("z")),
                        page_size=page_size)


def runner(client: Any, fake: FakeGateway, batch_size: int = 2) -> ConnectorRunner:
    gw = GatewayClient(HttpClient("https://gateway.test", transport=fake, auth_header=HttpClient.bearer(TOKEN),
                                  sleep=lambda s: None), "zabbix-test", batch_size=batch_size)
    return ConnectorRunner(client, load_mappings(REPO / "connectors" / "zabbix", "Zabbix"), gw)


# ------------------------------------------------------------------ HTTP katmanı
def test_client_refuses_credentials_in_url_and_unknown_schemes():
    with pytest.raises(HttpError, match="kullanıcı bilgisi"):
        HttpClient("https://kullanici:parola@zabbix.test")
    with pytest.raises(HttpError, match="https"):
        HttpClient("ftp://zabbix.test")


def test_repr_and_errors_never_contain_secrets_or_server_body():
    t = Scripted(resp(401, {"error": "sunucu-ayrintisi"}))
    c = HttpClient("https://zabbix.test", transport=t, auth_header=HttpClient.bearer(TOKEN))
    assert TOKEN not in repr(c) and "auth=var" in repr(c)
    with pytest.raises(HttpError) as exc:
        c.get_json("/api", q="1")
    assert exc.value.status == 401 and not exc.value.retryable
    assert TOKEN not in str(exc.value) and "sunucu-ayrintisi" not in str(exc.value) and "q=1" not in str(exc.value)
    assert t.sent[0][2]["Authorization"] == f"Bearer {TOKEN}"


def test_retries_transient_failures_with_backoff_and_retry_after():
    sleeps: list[float] = []
    t = Scripted(HttpError("bağlantı", retryable=True), resp(503), resp(429, retry_after="7"), resp(200, {"ok": 1}))
    c = HttpClient("https://zabbix.test", transport=t, sleep=sleeps.append, backoff=0.5)
    assert c.get_json("/api") == {"ok": 1}
    assert sleeps == [0.5, 1.0, 7.0] and len(t.sent) == 4
    assert len({body for *_x, body in t.sent}) == 1  # yeniden denemede aynı gövde (aynı olay kimlikleri)


def test_retry_after_is_capped_and_retries_are_bounded():
    sleeps: list[float] = []
    t = Scripted(*[resp(503, retry_after="3600")] * 3)
    c = HttpClient("https://zabbix.test", transport=t, retries=2, sleep=sleeps.append)
    with pytest.raises(HttpError) as exc:
        c.get_json("/api")
    assert exc.value.status == 503 and exc.value.retryable and sleeps == [120.0, 120.0]


def test_permanent_client_errors_are_not_retried():
    t = Scripted(resp(400, {"error": "x"}))
    with pytest.raises(HttpError) as exc:
        HttpClient("https://zabbix.test", transport=t, sleep=lambda s: pytest.fail("beklenmedi")).post_json("/x", {})
    assert exc.value.status == 400 and len(t.sent) == 1


def test_urllib_transport_requires_https_unless_explicitly_allowed():
    with pytest.raises(HttpError, match="Yalnızca https"):
        UrllibTransport().send("GET", "http://127.0.0.1:9/x", {}, None, 1)
    with pytest.raises(HttpError) as exc:  # izinle düz http denenir (kapalı bağlantı noktası → yeniden denenebilir hata)
        UrllibTransport(allow_http=True).send("GET", "http://127.0.0.1:9/x", {}, None, 1)
    assert exc.value.retryable and "Bağlantı kurulamadı" in str(exc.value)


def test_non_json_response_is_a_clear_error():
    with pytest.raises(HttpError, match="JSON değil"):
        HttpResponse(200, {}, b"<html>").json()


def test_read_secret(tmp_path):
    f = tmp_path / "belirtec"
    f.write_text(TOKEN + "\n", encoding="utf-8")
    assert read_secret(f, "Kaynak belirteci") == TOKEN
    with pytest.raises(HttpError, match="tanımlı değil"):
        read_secret(None, "Kaynak belirteci")
    with pytest.raises(HttpError, match="okunamadı: yok") as exc:
        read_secret(tmp_path / "yok", "Kaynak belirteci")
    assert str(tmp_path) not in str(exc.value)  # yalnızca dosya adı
    (tmp_path / "bos").write_text("\n", encoding="utf-8")
    with pytest.raises(HttpError, match="boş"):
        read_secret(tmp_path / "bos", "Kaynak belirteci")


def test_fingerprint_distinguishes_body_and_query_but_not_host():
    a = request_fingerprint("post", "https://a.test/api?x=1", b'{"id": 1}')
    assert a == request_fingerprint("POST", "https://b.test/api?x=1", b'{"id": 1}')
    assert a != request_fingerprint("POST", "https://a.test/api?x=2", b'{"id": 1}')
    assert a != request_fingerprint("POST", "https://a.test/api?x=1", b'{"id": 2}')
    assert a.startswith("POST /api?x=1 #")


def test_record_then_replay_gives_identical_records(tmp_path):
    rec = RecordingTransport(ZabbixSimulator.from_dir(SAMPLES), tmp_path, {"page_size": 2})
    recorded = list(zabbix(rec).fetch())
    rec.save()
    index = json.loads((tmp_path / "replay.json").read_text(encoding="utf-8"))
    assert index["options"] == {"page_size": 2} and len(index["responses"]) == 4
    assert "Bearer" not in (tmp_path / "replay.json").read_text(encoding="utf-8")
    replay = ReplayTransport.from_dir(tmp_path)
    assert list(zabbix(replay, page_size=replay.options["page_size"]).fetch()) == recorded
    with pytest.raises(HttpError, match="Kayıtlı yanıt yok"):  # farklı sayfa boyutu → kayıtta olmayan istek
        list(zabbix(ReplayTransport.from_dir(tmp_path), page_size=3).fetch())


# ------------------------------------------------------------------ Zabbix istemcisi
def test_zabbix_pages_by_host_id_chunks_and_selects_only_needed_fields():
    sim = ZabbixSimulator.from_dir(SAMPLES)
    hosts = list(zabbix(sim, page_size=2).fetch())
    assert [h["hostid"] for h in hosts] == ["20501", "20502", "20503", "20504", "20505"]
    assert sim.calls == 1 + 3  # kimlik listesi + 3 ayrıntı sayfası
    assert set(hosts[0]) == {"hostid", "host", "name", "status", "interfaces", "inventory", "hostgroups"}
    assert set(hosts[0]["inventory"]) <= {"os", "os_full", "serialno_a", "macaddress_a", "asset_tag"}
    assert hosts[4]["inventory"] == []  # envanteri kapalı host: Zabbix boş dizi döndürür


def test_zabbix_api_error_reports_code_only():
    t = Scripted(resp(200, {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Not authorised.",
                                                         "data": "kurum-ici-ayrinti"}, "id": 1}))
    with pytest.raises(HttpError) as exc:
        list(zabbix(t).fetch())
    assert "-32602" in str(exc.value) and "kurum-ici-ayrinti" not in str(exc.value)


def test_zabbix_unexpected_extra_hosts_are_rejected():
    t = Scripted(resp(200, {"jsonrpc": "2.0", "result": [{"hostid": "1"}], "id": 1}),
                 resp(200, {"jsonrpc": "2.0", "result": [{"hostid": "1"}, {"hostid": "2"}], "id": 2}))
    with pytest.raises(HttpError, match="fazla host"):
        list(zabbix(t).fetch())


def test_simulator_requires_bearer_token():
    sim = ZabbixSimulator.from_dir(SAMPLES)
    client = ZabbixClient(HttpClient("https://zabbix.test", transport=sim))
    with pytest.raises(HttpError, match="-32602"):
        list(client.fetch())


# ------------------------------------------------------------------ çalıştırıcı
def test_successful_run_opens_streams_in_batches_and_closes_completed():
    fake = FakeGateway()
    report = runner(zabbix(), fake, batch_size=2).run()
    assert report.completed and report.fetched == report.mapped == report.sent == 5 and report.errors == 0
    assert [len(b) for b in fake.batches] == [2, 2, 1]
    closed = fake.runs[str(report.run_id)]["closed"]
    assert closed.completed and closed.record_count == 5 and closed.error_count == 0 and closed.finished_at
    assert report.by_class == {"cmdb_ci_win_server": 1, "cmdb_ci_linux_server": 2, "cmdb_ci_netgear": 1,
                               "cmdb_ci_server": 1}
    first = fake.batches[0][0]
    assert first["source"] == "urn:evren:connector:zabbix-test" and first["data"]["source_key"] == "20501"
    assert all(h["Authorization"] == f"Bearer {TOKEN}" for h in fake.headers)
    assert report.gateway_state == "closing"


def test_partial_fetch_closes_run_as_not_completed():
    fake = FakeGateway()
    sim = ZabbixSimulator.from_dir(SAMPLES, fail_on_call=3)  # ikinci ayrıntı sayfası kalıcı 503
    client = ZabbixClient(HttpClient("https://zabbix.test", transport=sim, auth_header="Bearer z", retries=0),
                          page_size=2)
    report = runner(client, fake).run()
    assert not report.completed and "durum 503" in report.failure
    closed = fake.runs[str(report.run_id)]["closed"]
    assert closed.completed is False and closed.record_count == report.sent == 2


def test_mapping_error_is_counted_and_run_is_not_completed():
    hosts = json.loads((SAMPLES / "hosts.json").read_text(encoding="utf-8"))["hosts"]
    hosts.append({"hostid": "", "host": "kimliksiz"})

    class Source:
        data_source = "Zabbix"

        def fetch(self, mode, since):
            yield from hosts

    fake = FakeGateway()
    report = runner(Source(), fake).run()
    assert report.fetched == 6 and report.sent == 5 and report.skipped == report.errors == 1
    assert not report.completed and report.notes == ["eşleme hatası: MappingError"]
    assert fake.runs[str(report.run_id)]["closed"].error_count == 1
    assert "kimliksiz" not in json.dumps(report.as_json())  # kayıt içeriği rapora/günlüğe yazılmaz


def test_gateway_rejection_stops_the_run_without_echoing_server_message():
    fake = FakeGateway(reject_batch=2)
    report = runner(zabbix(), fake, batch_size=2).run()
    assert not report.completed and report.sent == 2 and report.errors == 2
    assert report.failure == "geçit reddetti (durum 400)"
    assert fake.runs[str(report.run_id)]["closed"].completed is False


def test_batch_larger_than_gateway_limit_is_rejected():
    fake = FakeGateway(max_batch=1)
    report = runner(zabbix(), fake, batch_size=2).run()
    assert not report.completed and report.sent == 0 and "413" in report.failure


@pytest.mark.parametrize("status", [400, 403, 409, 413])
def test_gateway_client_raises_rejected_for_permanent_errors(status):
    record = IngestRecord(data_source="Zabbix", run_id=uuid.uuid4(), source_key="1", class_hint="cmdb_ci_server",
                          attributes={"name": "a"}, observed_at=datetime.now(UTC))
    t = Scripted(resp(status, {"error": {"code": "x"}}))
    gw = GatewayClient(HttpClient("https://gateway.test", transport=t), "x")
    with pytest.raises(GatewayRejected) as exc:
        gw.send(record.run_id, [record])
    assert exc.value.status == status and len(t.sent) == 1


def test_mappings_must_belong_to_the_client_source():
    with pytest.raises(MappingError, match="başka bir veri kaynağına"):
        ConnectorRunner(zabbix(), load_mappings(REPO / "connectors" / "vcenter", "vCenter"), DryRunGateway())
    assert len(load_mappings(REPO / "connectors" / "vcenter", "vCenter")) == 3  # ESXi, VM örneği, misafir sunucu
    with pytest.raises(MappingError, match="eşleme dosyası yok"):
        load_mappings(REPO / "connectors" / "zabbix", "vCenter")


def test_dry_run_writes_records_and_sends_nothing(tmp_path):
    out = tmp_path / "kayitlar.ndjson"
    report = ConnectorRunner(zabbix(), load_mappings(REPO / "connectors" / "zabbix", "Zabbix"),
                             DryRunGateway(out, batch_size=3)).run()
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert report.completed and report.gateway_state == "dry_run" and len(lines) == 5
    assert lines[0]["attributes"]["name"] == "ABC_1" and lines[2]["class_hint"] == "cmdb_ci_netgear"


def test_every_registered_source_has_mappings_and_a_simulator():
    for spec in SOURCES.values():
        assert load_mappings(REPO / "connectors" / spec.mapping_dir, spec.data_source)
        assert spec.simulator is not None


def test_samples_are_marked_synthetic():
    doc = json.loads((SAMPLES / "hosts.json").read_text(encoding="utf-8"))
    assert "SENTETİK" in doc["_aciklama"]


# ------------------------------------------------------------------ komut satırı
@pytest.fixture
def cli():
    sys.path.insert(0, str(REPO / "tools"))
    import connector_run
    yield connector_run.main
    sys.path.remove(str(REPO / "tools"))


def test_cli_dry_run_with_simulator(cli, capsys, tmp_path):
    rc = cli(["--connector", "zabbix", "--simulate", str(SAMPLES), "--dry-run", "--page-size", "2",
              "--out", str(tmp_path / "k.ndjson")], env={})
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and report["completed"] and report["sent"] == 5 and report["gateway_state"] == "dry_run"


def test_cli_config_errors_exit_2_without_secrets(cli, capsys, tmp_path):
    assert cli(["--connector", "zabbix", "--dry-run"], env={}) == 2
    assert "EVREN_SOURCE_URL tanımlı değil" in capsys.readouterr().err
    env = {"EVREN_SOURCE_URL": "https://zabbix.test", "EVREN_SOURCE_TOKEN_FILE": str(tmp_path / "yok")}
    assert cli(["--connector", "zabbix", "--dry-run"], env=env) == 2
    assert "sır dosyası okunamadı: yok" in capsys.readouterr().err
    assert cli(["--connector", "zabbix", "--simulate", str(SAMPLES)], env={}) == 2
    assert "EVREN_GATEWAY_URL tanımlı değil" in capsys.readouterr().err
    secret = tmp_path / "gw"
    secret.write_text(TOKEN, encoding="utf-8")
    env = {"EVREN_GATEWAY_URL": "https://kullanici:parola@gateway.test", "EVREN_CONNECTOR_TOKEN_FILE": str(secret)}
    assert cli(["--connector", "zabbix", "--simulate", str(SAMPLES)], env=env) == 2
    err = capsys.readouterr().err
    assert "kullanıcı bilgisi" in err and "parola" not in err.replace("kullanıcı bilgisi", "") and TOKEN not in err


def test_cli_unreachable_gateway_exits_1(cli, capsys, tmp_path):
    secret = tmp_path / "gw"
    secret.write_text(TOKEN, encoding="utf-8")
    env = {"EVREN_GATEWAY_URL": "http://127.0.0.1:9", "EVREN_CONNECTOR_TOKEN_FILE": str(secret),
           "EVREN_GATEWAY_ALLOW_HTTP": "1"}
    assert cli(["--connector", "zabbix", "--simulate", str(SAMPLES), "--retries", "0"], env=env) == 1
    err = capsys.readouterr().err
    assert "Çalışma açılamadı" in err and TOKEN not in err
