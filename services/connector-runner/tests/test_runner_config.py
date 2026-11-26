"""Çalıştırıcı yapılandırması (ADR-0021 §2–3): sır değil yol, bilinmeyen anahtar reddi, https, zamanlama."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from evren_connector_runner import ConfigError, RunnerConfig, load_instances
from evren_connector_runner.runner import redact


@pytest.fixture
def secret(tmp_path):
    p = tmp_path / "gw"
    p.write_text("x")
    return str(p)


def load(tmp_path, items, metadata, **kw):
    f = tmp_path / "connectors.yaml"
    f.write_text(yaml.safe_dump({"connectors": items}), encoding="utf-8")
    return load_instances(f, metadata, **kw)


def zabbix(secret, **extra):
    return {"name": "zabbix-dc1", "connector": "zabbix", "url": "https://zabbix.test/api_jsonrpc.php",
            "token_file": secret, "gateway_token_file": secret, **extra}


def test_valid_instance_takes_schedule_from_metadata(tmp_path, metadata, secret):
    (inst,) = load(tmp_path, [zabbix(secret)], metadata)
    assert inst.data_source == "Zabbix" and inst.schedule.expression == "15 */2 * * *"
    assert inst.source_env() == {"EVREN_SOURCE_URL": "https://zabbix.test/api_jsonrpc.php",
                                 "EVREN_SOURCE_TOKEN_FILE": secret, "EVREN_CONNECTOR_TOKEN_FILE": secret}
    assert secret not in repr(inst) and "zabbix.test" not in repr(inst)  # yol ve adres repr'de yok
    (off,) = load(tmp_path, [zabbix(secret, schedule=None)], metadata)
    assert off.schedule is None
    (own,) = load(tmp_path, [zabbix(secret, schedule="0 3 * * *", schedule_mode="delta", page_size=200)], metadata)
    assert own.schedule.expression == "0 3 * * *" and own.schedule_mode == "delta" and own.page_size == 200


@pytest.mark.parametrize(("extra", "message"), [
    ({"password": "x"}, "sır dosyaya yazılmaz"),
    ({"token": "x"}, "sır dosyaya yazılmaz"),
    ({"client_secret": "x"}, "sır dosyaya yazılmaz"),
    ({"colour": "x"}, "bilinmeyen anahtar"),
    ({"url": "http://zabbix.test"}, "https"),
    ({"token_file": "goreli/yol"}, "mutlak"),
    ({"token_file": "/yok/boyle/bir/dosya"}, "bulunamadı"),
    ({"schedule": "0 25 * * *"}, "zamanlama geçersiz"),
    ({"schedule_mode": "full"}, "schedule_mode"),
    ({"api_release": "8.0"}, "api_release"),
    ({"page_size": 0}, "page_size"),
    ({"max_runtime_minutes": 0}, "max_runtime"),
    ({"enabled": "evet"}, "enabled"),
    ({"connector": "splunk"}, "tanımsız kaynak istemcisi"),
    ({"name": "-kotu"}, "örnek adı"),
    ({"simulate": "connectors/zabbix/samples"}, "EVREN_CONNECTOR_ALLOW_SIMULATE"),
])
def test_invalid_instances_are_rejected(tmp_path, metadata, secret, extra, message):
    with pytest.raises(ConfigError, match=message) as exc:
        load(tmp_path, [zabbix(secret, **extra)], metadata)
    assert secret not in str(exc.value)  # ileti dosya yolu içermez


def test_required_credentials_per_auth_type(tmp_path, metadata, secret):
    with pytest.raises(ConfigError, match="gateway_token_file"):
        load(tmp_path, [{k: v for k, v in zabbix(secret).items() if k != "gateway_token_file"}], metadata)
    with pytest.raises(ConfigError, match="token_file"):
        load(tmp_path, [{k: v for k, v in zabbix(secret).items() if k != "token_file"}], metadata)
    vc = {"name": "vc1", "connector": "vcenter", "url": "https://vc.test", "user": "u", "gateway_token_file": secret}
    with pytest.raises(ConfigError, match="password_file"):
        load(tmp_path, [vc], metadata)
    dfn = {"name": "mde", "connector": "defender", "gateway_token_file": secret, "tenant_id": "t", "client_id": "c"}
    with pytest.raises(ConfigError, match="client_secret_file"):
        load(tmp_path, [dfn], metadata)
    (ok,) = load(tmp_path, [{**dfn, "client_secret_file": secret}], metadata)  # Defender: varsayılan API adresi
    assert ok.data_source == "Defender"


def test_simulate_is_dev_only_and_inside_repo(tmp_path, metadata, secret):
    sim = {"name": "zbx-sim", "connector": "zabbix", "gateway_token_file": secret, "simulate": "connectors/zabbix/samples"}
    (inst,) = load(tmp_path, [sim], metadata, allow_simulate=True)
    assert inst.simulate.is_dir()
    for bad in ("../etc", "/etc", "connectors/yok"):
        with pytest.raises(ConfigError, match="depo içindeki"):
            load(tmp_path, [{**sim, "simulate": bad}], metadata, allow_simulate=True)


def test_duplicate_names_and_bad_file(tmp_path, metadata, secret):
    with pytest.raises(ConfigError, match="tekrarlanıyor"):
        load(tmp_path, [zabbix(secret), zabbix(secret)], metadata)
    f = tmp_path / "bos.yaml"
    f.write_text("baska: 1", encoding="utf-8")
    with pytest.raises(ConfigError, match="connectors"):
        load_instances(f, metadata)
    with pytest.raises(ConfigError, match="okunamadı"):
        load_instances(tmp_path / "yok.yaml", metadata)


def test_runner_config_from_env(tmp_path):
    base = {"EVREN_CONNECTORS_FILE": str(tmp_path / "c.yaml"), "EVREN_GATEWAY_URL": "https://gecit:8089"}
    cfg = RunnerConfig.from_env(base)
    assert cfg.tz == ZoneInfo("Europe/Istanbul") and cfg.max_parallel == 2 and cfg.role == "evren_app_connector"
    assert "conninfo" not in repr(cfg) or "postgres" not in repr(cfg)
    with pytest.raises(ConfigError, match="https"):
        RunnerConfig.from_env({**base, "EVREN_GATEWAY_URL": "http://gecit:8089"})
    assert RunnerConfig.from_env({**base, "EVREN_GATEWAY_URL": "http://gecit:8089",
                                  "EVREN_GATEWAY_ALLOW_HTTP": "1"}).gateway_allow_http
    for key, value in (("EVREN_SCHEDULE_TZ", "Mars/Olympus"), ("EVREN_CONNECTOR_MAX_PARALLEL", "0"),
                       ("EVREN_WORKER_POLL_SECONDS", "x")):
        with pytest.raises(ConfigError):
            RunnerConfig.from_env({**base, key: value})
    with pytest.raises(ConfigError, match="EVREN_CONNECTORS_FILE"):
        RunnerConfig.from_env({"EVREN_GATEWAY_URL": "https://g"})
    dsn = tmp_path / "dsn"
    dsn.write_text("postgresql://kullanici:gizli@db/evren\n")
    cfg = RunnerConfig.from_env({**base, "EVREN_DATABASE_URL_FILE": str(dsn), "EVREN_RUNNER_NAME": "dmz-1"})
    assert cfg.conninfo.endswith("/evren") and "gizli" not in repr(cfg)
    assert cfg.worker_id == f"dmz-1:{os.getpid()}"


def test_redact_removes_config_values_and_urls():
    note = redact("Yapılandırma hatası: /run/secrets/zbx okunamadı; https://zabbix.kurum/api 401 " + "x" * 400,
                  ["/run/secrets/zbx"])
    assert "/run/secrets" not in note and "zabbix.kurum" not in note and len(note) == 298
    assert Path("/run/secrets/zbx").name not in note


def test_archive_settings_require_a_valid_key_and_absolute_dir(tmp_path):
    """ADR-0024: arşiv açıksa anahtar zorunlu (düz metin arşiv yok); yol mutlak; saklama süresi sınırlı."""
    from cryptography.fernet import Fernet

    base = {"EVREN_CONNECTORS_FILE": str(tmp_path / "c.yaml"), "EVREN_GATEWAY_URL": "https://gecit:8089",
            "EVREN_ARCHIVE_DIR": str(tmp_path / "arsiv")}
    with pytest.raises(ConfigError, match="anahtar"):
        RunnerConfig.from_env(base)
    key = tmp_path / "archive.key"
    key.write_text("gecersiz", encoding="utf-8")
    with pytest.raises(ConfigError, match="geçersiz"):
        RunnerConfig.from_env({**base, "EVREN_ARCHIVE_KEY_FILE": str(key)})
    key.write_text(Fernet.generate_key().decode(), encoding="utf-8")
    cfg = RunnerConfig.from_env({**base, "EVREN_ARCHIVE_KEY_FILE": str(key), "EVREN_ARCHIVE_RETENTION_DAYS": "7"})
    assert cfg.archive_dir == str(tmp_path / "arsiv") and cfg.archive_retention_days == 7
    for bad in ({"EVREN_ARCHIVE_DIR": "goreli/arsiv"}, {"EVREN_ARCHIVE_RETENTION_DAYS": "0"}):
        with pytest.raises(ConfigError):
            RunnerConfig.from_env({**base, "EVREN_ARCHIVE_KEY_FILE": str(key), **bad})
    assert RunnerConfig.from_env({k: v for k, v in base.items() if k != "EVREN_ARCHIVE_DIR"}).archive_dir is None
