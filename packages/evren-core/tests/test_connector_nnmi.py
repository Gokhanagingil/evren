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
from evren_core.connectors.sources.nnmi import (
    IFACE_PATH,
    PATH,
    NNMiClient,
    NNMiSimulator,
    get_interfaces_envelope,
    get_nodes_envelope,
)

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
    assert [r["id"] for r in rows if r["_type"] == "Node"] == ["1001", "1004", "1007", "1010"]
    assert [r["_type"] for r in rows][:4] == ["Node"] * 4  # arayüzler düğümlerden sonra (ADR-0029)
    assert sim.calls == [(0, 2), (1004, 2), (1010, 2),  # düğümler, sonra arayüzler (aynı anahtar sırası)
                         (0, 2), (3002, 2), (3004, 2), (3101, 2), (3201, 2), (3301, 2)]
    assert sim.paths == [PATH] * 3 + [IFACE_PATH] * 6


UUID = {"1001": "3c6d2a1e-9b7f-4a50-8c21-6e0f1d2b3a40", "1004": "4d7e3b2f-0c80-4b61-9d32-7f102e3c4b51",
        "1007": "5e8f4c30-1d91-4c72-ae43-80213f4d5c62", "1010": "6f905d41-2ea2-4d83-bf54-9132405e6d73"}


def test_interfaces_give_macs_count_and_l2_neighbours():
    """F1-03: arayüzler düğüme MAC, arayüz sayısı ve L2 komşusu olarak katılır; küçük kimlikli uç üst."""
    rows = {r["id"]: r for r in client(page_size=3).fetch() if r["_type"] == "Node"}
    sw, rt, srv = rows["1001"], rows["1004"], rows["1010"]
    assert sw["_mac_addresses"] == ["70:6D:15:AA:00:01", "70:6D:15:AA:00:02", "70:6D:15:AA:00:0A", "70:6D:15:AA:00:FF"]
    assert sw["_interface_count"] == 4  # MAC'i olmayan sanal arayüz (Vlan10) sayılmaz
    assert sw["_l2_down"] == sorted([UUID["1004"], UUID["1007"], UUID["1010"]]) and sw["_l2_up"] == []
    assert rt["_l2_up"] == [UUID["1001"]] and rt["_l2_down"] == [] and srv["_l2_up"] == [UUID["1001"]]


def test_interface_of_unknown_node_is_ignored_and_envelope_is_integer_only():
    doc = json.loads((SAMPLES / "nodes.json").read_text(encoding="utf-8"))
    extra = {"id": "3999", "name": "x", "hostedOnId": "9999", "physicalAddress": "AA:BB:CC:DD:EE:FF",
             "connectionId": "2001"}
    sim = NNMiSimulator(doc["nodes"], interfaces=[*doc["interfaces"], extra])
    rows = {r["id"]: r for r in client(sim).fetch()}
    assert "AA:BB:CC:DD:EE:FF" not in json.dumps(rows) and rows["1004"]["_l2_up"] == [UUID["1001"]]
    assert "3999" not in rows  # kapsam dışı düğümün arayüzü adaptör de olmaz
    env = get_interfaces_envelope(3002, 2).decode()
    assert "getInterfaces" in env and "<value>3002</value>" in env
    with pytest.raises(ValueError):
        get_interfaces_envelope("1; DROP", 2)  # type: ignore[arg-type]


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
    # 4 düğüm + 8 MAC'li arayüz; sunucu düğümü ve onun arayüzü kapsam dışı (ağ cihazı değil)
    assert report.completed and report.fetched == 12 and report.sent == 10
    assert report.by_class == {"cmdb_ci_ip_switch": 1, "cmdb_ci_ip_router": 1, "cmdb_ci_ip_firewall": 1,
                               "cmdb_ci_network_adapter": 7}
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    adapters = [r for r in lines if r["class_hint"] == "cmdb_ci_network_adapter"]
    gi1 = next(r for r in adapters if r["attributes"]["name"] == "Gi1/0/1")
    # Bağımlı CI: ana düğümün kaynak anahtarı, MAC; MAC küresel kimlik için lookup'a girmez (ADR-0029)
    assert gi1["parent"] == {"source_key": UUID["1001"], "data_source": None}
    assert gi1["attributes"]["mac_address"] == "70:6D:15:AA:00:01" and gi1["source_key"] == f"{UUID['1001']}/if/3001"
    assert not gi1["lookup"]["mac_addresses"] and all(r["parent"] for r in adapters)
    recs = {r["attributes"]["name"]: r for r in lines if r["class_hint"] != "cmdb_ci_network_adapter"}
    sw = recs["IST-DC2-CORE-SW01"]
    assert sw["attributes"]["serial_number"] == "FDO2213X0AB" and sw["external_ids"]["nnmi_uuid"].startswith("3c6d")
    assert "systemObjectId" not in json.dumps(sw)  # sysObjectID kimlik değildir, kayda girmez
    assert sw["attributes"]["physical_interface_count"] == 4 and "70:6D:15:AA:00:01" in sw["lookup"]["mac_addresses"]
    assert {(r["direction"], r["target_source_key"]) for r in sw["relations"]} == {
        ("parent", UUID["1004"]), ("parent", UUID["1007"]), ("parent", UUID["1010"])}
    rt = recs["IST-DC2-EDGE-RT01"]
    assert [(r["type"], r["direction"], r["target_source_key"]) for r in rt["relations"]] == [
        ("Connects to::Connected by", "child", UUID["1001"])]  # iki uç aynı ilişkiyi tarif eder


def test_record_and_replay_xml(tmp_path):
    rec = RecordingTransport(NNMiSimulator.from_dir(SAMPLES), tmp_path, {"page_size": 2})
    http = HttpClient("https://nnmi.test", transport=rec, retries=0, auth_header=HttpClient.basic("u", PW))
    recorded = list(NNMiClient(http, page_size=2).fetch())
    rec.save()
    assert sorted(f.suffix for f in tmp_path.iterdir() if f.name != "replay.json") == [".xml"] * 9
    assert PW not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    replay = HttpClient("https://nnmi.test", transport=ReplayTransport.from_dir(tmp_path), retries=0)
    assert list(NNMiClient(replay, page_size=2).fetch()) == recorded


def test_samples_are_synthetic():
    assert "SENTETİK" in json.loads((SAMPLES / "nodes.json").read_text(encoding="utf-8"))["_aciklama"]
