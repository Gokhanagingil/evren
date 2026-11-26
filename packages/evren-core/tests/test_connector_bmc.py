"""BMC Helix Discovery istemcisi (U5.2, ADR-0020): data/search, results_id ile sayfalama, sayı denetimi, eşleme.
Kaynak ``BMCDiscoverySimulator`` (sentetik Host düğümleri); gerçek Discovery kabulü değildir (F1-01, U10)."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient, HttpError, HttpResponse, RecordingTransport, ReplayTransport
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.bmc_discovery import HOST_QUERY, BMCDiscoveryClient, BMCDiscoverySimulator

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "bmc_discovery" / "samples"
TOKEN = "bmc-" + secrets.token_hex(12)  # her çalışmada rastgele


def client(sim=None, page_size: int = 3) -> BMCDiscoveryClient:
    sim = sim or BMCDiscoverySimulator.from_dir(SAMPLES)
    http = HttpClient("https://discovery.test", transport=sim, retries=0, auth_header=HttpClient.bearer(TOKEN))
    return BMCDiscoveryClient(http, page_size=page_size)


def run(c: BMCDiscoveryClient, out: Path | None = None):
    return ConnectorRunner(c, load_mappings(REPO / "connectors" / "bmc_discovery", "BMC"), DryRunGateway(out)).run()


def test_pages_with_results_id_and_offset():
    sim = BMCDiscoverySimulator.from_dir(SAMPLES)
    rows = list(client(sim, page_size=3).fetch())
    assert len(rows) == 4 and len(sim.calls) == 2
    first, second = sim.calls
    assert first["query"] == HOST_QUERY and first["format"] == "object" and first["limit"] == "3"
    assert "results_id" not in first and "offset" not in first
    assert second["results_id"] == "rs-1" and second["offset"] == "3"


def test_count_mismatch_makes_the_run_incomplete():
    report = run(client(BMCDiscoverySimulator.from_dir(SAMPLES, lose_rows=1)))
    assert not report.completed and "sonuç sayısı uyuşmuyor (3/4)" in report.failure


def test_failure_on_second_page_is_incomplete():
    report = run(client(BMCDiscoverySimulator.from_dir(SAMPLES, fail_on_call=2)))
    assert not report.completed and "durum 503" in report.failure and report.fetched == 3


def test_auth_failure_does_not_echo_server_message():
    http = HttpClient("https://discovery.test", transport=BMCDiscoverySimulator.from_dir(SAMPLES), retries=0)
    with pytest.raises(HttpError) as exc:
        list(BMCDiscoveryClient(http).fetch())
    assert exc.value.status == 401 and "Authentication required" not in str(exc.value)


@pytest.mark.parametrize("payload", [{"results": []}, [], [{"count": 1}], [{"results": [{"name": "anahtarsiz"}],
                                                                          "count": 1}]])
def test_unexpected_responses_are_rejected(payload):
    class Fixed:
        def send(self, method, url, headers, body, timeout):
            return HttpResponse(200, {}, json.dumps(payload).encode())

    with pytest.raises(HttpError, match="BMC Discovery"):
        list(BMCDiscoveryClient(HttpClient("https://x.test", transport=Fixed())).fetch())


@pytest.mark.parametrize("release", ["1", "../1.12", "v1.12", "1.12/x"])
def test_release_is_validated(release):
    with pytest.raises(HttpError, match="sürümü geçersiz"):
        BMCDiscoveryClient(HttpClient("https://x.test"), release=release)


def test_mapping_excludes_desktops_and_classifies_hosts(tmp_path):
    out = tmp_path / "k.ndjson"
    report = run(client(), out)
    assert report.completed and report.fetched == 4 and report.sent == 3
    assert report.by_class == {"cmdb_ci_linux_server": 2, "cmdb_ci_esx_server": 1}
    recs = {r["attributes"]["name"]: r for r in (json.loads(x) for x in out.read_text(encoding="utf-8").splitlines())}
    assert "IST-PC-0042" not in recs  # masaüstü kapsam dışı (include_when)
    app = recs["ist-app-02"]
    assert app["attributes"]["serial_number"].startswith("VMware-42 1a") and app["attributes"]["virtual"] is True
    assert app["lookup"]["mac_addresses"] == ["00:50:56:9A:01:02"]
    assert recs["esx01"]["attributes"]["cpu_count"] == 2 and recs["esx01"]["attributes"]["ram"] == 786432


def test_record_and_replay_without_token(tmp_path):
    rec = RecordingTransport(BMCDiscoverySimulator.from_dir(SAMPLES), tmp_path, {"page_size": 3})
    http = HttpClient("https://discovery.test", transport=rec, retries=0, auth_header=HttpClient.bearer(TOKEN))
    recorded = list(BMCDiscoveryClient(http, page_size=3).fetch())
    rec.save()
    assert TOKEN not in "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    replay = HttpClient("https://discovery.test", transport=ReplayTransport.from_dir(tmp_path), retries=0,
                        auth_header="Bearer kayit")
    assert list(BMCDiscoveryClient(replay, page_size=3).fetch()) == recorded


def test_samples_are_synthetic():
    assert "SENTETİK" in json.loads((SAMPLES / "hosts.json").read_text(encoding="utf-8"))["_aciklama"]
