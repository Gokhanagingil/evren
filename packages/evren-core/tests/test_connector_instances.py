"""Bağlayıcı örneği tanımı (ADR-0021 §2, ADR-0038): çalıştırıcı ve panel sihirbazı aynı kuralları kullanır; sihirbazın
ürettiği blok çalıştırıcının yükleyicisiyle geri okunur; sır değeri ve göreli yol reddedilir."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evren_core.connectors.instances import (
    AUTH_FIELDS,
    ConfigError,
    parse_instance,
    render_instance,
    secret_files,
)
from evren_core.connectors.sources import SOURCES
from evren_core.metadata import load_metadata

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def md():
    return load_metadata(REPO / "metadata")


def zabbix(**extra):
    return {"name": "zabbix-dc2", "connector": "zabbix", "url": "https://zabbix.kurum.local/api_jsonrpc.php",
            "token_file": "/run/secrets/zabbix_dc2_token", "gateway_token_file": "/run/secrets/evren_gateway_zabbix-dc2",
            **extra}


def test_render_round_trips_through_the_runner_rules(md, tmp_path):
    raw = zabbix(schedule="15 */2 * * *", page_size=200)
    block = render_instance(raw)
    assert block.startswith("  - name: zabbix-dc2\n") and "token_file: /run/secrets/zabbix_dc2_token" in block
    doc = yaml.safe_load("connectors:\n" + block)
    inst = parse_instance(doc["connectors"][0], md, check_files=False)
    assert inst.data_source == "Zabbix" and inst.schedule.expression == "15 */2 * * *" and inst.page_size == 200
    # Çalıştırıcıda dosyalar da denetlenir
    with pytest.raises(ConfigError, match="dosyası bulunamadı"):
        parse_instance(doc["connectors"][0], md)
    tok = tmp_path / "tok"
    tok.write_text("x")
    ok = parse_instance(zabbix(token_file=str(tok), gateway_token_file=str(tok)), md)
    assert ok.settings["token_file"] == str(tok)


def test_manual_only_schedule_and_secret_list(md):
    raw = zabbix(schedule=None, ca_file="/etc/evren/kurum-ca.pem")
    assert "schedule: null" in render_instance(raw)
    assert parse_instance(raw, md, check_files=False).schedule is None
    assert [s["key"] for s in secret_files(raw)] == ["token_file", "ca_file", "gateway_token_file"]


@pytest.mark.parametrize(("raw", "needle"), [
    ({**zabbix(), "token": "çok-gizli"}, "sır dosyaya yazılmaz"),
    ({**zabbix(), "token_file": "secrets/tok"}, "mutlak dosya yolu"),
    ({**zabbix(), "token_file": "/run/../etc/shadow"}, "mutlak dosya yolu"),
    (zabbix(url="http://zabbix.kurum.local"), "https olmalı"),
    (zabbix(connector="yok"), "tanımsız kaynak istemcisi"),
    (zabbix(schedule="her gün"), "zamanlama geçersiz"),
    ({**zabbix(), "token_file": None}, "gerekli: token_file"),
    (zabbix(name="-kötü"), "örnek adı geçersiz"),
    (zabbix(page_size=0), "page_size"),
])
def test_invalid_definitions_are_rejected_without_echoing_secrets(md, raw, needle):
    with pytest.raises(ConfigError, match=needle) as exc:
        parse_instance({k: v for k, v in raw.items() if v is not None}, md, check_files=False)
    assert "çok-gizli" not in str(exc.value)


def test_every_source_has_auth_fields_for_the_wizard():
    for spec in SOURCES.values():
        assert spec.auth in AUTH_FIELDS and any(secret for _k, _l, secret in AUTH_FIELDS[spec.auth])
