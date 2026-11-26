"""vCenter istemcisi (U5.2, ADR-0020): VI/JSON oturumu, sayfalı özellik toplama, değer açma, eşleme, oturum kapatma.

Kaynak ``VCenterSimulator`` (VI/JSON'ın kullanılan alt kümesi, sentetik envanter). Gerçek vCenter kabulü değildir.
"""
from __future__ import annotations

import copy
import json
import secrets
from pathlib import Path

import pytest

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient, HttpError, RecordingTransport, ReplayTransport
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.vcenter import (
    SESSION_HEADER,
    VCenterClient,
    VCenterSimulator,
    unbox,
    vmware_serial,
)

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "vcenter" / "samples"
VC = "8d5f1a2b-0000-4c3d-9e8f-abcdef012345"
PASSWORD = "sim-" + secrets.token_hex(8)  # her çalışmada rastgele simülatör parolası
WRONG = "yanlis-" + secrets.token_hex(4)
OTHER = "baska-" + secrets.token_hex(4)


def client(sim=None, page_size: int = 2, password: str = PASSWORD, retries: int = 0) -> VCenterClient:
    sim = sim or VCenterSimulator.from_dir(SAMPLES, password=PASSWORD)
    http = HttpClient("https://vcenter.test", transport=sim, retries=retries, sleep=lambda s: None)
    return VCenterClient(http, user="svc-evren@vsphere.local", password=password, page_size=page_size)


def run(c: VCenterClient, out: Path | None = None):
    return ConnectorRunner(c, load_mappings(REPO / "connectors" / "vcenter", "vCenter"), DryRunGateway(out)).run()


# ------------------------------------------------------------------ değer biçimi
def test_unbox_vi_json_values():
    assert unbox({"_typeName": "string", "_value": "a"}) == "a"
    assert unbox({"_typeName": "boolean", "_value": False}) is False
    assert unbox({"_typeName": "ManagedObjectReference", "type": "HostSystem", "value": "host-1"}) == "host-1"
    nic = {"_typeName": "ArrayOfGuestNicInfo",
           "_value": [{"_typeName": "GuestNicInfo", "macAddress": "00:50:56:00:00:01", "ipAddress": ["10.0.0.1"]}]}
    assert unbox(nic) == [{"macAddress": "00:50:56:00:00:01", "ipAddress": ["10.0.0.1"]}]


def test_vmware_serial_from_bios_uuid():
    assert vmware_serial("421A2B3C-4D5E-6F70-8192-A3B4C5D6E7F8") == \
        "VMware-42 1a 2b 3c 4d 5e 6f 70-81 92 a3 b4 c5 d6 e7 f8"
    assert vmware_serial("bozuk") is None and vmware_serial(None) is None


def test_release_is_validated():
    with pytest.raises(HttpError, match="sürümü geçersiz"):
        VCenterClient(HttpClient("https://vcenter.test"), user="u", password=OTHER, release="../x")


# ------------------------------------------------------------------ oturum ve sayfalama
def test_fetch_logs_in_pages_with_tokens_and_logs_out():
    sim = VCenterSimulator.from_dir(SAMPLES, password=PASSWORD)
    c = client(sim, page_size=2)
    docs = list(c.fetch())
    assert [d["_moId"] for d in docs] == ["host-221", "host-222", "host-310",
                                          "vm-1042", "vm-1043", "vm-1044", "vm-1050", "vm-1051"]
    assert len(sim.tokens) == 0 and len(sim.destroyed) == 2  # her görünüm kapatıldı
    assert sim.logged_out == 1 and not sim.sessions
    assert SESSION_HEADER not in c.http.extra_headers  # oturum kimliği istemcide kalmaz


def test_documents_use_api_property_names_and_qualified_keys():
    docs = {d["_moId"]: d for d in client().fetch()}
    host, vm = docs["host-221"], docs["vm-1042"]
    assert host["_type"] == "HostSystem" and host["_key"] == f"{VC}/host-221"
    assert host["summary"]["hardware"]["memorySize"] == 824633720832
    assert host["config"]["network"]["pnic"][0] == {"device": "vmnic0", "mac": "94:40:c9:a1:02:10"}
    assert vm["runtime"]["host"] == "host-221" and vm["_ref"]["host"] == f"{VC}/host-221"
    assert vm["config"]["template"] is False and vm["guest"]["net"][0]["macAddress"] == "00:50:56:9a:01:02"
    assert vm["_derived"]["vmwareSerial"] == "VMware-42 1a 2b 3c 4d 5e 6f 70-81 92 a3 b4 c5 d6 e7 f8"
    assert vm["_vcenter"]["instanceUuid"] == VC.upper()


def test_wrong_password_fails_without_echoing_credentials():
    with pytest.raises(HttpError) as exc:
        list(client(password=WRONG).fetch())
    text = str(exc.value)
    assert exc.value.status == 500 and "Login" in text and WRONG not in text and "svc-evren" not in text


def test_calls_without_session_are_rejected_by_simulator():
    sim = VCenterSimulator.from_dir(SAMPLES)
    http = HttpClient("https://vcenter.test", transport=sim, retries=0)
    with pytest.raises(HttpError) as exc:
        http.post_json("/sdk/vim25/8.0.1.0/ViewManager/ViewManager/CreateContainerView", {})
    assert exc.value.status == 401


def test_failure_mid_paging_still_destroys_view_and_logs_out():
    sim = VCenterSimulator.from_dir(SAMPLES, password=PASSWORD, fail_on_call=5)  # 2. sayfa (Continue) 503
    c = client(sim, page_size=2)
    report = run(c)
    assert not report.completed and "durum 503" in report.failure and report.fetched == 2
    assert sim.destroyed and sim.logged_out == 1


def test_missing_properties_are_flagged_not_hidden():
    inv = json.loads((SAMPLES / "inventory.json").read_text(encoding="utf-8"))
    broken = copy.deepcopy(inv)
    broken["vms"][0]["_missing"] = ["config"]
    del broken["vms"][0]["config"]
    docs = {d["_moId"]: d for d in client(VCenterSimulator(broken)).fetch()}
    assert docs["vm-1042"]["_missing"] == ["config"]
    report = run(client(VCenterSimulator(broken)))
    assert not report.completed and report.errors == 1  # kimliksiz VM çalışmayı tamamlanmamış yapar


# ------------------------------------------------------------------ eşleme
def test_mapping_produces_hosts_vm_instances_and_guests(tmp_path):
    out = tmp_path / "k.ndjson"
    report = run(client(), out)
    assert report.completed and report.fetched == 8
    assert report.by_class == {"cmdb_ci_esx_server": 3, "cmdb_ci_vmware_instance": 4, "cmdb_ci_linux_server": 2,
                               "cmdb_ci_win_server": 1}
    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    by_key = {r["source_key"]: r for r in recs}
    esx = by_key[f"{VC}/host-221"]
    assert esx["attributes"]["name"] == "esx01" and esx["attributes"]["ram"] == 786432
    assert esx["attributes"]["serial_number"] == "CZ2150ABCD" and esx["attributes"]["virtual"] is False
    assert esx["lookup"]["mac_addresses"] == ["94:40:c9:a1:02:10", "94:40:c9:a1:02:11"]
    assert by_key[f"{VC}/host-310"]["attributes"]["name"] == "10.40.1.31"  # IP ad olarak kalır (ad kimliği sayılmaz)
    vm = by_key["5003A1B2-C3D4-E5F6-0718-293A4B5C6D7E"]
    guest = by_key["guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D7E"]
    assert vm["relations"] == [{"type": "Registered on::Has registered", "direction": "parent",
                                "target_source_key": f"{VC}/host-221", "target_data_source": None,
                                "target_class_hint": "cmdb_ci_esx_server"}]
    assert {r["target_source_key"] for r in guest["relations"]} == {vm["source_key"], f"{VC}/host-221"}
    assert guest["attributes"]["serial_number"].startswith("VMware-42 1a")
    keys = set(by_key)
    assert "5003A1B2-C3D4-E5F6-0718-293A4B5C6D90" not in keys  # şablon CI değildir
    assert "guest:5003A1B2-C3D4-E5F6-0718-293A4B5C6D81" not in keys  # VMware Tools yok → misafir sunucu yok
    assert not any(k.endswith(("/vm-1042", "/vm-1043")) for k in keys)  # ESXi eşlemesi VM'lere uygulanmaz


def test_relation_targets_match_host_source_keys(tmp_path):
    out = tmp_path / "k.ndjson"
    run(client(), out)
    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    hosts = {r["source_key"] for r in recs if r["class_hint"] == "cmdb_ci_esx_server"}
    targets = {rel["target_source_key"] for r in recs for rel in r["relations"]
               if rel["target_class_hint"] == "cmdb_ci_esx_server"}
    assert targets and targets <= hosts


def test_record_and_replay_vcenter_without_secrets(tmp_path):
    rec = RecordingTransport(VCenterSimulator.from_dir(SAMPLES, password=PASSWORD), tmp_path, {"page_size": 3})
    http = HttpClient("https://vcenter.test", transport=rec, retries=0)
    recorded = list(VCenterClient(http, user="svc-evren", password=PASSWORD, page_size=3).fetch())
    rec.save()
    saved = (tmp_path / "replay.json").read_text(encoding="utf-8")
    assert '"sim-1"' not in saved and "kayit-yer-tutucu" in saved  # oturum kimliği diske yazılmaz
    assert PASSWORD not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    import hashlib
    login = json.dumps({"userName": "svc-evren", "password": PASSWORD}, ensure_ascii=False).encode()
    assert hashlib.sha256(login).hexdigest()[:16] not in saved  # parolanın özeti de yok
    # oynatma gerçek kimlik bilgisi istemez: farklı kullanıcı/parola aynı kayda eşlenir
    http2 = HttpClient("https://vcenter.test", transport=ReplayTransport.from_dir(tmp_path), retries=0)
    assert list(VCenterClient(http2, user="baska", password=OTHER, page_size=3).fetch()) == recorded
