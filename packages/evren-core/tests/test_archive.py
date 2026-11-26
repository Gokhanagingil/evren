"""Ham yük arşivi (U5.4, ADR-0024): şifreli parçalar, manifest, bütünlük, anahtar döndürme, yol geçişi, saklama,
arşivden yeniden işleme. Örnek veri sentetiktir (Zabbix simülatörü)."""
from __future__ import annotations

import json
import os
import stat
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from evren_core.connectors.archive import ArchiveError, ArchiveSource, ArchiveWriter, load_keys, open_run, prune, runs

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "zabbix" / "samples"
NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
SECRET_VALUE = "gizli-kaynak-degeri-" + uuid.uuid4().hex[:6]  # kaynak verisi gibi; manifest ve hatada görünmemeli


@pytest.fixture
def keyfile(tmp_path):
    p = tmp_path / "archive.key"
    p.write_text(Fernet.generate_key().decode() + "\n", encoding="utf-8")
    return p


def write_run(root, keys, n=5, *, started=NOW, completed=True, segment=2, source="Zabbix"):
    w = ArchiveWriter(root, source, uuid.uuid4(), keys, started_at=started, segment_records=segment)
    refs = [w.add({"hostid": str(i), "secret": SECRET_VALUE}) for i in range(n)]
    manifest = w.close(completed=completed)
    return w, refs, manifest


def test_round_trip_segments_refs_and_manifest_without_data(tmp_path, keyfile):
    keys = load_keys(keyfile)
    w, refs, manifest = write_run(tmp_path / "a", keys)
    assert manifest["records"] == 5 and [s["records"] for s in manifest["segments"]] == [2, 2, 1]
    assert refs[0] == f"archive:Zabbix/2026-11-26/{w.run_id}/1/0" and refs[4] == f"archive:Zabbix/2026-11-26/{w.run_id}/3/0"
    text = (w.dir / "manifest.json").read_text(encoding="utf-8")
    assert SECRET_VALUE not in text and manifest["key_id"] == keys.key_id
    for f in w.dir.iterdir():
        assert SECRET_VALUE.encode() not in f.read_bytes()  # parçalar şifreli
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(w.dir.stat().st_mode) == 0o700
    run = open_run(tmp_path / "a", w.dir.relative_to(tmp_path / "a"), keys)
    got = list(run.records())
    assert [r for r, _ in got] == refs and [raw["hostid"] for _, raw in got] == ["0", "1", "2", "3", "4"]
    listed = runs(tmp_path / "a")
    assert listed[0]["run_id"] == str(w.run_id) and listed[0]["records"] == 5 and SECRET_VALUE not in json.dumps(listed)


def test_tampered_segment_and_wrong_key_stop_without_content(tmp_path, keyfile):
    keys = load_keys(keyfile)
    w, _, _ = write_run(tmp_path, keys)
    other = tmp_path / "other.key"
    other.write_text(Fernet.generate_key().decode(), encoding="utf-8")
    with pytest.raises(ArchiveError, match="çözülemedi") as exc:
        list(open_run(tmp_path, w.dir, load_keys(other)).records())
    assert SECRET_VALUE not in str(exc.value)
    seg = w.dir / "segment-00002.enc"
    data = bytearray(seg.read_bytes())
    data[10] ^= 0x01
    seg.write_bytes(bytes(data))
    with pytest.raises(ArchiveError, match="kurcalanmış"):
        list(open_run(tmp_path, w.dir, keys).records())


def test_key_rotation_reads_old_archives(tmp_path, keyfile):
    old_keys = load_keys(keyfile)
    w, refs, _ = write_run(tmp_path, old_keys)
    rotated = tmp_path / "rotated.key"
    rotated.write_text(Fernet.generate_key().decode() + "\n" + keyfile.read_text(encoding="utf-8"), encoding="utf-8")
    new_keys = load_keys(rotated)
    assert new_keys.key_id != old_keys.key_id  # yeni yazım yeni anahtarla
    assert [r for r, _ in open_run(tmp_path, w.dir, new_keys).records()] == refs


@pytest.mark.parametrize("bad", ["../dışarı", "Zabbix/2026-11-26", "Zabbix/dun/0d5f", "/etc", "Zabbix/2026-11-26/yok"])
def test_paths_outside_or_malformed_are_rejected(tmp_path, keyfile, bad):
    keys = load_keys(keyfile)
    write_run(tmp_path, keys)
    with pytest.raises(ArchiveError):
        open_run(tmp_path, bad, keys)


def test_half_archive_without_manifest_is_not_listed_or_opened(tmp_path, keyfile):
    keys = load_keys(keyfile)
    w = ArchiveWriter(tmp_path, "Zabbix", uuid.uuid4(), keys, started_at=NOW)
    w.add({"hostid": "1"})
    assert runs(tmp_path) == []
    with pytest.raises(ArchiveError, match="manifest"):
        open_run(tmp_path, w.dir, keys)
    with pytest.raises(ArchiveError):
        load_keys(tmp_path / "yok.key")
    (tmp_path / "bos.key").write_text("", encoding="utf-8")
    with pytest.raises(ArchiveError, match="boş"):
        load_keys(tmp_path / "bos.key")


def test_prune_removes_only_expired_conforming_run_dirs(tmp_path, keyfile):
    keys = load_keys(keyfile)
    old, _, _ = write_run(tmp_path, keys, started=NOW - timedelta(days=40))
    new, _, _ = write_run(tmp_path, keys, started=NOW - timedelta(days=5))
    stray = tmp_path / "Zabbix" / (NOW - timedelta(days=90)).date().isoformat() / "elle-birakilan"
    stray.mkdir(parents=True)
    removed = prune(tmp_path, 30, now=NOW)
    assert removed == [str(old.dir.relative_to(tmp_path))] and not old.dir.exists() and new.dir.exists()
    assert stray.exists()  # biçime uymayan dizine dokunulmaz
    with pytest.raises(ArchiveError):
        prune(tmp_path, 0, now=NOW)


def test_archive_source_mode_follows_original_completion(tmp_path, keyfile):
    keys = load_keys(keyfile)
    done, _, _ = write_run(tmp_path, keys, completed=True)
    half, _, _ = write_run(tmp_path, keys, completed=False)
    assert ArchiveSource(open_run(tmp_path, done.dir, keys)).mode == "snapshot"
    assert ArchiveSource(open_run(tmp_path, half.dir, keys)).mode == "delta"  # eksik arşiv "kaynakta yok" demesin


# ------------------------------------------------------------------ komut satırı: arşivle ve yeniden işle
@pytest.fixture
def cli():
    sys.path.insert(0, str(REPO / "tools"))
    import connector_run
    yield connector_run.main
    sys.path.remove(str(REPO / "tools"))


def test_cli_archives_and_reprocesses_without_source(cli, capsys, tmp_path, keyfile):
    env = {"EVREN_ARCHIVE_DIR": str(tmp_path / "arsiv"), "EVREN_ARCHIVE_KEY_FILE": str(keyfile)}
    first = tmp_path / "ilk.ndjson"
    assert cli(["--connector", "zabbix", "--simulate", str(SAMPLES), "--dry-run", "--out", str(first)], env=env) == 0
    report = json.loads(capsys.readouterr().out)
    listed = runs(tmp_path / "arsiv")
    assert len(listed) == 1 and listed[0]["records"] == report["fetched"] and listed[0]["run_id"] == report["run_id"]
    original = [json.loads(x) for x in first.read_text(encoding="utf-8").splitlines()]
    assert all(r["raw_ref"].startswith("archive:Zabbix/") for r in original)
    again = tmp_path / "yeniden.ndjson"
    rc = cli(["--connector", "zabbix", "--from-archive", listed[0]["path"], "--dry-run", "--out", str(again)], env=env)
    report2 = json.loads(capsys.readouterr().out)
    assert rc == 0 and report2["from_archive"] and report2["sent"] == report["sent"] and report2["mode"] == "snapshot"
    redo = [json.loads(x) for x in again.read_text(encoding="utf-8").splitlines()]
    strip = lambda r: {k: v for k, v in r.items() if k not in ("run_id", "observed_at")}  # noqa: E731
    assert [strip(r) for r in redo] == [strip(r) for r in original]  # aynı kayıtlar, asıl raw_ref korunur
    assert len(runs(tmp_path / "arsiv")) == 1  # yeniden işleme yeni arşiv yazmaz
    # başka kaynağın istemcisiyle oynatılamaz; anahtarsız arşiv açılmaz
    assert cli(["--connector", "vcenter", "--from-archive", listed[0]["path"], "--dry-run"], env=env) == 2
    assert "Zabbix kaynağına ait" in capsys.readouterr().err
    no_key = {"EVREN_ARCHIVE_DIR": str(tmp_path / "arsiv")}
    assert cli(["--connector", "zabbix", "--simulate", str(SAMPLES), "--dry-run"], env=no_key) == 2
    assert "anahtar" in capsys.readouterr().err
    assert os.listdir(tmp_path / "arsiv" / "Zabbix")  # ilk arşiv yerinde
