"""ServiceNow CMDB istemcisi (U5.2, ADR-0020): Table API, anahtar sıralı sayfalama, ACL'nin kısalttığı sayfa, delta,
sızıntısız hata, eşleme. Kaynak ``ServiceNowSimulator`` (sentetik tablolar); gerçek ServiceNow kabulü değildir."""
from __future__ import annotations

import json
import secrets
import urllib.parse
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient, HttpError, HttpResponse, RecordingTransport, ReplayTransport
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.servicenow import ServiceNowClient, ServiceNowSimulator, sn_datetime

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "servicenow_cmdb" / "samples"
USER = "svc-evren-ro"
PASSWORD = "sim-" + secrets.token_hex(8)  # her çalışmada rastgele


def client(sim=None, page_size: int = 2, password: str = PASSWORD) -> ServiceNowClient:
    sim = sim or ServiceNowSimulator.from_dir(SAMPLES, user=USER, password=PASSWORD)
    http = HttpClient("https://ornek.service-now.com", transport=sim, retries=0,
                      auth_header=HttpClient.basic(USER, password))
    return ServiceNowClient(http, page_size=page_size)


def queries(sim: ServiceNowSimulator) -> list[str]:
    return [dict(urllib.parse.parse_qsl(c.split("?", 1)[1]))["sysparm_query"] for c in sim.calls]


def test_keyset_paging_until_empty_page():
    sim = ServiceNowSimulator.from_dir(SAMPLES, user=USER, password=PASSWORD)
    rows = list(client(sim).fetch())
    assert [r["_table"] for r in rows] == ["cmdb_ci_server"] * 4 + ["cmdb_ci_netgear"]
    assert len(sim.calls) == 3 + 2  # tablo başına boş sayfaya kadar
    q = queries(sim)
    assert q[0] == "ORDERBYsys_id" and q[1] == f"sys_id>{rows[1]['sys_id']}^ORDERBYsys_id"
    first = dict(urllib.parse.parse_qsl(sim.calls[0].split("?", 1)[1]))
    assert first["sysparm_exclude_reference_link"] == "true" and first["sysparm_display_value"] == "false"
    assert first["sysparm_no_count"] == "true" and first["sysparm_limit"] == "2" and "sys_id" in first["sysparm_fields"]
    assert "sysparm_offset" not in first


def test_page_shortened_by_acl_does_not_stop_paging():
    all_ids = sorted(r["sys_id"] for r in json.loads((SAMPLES / "tables.json").read_text(encoding="utf-8"))
                     ["tables"]["cmdb_ci_server"])
    sim = ServiceNowSimulator.from_dir(SAMPLES, user=USER, password=PASSWORD, hidden={all_ids[0]})
    rows = [r["sys_id"] for r in client(sim).fetch() if r["_table"] == "cmdb_ci_server"]
    assert rows == all_ids[1:]  # ilk sayfa kısa geldi (1 satır) ama sonraki sayfalar da çekildi


def test_delta_uses_utc_updated_on():
    sim = ServiceNowSimulator.from_dir(SAMPLES, user=USER, password=PASSWORD)
    since = datetime(2026, 11, 21, 3, 0, tzinfo=timezone(timedelta(hours=3)))  # İstanbul 03:00 = UTC 00:00
    rows = list(client(sim).fetch("delta", since))
    assert {r["name"] for r in rows} == {"IST-MUH-11", "IST-DC2-CORE-SW01"}
    assert queries(sim)[0] == "sys_updated_on>=2026-11-21 00:00:00^ORDERBYsys_id"
    with pytest.raises(HttpError, match="saat dilimi"):
        sn_datetime(datetime(2026, 11, 21))


def test_auth_failure_does_not_echo_server_detail_or_password():
    with pytest.raises(HttpError) as exc:
        list(client(password="yanlis-" + secrets.token_hex(4)).fetch())
    text = str(exc.value)
    assert exc.value.status == 401 and "Required to provide" not in text and "User Not" not in text


def test_unsorted_or_invalid_rows_are_rejected():
    class Unsorted:
        def send(self, method, url, headers, body, timeout):
            rows = [{"sys_id": "b" * 32}, {"sys_id": "a" * 32}]
            return HttpResponse(200, {}, json.dumps({"result": rows}).encode())

    with pytest.raises(HttpError, match="sıralaması"):
        list(ServiceNowClient(HttpClient("https://x.test", transport=Unsorted())).fetch())

    class NotAList:
        def send(self, method, url, headers, body, timeout):
            return HttpResponse(200, {}, b'{"result": {"a": 1}}')

    with pytest.raises(HttpError, match="beklenen biçimde"):
        list(ServiceNowClient(HttpClient("https://x.test", transport=NotAList())).fetch())


@pytest.mark.parametrize("table", ["cmdb_ci; drop", "../sys_user", "CMDB_CI"])
def test_table_names_are_validated(table):
    with pytest.raises(HttpError, match="Geçersiz tablo"):
        ServiceNowClient(HttpClient("https://x.test"), tables=[table])


def test_mapping_keeps_sys_id_and_reference_ids(tmp_path):
    out = tmp_path / "k.ndjson"
    report = ConnectorRunner(client(), load_mappings(REPO / "connectors" / "servicenow_cmdb", "ServiceNow"),
                             DryRunGateway(out)).run()
    assert report.completed and report.sent == 5
    assert report.by_class == {"cmdb_ci_linux_server": 1, "cmdb_ci_win_server": 1, "cmdb_ci_esx_server": 1,
                               "cmdb_ci_server": 1, "cmdb_ci_ip_switch": 1}
    recs = {r["source_key"]: r for r in (json.loads(x) for x in out.read_text(encoding="utf-8").splitlines())}
    app = recs["5b1c2d3e4f5061728394a5b6c7d8e9f0"]
    assert app["external_ids"] == {"sn_sys_id": "5b1c2d3e4f5061728394a5b6c7d8e9f0"}
    assert app["attributes"]["support_group"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert app["attributes"]["operational_status"] == 1 and "managed_by" not in app["attributes"]  # boş alan yazılmaz
    assert app["lookup"]["mac_addresses"] == ["00:50:56:9a:01:02"]


def test_record_and_replay_without_credentials(tmp_path):
    rec = RecordingTransport(ServiceNowSimulator.from_dir(SAMPLES, user=USER, password=PASSWORD), tmp_path,
                             {"page_size": 2})
    http = HttpClient("https://ornek.service-now.com", transport=rec, retries=0,
                      auth_header=HttpClient.basic(USER, PASSWORD))
    recorded = list(ServiceNowClient(http, page_size=2).fetch())
    rec.save()
    saved = "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    assert PASSWORD not in saved and HttpClient.basic(USER, PASSWORD).split()[1] not in saved
    replay = HttpClient("https://ornek.service-now.com", transport=ReplayTransport.from_dir(tmp_path), retries=0)
    assert list(ServiceNowClient(replay, page_size=2).fetch()) == recorded


def test_samples_are_synthetic_and_sorted_ids_are_valid():
    doc = json.loads((SAMPLES / "tables.json").read_text(encoding="utf-8"))
    assert "SENTETİK" in doc["_aciklama"]
    for rows in doc["tables"].values():
        for r in rows:
            assert len(r["sys_id"]) == 32 and all(isinstance(v, str) for v in r.values())  # Table API: hep metin


def test_utc_now_formatting():
    assert sn_datetime(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02 03:04:05"
