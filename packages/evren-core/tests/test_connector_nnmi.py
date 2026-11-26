"""NNMi istemcisi ve güvenli XML ayrıştırma (U5.2, ADR-0020 §13): SOAP getNodes, id sırasıyla sayfalama, sıralama
güvencesi, SOAP Fault, DTD/varlık reddi, kayıt/oynatma. Kaynak ``NNMiSimulator`` (sentetik); gerçek kabul değildir."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient, HttpError, HttpResponse, RecordingTransport, ReplayTransport
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.safe_xml import MAX_DEPTH, UnsafeXml, parse
from evren_core.connectors.sources.nnmi import NNMiClient, NNMiSimulator, get_nodes_envelope

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "nnmi" / "samples"
PW = "sim-" + secrets.token_hex(8)


def client(sim=None, page_size: int = 2) -> NNMiClient:
    sim = sim or NNMiSimulator.from_dir(SAMPLES)
    return NNMiClient(HttpClient("https://nnmi.test", transport=sim, retries=0,
                                 auth_header=HttpClient.basic("svc-evren", PW)), page_size=page_size)


def run(c: NNMiClient, out: Path | None = None):
    return ConnectorRunner(c, load_mappings(REPO / "connectors" / "nnmi", "NNMi"), DryRunGateway(out)).run()


# ------------------------------------------------------------------ güvenli XML
@pytest.mark.parametrize("doc", [
    b'<!DOCTYPE x [<!ENTITY a "aaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]><x>&b;</x>',          # varlık genişletme
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',  # XXE
    b'<!DOCTYPE x SYSTEM "http://kotu.example/x.dtd"><x/>',                                     # dış DTD
    b'<x>&tanimsiz;</x>', b'<x><y></x>', b'',
])
def test_unsafe_or_broken_xml_is_rejected(doc):
    with pytest.raises(UnsafeXml):
        parse(doc)


def test_depth_is_limited_and_namespaces_are_dropped():
    with pytest.raises(UnsafeXml, match="derin"):
        parse(b"<a>" * (MAX_DEPTH + 1) + b"</a>" * (MAX_DEPTH + 1))
    root = parse(b'<S:E xmlns:S="urn:s"><S:B><r><n>a &amp; b</n></r></S:B></S:E>')
    assert root.tag == "E" and root.find("B").find("r").find("n").text == "a & b"


def test_request_envelope_contains_only_integers():
    env = get_nodes_envelope(1004, 2).decode()
    assert "<operator>GT</operator><value>1004</value>" in env and "<name>maxObjects</name><value>2</value>" in env
    with pytest.raises(ValueError):
        get_nodes_envelope("1 or 1=1", 2)  # type: ignore[arg-type]


# ------------------------------------------------------------------ istemci
def test_pages_by_id_until_empty_page():
    sim = NNMiSimulator.from_dir(SAMPLES)
    rows = list(client(sim).fetch())
    assert [r["id"] for r in rows] == ["1001", "1004", "1007", "1010"]
    assert sim.calls == [(0, 2), (1004, 2), (1010, 2)]


def test_unordered_pages_stop_the_run_instead_of_skipping():
    report = run(client(NNMiSimulator.from_dir(SAMPLES, unordered=True)))
    assert not report.completed and "kimlik sırasında gelmedi" in report.failure


def test_soap_fault_is_reported_without_details():
    with pytest.raises(HttpError) as exc:
        list(client(NNMiSimulator.from_dir(SAMPLES, fault=True)).fetch())
    assert "gizli ayrıntı" not in str(exc.value)


def test_hostile_xml_response_is_rejected():
    class Hostile:
        def send(self, method, url, headers, body, timeout):
            doc = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><Envelope><Body>&e;</Body></Envelope>'
            return HttpResponse(200, {"content-type": "text/xml"}, doc)

    with pytest.raises(HttpError, match="reddedildi"):
        list(NNMiClient(HttpClient("https://x.test", transport=Hostile())).fetch())


def test_missing_auth_is_401():
    with pytest.raises(HttpError) as exc:
        list(NNMiClient(HttpClient("https://nnmi.test", transport=NNMiSimulator.from_dir(SAMPLES), retries=0)).fetch())
    assert exc.value.status == 401


def test_mapping_keeps_network_devices_only(tmp_path):
    out = tmp_path / "k.ndjson"
    report = run(client(), out)
    assert report.completed and report.fetched == 4 and report.sent == 3
    assert report.by_class == {"cmdb_ci_ip_switch": 1, "cmdb_ci_ip_router": 1, "cmdb_ci_ip_firewall": 1}
    recs = {r["attributes"]["name"]: r for r in (json.loads(x) for x in out.read_text(encoding="utf-8").splitlines())}
    sw = recs["IST-DC2-CORE-SW01"]
    assert sw["attributes"]["serial_number"] == "FDO2213X0AB" and sw["external_ids"]["nnmi_uuid"].startswith("3c6d")
    assert "systemObjectId" not in json.dumps(sw)  # sysObjectID kimlik değildir, kayda girmez


def test_record_and_replay_xml(tmp_path):
    rec = RecordingTransport(NNMiSimulator.from_dir(SAMPLES), tmp_path, {"page_size": 2})
    http = HttpClient("https://nnmi.test", transport=rec, retries=0, auth_header=HttpClient.basic("u", PW))
    recorded = list(NNMiClient(http, page_size=2).fetch())
    rec.save()
    assert sorted(f.suffix for f in tmp_path.iterdir() if f.name != "replay.json") == [".xml"] * 3
    assert PW not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    replay = HttpClient("https://nnmi.test", transport=ReplayTransport.from_dir(tmp_path), retries=0)
    assert list(NNMiClient(replay, page_size=2).fetch()) == recorded


def test_samples_are_synthetic():
    assert "SENTETİK" in json.loads((SAMPLES / "nodes.json").read_text(encoding="utf-8"))["_aciklama"]
