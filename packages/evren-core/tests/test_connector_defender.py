"""Microsoft Defender for Endpoint istemcisi (U5.2, ADR-0020): Entra istemci kimliği, nextLink güvenliği, belirteç
yenileme, kısıtlı veri, kayıtta sır yok. Kaynak ``DefenderSimulator`` (sentetik makineler); gerçek kabul değildir."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import pytest

from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import (
    HttpClient,
    HttpError,
    RecordingTransport,
    ReplayTransport,
    request_fingerprint,
)
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources.defender import DEFAULT_SCOPE, DefenderClient, DefenderSimulator

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "defender" / "samples"
SECRET = "sim-" + secrets.token_hex(12)  # her çalışmada rastgele istemci sırrı
TENANT = "11111111-2222-3333-4444-555555555555"


def client(sim=None, page_size: int = 2, secret: str = SECRET) -> DefenderClient:
    sim = sim or DefenderSimulator.from_dir(SAMPLES, secret=SECRET)
    api = HttpClient(DefenderSimulator.API, transport=sim, retries=0)
    login = HttpClient("https://login.microsoftonline.com", transport=sim, retries=0)
    return DefenderClient(api, login, tenant=TENANT, client_id="app-id", client_secret=secret, page_size=page_size)


def run(c: DefenderClient, out: Path | None = None):
    return ConnectorRunner(c, load_mappings(REPO / "connectors" / "defender", "Defender"), DryRunGateway(out)).run()


def test_client_credentials_flow_and_paging_via_next_link():
    sim = DefenderSimulator.from_dir(SAMPLES, secret=SECRET)
    c = client(sim)
    rows = list(c.fetch())
    assert len(rows) == 3 and sim.api_calls == 2
    assert sim.forms == [{"grant_type": "client_credentials", "client_id": "app-id", "client_secret": SECRET,
                          "scope": DEFAULT_SCOPE}]
    assert "Authorization" not in c.http.extra_headers  # belirteç istemcide kalmaz


def test_expired_token_is_refreshed_once():
    sim = DefenderSimulator.from_dir(SAMPLES, secret=SECRET, expire_after=1)
    c = client(sim)
    assert len(list(c.fetch())) == 3 and c.token_requests == 2


def test_next_link_to_another_host_is_not_followed():
    sim = DefenderSimulator.from_dir(SAMPLES, secret=SECRET, foreign_next=True)
    report = run(client(sim))
    assert not report.completed and "başka bir adrese" in report.failure and report.fetched == 2


def test_wrong_secret_fails_without_echoing_details():
    with pytest.raises(HttpError) as exc:
        list(client(secret="yanlis-" + secrets.token_hex(4)).fetch())
    text = str(exc.value)
    assert exc.value.status == 401 and "AADSTS" not in text and "yanlis-" not in text and SECRET not in text


@pytest.mark.parametrize("tenant", ["../x", "kiraci", "11111111-2222-3333-4444-55555555555/"])
def test_tenant_is_validated(tenant):
    with pytest.raises(HttpError, match="kiracı"):
        DefenderClient(HttpClient("https://x.test"), HttpClient("https://y.test"), tenant=tenant, client_id="a",
                       client_secret=SECRET)


def test_mapping_keeps_restricted_posture_out_of_ci_attributes(tmp_path):
    out = tmp_path / "k.ndjson"
    report = run(client(), out)
    assert report.completed and report.fetched == 3 and report.sent == 2
    assert report.by_class == {"cmdb_ci_linux_server": 1, "cmdb_ci_win_server": 1}
    recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    text = json.dumps(recs)
    assert "riskScore" not in text and "exposureLevel" not in text  # kısıtlı veri CI özniteliği olmaz
    win = next(r for r in recs if r["class_hint"] == "cmdb_ci_win_server")
    assert win["attributes"]["name"] == "ist-muh-11" and win["external_ids"]["aad_device_id"].startswith("6b1d")
    assert win["lookup"]["mac_addresses"] == ["0050569A1111"]


def test_record_and_replay_without_secret_or_token(tmp_path):
    rec = RecordingTransport(DefenderSimulator.from_dir(SAMPLES, secret=SECRET), tmp_path, {"page_size": 2})
    api = HttpClient(DefenderSimulator.API, transport=rec, retries=0)
    login = HttpClient("https://login.microsoftonline.com", transport=rec, retries=0)
    recorded = list(DefenderClient(api, login, tenant=TENANT, client_id="app-id", client_secret=SECRET,
                                   page_size=2).fetch())
    rec.save()
    saved = "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    assert SECRET not in saved and "sim-token-1" not in saved and "kayit-yer-tutucu" in saved
    sim2 = ReplayTransport.from_dir(tmp_path)
    api2 = HttpClient(DefenderSimulator.API, transport=sim2, retries=0)
    login2 = HttpClient("https://login.microsoftonline.com", transport=sim2, retries=0)
    other = "baska-" + secrets.token_hex(4)
    assert list(DefenderClient(api2, login2, tenant=TENANT, client_id="app-id", client_secret=other,
                               page_size=2).fetch()) == recorded


def test_form_body_secrets_are_masked_in_fingerprints():
    a = request_fingerprint("POST", "https://l.test/t/oauth2/v2.0/token", b"client_secret=aaa&scope=s")
    b = request_fingerprint("POST", "https://l.test/t/oauth2/v2.0/token", b"client_secret=bbb&scope=s")
    c = request_fingerprint("POST", "https://l.test/t/oauth2/v2.0/token", b"client_secret=bbb&scope=x")
    assert a == b and a != c


def test_cli_requires_entra_settings(capsys):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import connector_run
        assert connector_run.main(["--connector", "defender", "--dry-run"], env={}) == 2
        assert "EVREN_SOURCE_TENANT_ID tanımlı değil" in capsys.readouterr().err
        assert connector_run.main(["--connector", "defender", "--simulate", str(SAMPLES), "--dry-run"], env={}) == 0
    finally:
        sys.path.remove(str(REPO / "tools"))


def test_samples_are_synthetic():
    assert "SENTETİK" in json.loads((SAMPLES / "machines.json").read_text(encoding="utf-8"))["_aciklama"]
