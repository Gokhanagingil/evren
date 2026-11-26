"""Yetkisiz değişiklik tespiti (F1-04, ADR-0025): kural dosyası, onaylı pencere, değişiklik kayıtlarının dönüşümü ve
ServiceNow değişiklik bağlayıcısının sorguları. Örnek veri sentetiktir."""
from __future__ import annotations

import copy
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from evren_core.change_control import ChangeControlError, ChangePolicy, ChangeRecord, evaluate, validate_change_control
from evren_core.connectors.gateway import DryRunGateway
from evren_core.connectors.http import HttpClient
from evren_core.connectors.runtime import ConnectorRunner, load_mappings
from evren_core.connectors.sources import SOURCES
from evren_core.connectors.sources.servicenow import ServiceNowSimulator
from evren_core.metadata import validate
from evren_core.reference import ReferenceDataError, coerce_reference

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "connectors" / "servicenow_change" / "samples"
DOC = yaml.safe_load((REPO / "metadata" / "change_control.yaml").read_text(encoding="utf-8"))
POLICY = ChangePolicy.from_document(DOC)
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


def chg(number="CHG1", *, type="normal", state="implement", approval="approved", start=None, end=None,
        work_start=None, work_end=None) -> ChangeRecord:
    return ChangeRecord(sys_id=number.lower().ljust(32, "0")[:32], number=number, type=type, state=state,
                        approval=approval, start_date=start, end_date=end, work_start=work_start, work_end=work_end)


# ---------------------------------------------------------------------------------------------- kurallar
def test_policy_from_repository_file(metadata):
    assert POLICY.enabled and "Network" in POLICY.observer_roles and "Monitoring" not in POLICY.observer_roles
    assert POLICY.grace == timedelta(minutes=60) and POLICY.recheck == timedelta(days=7)
    # izlenen alanlar alt sınıflara kalıtılır
    assert {"ram", "os", "serial_number"} <= POLICY.watched_for("cmdb_ci_esx_server", metadata.hierarchy)
    assert POLICY.watched_for("cmdb_ci_ip_switch", metadata.hierarchy) == {"firmware_version", "serial_number"}
    assert POLICY.watched_for("cmdb_ci_appl", metadata.hierarchy) == frozenset()
    assert POLICY.severity_for({"used_for": "Production"}) == "high"
    assert POLICY.severity_for({"used_for": "Test"}) == "medium"
    assert not ChangePolicy.from_document(None).enabled


@pytest.mark.parametrize(("path", "value", "message"), [
    (("authorization", "states"), ["closed", "-1"], "sözlükte olmayan değer '-1'"),
    (("authorization", "states"), ["other"], "tanınmayan değer"),
    (("authorization", "approvals"), [], "approvals boş olamaz"),
    (("authorization", "tolerance_minutes"), 0, "pozitif tam sayı"),
    (("evaluation", "grace_minutes"), "60", "pozitif tam sayı"),
    (("severity", "default"), "urgent", "severity.default"),
])
def test_invalid_policy_is_rejected(path, value, message):
    doc = copy.deepcopy(DOC)
    doc[path[0]][path[1]] = value
    with pytest.raises(ChangeControlError, match=message):
        ChangePolicy.from_document(doc)


def test_metadata_validation_checks_roles_fields_and_product_names(metadata):
    def errors(**changes):
        doc = {**copy.deepcopy(DOC), **changes}
        return validate_change_control(doc, roles={r["name"] for r in metadata.source_roles},
                                       hierarchy=metadata.hierarchy, fields_of=metadata.fields_of,
                                       products=["BMC", "vCenter", "ServiceNow"])
    assert errors() == []
    assert errors(observer_roles=["Discovery", "BMC"]) == [
        "change_control: tanımsız rol BMC", "change_control: ürün adı içeremez (BMC)"]
    assert errors(watched={"cmdb_ci_computer": ["ram", "uydurma"]}) == [
        "change_control.watched.cmdb_ci_computer: sınıfta olmayan alan uydurma"]
    assert errors(watched={"cmdb_ci_yok": ["ram"]}) == ["change_control.watched: tanımsız sınıf cmdb_ci_yok"]
    assert errors(severity={"default": "medium", "rules": [{"field": "yok", "equals": 1, "severity": "high"}]}) == [
        "change_control.severity: cmdb_ci'da olmayan alan yok"]
    assert validate(metadata).ok  # depodaki dosya geçerli


# ---------------------------------------------------------------------------------------------- değerlendirme
def test_approved_change_with_overlapping_planned_window_authorizes():
    v = evaluate(NOW - 6 * H, NOW, [chg(start=NOW - 2 * H, end=NOW - H)], POLICY, NOW)
    assert v.authorized and v.change.number == "CHG1" and v.window == "planlı"
    assert "CHG1 (planlı pencere)" in v.explanation(NOW - 6 * H, NOW)


def test_tolerance_extends_window_at_both_ends():
    obs_from, obs_at = NOW - 3 * H, NOW
    just_inside = chg(start=NOW + 59 * timedelta(minutes=1), end=NOW + 2 * H)
    just_outside = chg(start=NOW + 61 * timedelta(minutes=1), end=NOW + 2 * H)
    assert evaluate(obs_from, obs_at, [just_inside], POLICY, NOW).authorized
    assert not evaluate(obs_from, obs_at, [just_outside], POLICY, NOW).authorized
    before = chg(start=NOW - 9 * H, end=obs_from - 59 * timedelta(minutes=1))
    assert evaluate(obs_from, obs_at, [before], POLICY, NOW).authorized


@pytest.mark.parametrize(("record", "fragment"), [
    (chg(state="authorize", approval="requested"), "durum: yetkilendirme, onay: bekliyor"),
    (chg(state="canceled"), "durum: iptal"),
    (chg(state="closed", approval="rejected"), "onay: reddedildi"),
    (chg(state="other"), "durum: tanınmayan"),
    (chg(type="emergency", state="implement", approval="not_requested"), "tür: acil"),
])
def test_unauthorized_records_do_not_cover(record, fragment):
    rec = ChangeRecord(**{**record.__dict__, "start_date": NOW - 5 * H, "end_date": NOW + H})
    v = evaluate(NOW - H, NOW, [rec], POLICY, NOW)
    assert not v.authorized and fragment in v.considered[0]["reason"]
    assert "kapsayan onaylı değişiklik kaydı yok" in v.explanation(NOW - H, NOW)


def test_standard_change_is_preapproved_without_approval_field():
    rec = chg(type="standard", state="closed", approval="not_requested", start=NOW - 2 * H, end=NOW - H)
    assert evaluate(NOW - 3 * H, NOW, [rec], POLICY, NOW).authorized
    assert not evaluate(NOW - 3 * H, NOW, [chg(type="standard", state="new", approval=None, start=NOW - 2 * H,
                                              end=NOW - H)], POLICY, NOW).authorized  # taslak standart değişiklik


def test_actual_window_of_change_in_implementation_extends_to_now():
    rec = chg(state="implement", work_start=NOW - 30 * timedelta(minutes=1))
    v = evaluate(NOW - 10 * timedelta(minutes=1), NOW, [rec], POLICY, NOW)
    assert v.authorized and v.window == "gerçekleşen"
    closed_without_end = chg(state="closed", work_start=NOW - 9 * H)  # bitişi bilinmeyen kapalı kayıt: pencere yok
    v = evaluate(NOW - H, NOW, [closed_without_end], POLICY, NOW)
    assert not v.authorized and v.considered[0]["reason"] == "planlı veya gerçekleşen pencere yok"


def test_no_candidates_and_ordering_are_explained():
    v = evaluate(None, NOW, [], POLICY, NOW)
    assert not v.authorized and "CI'a bağlı değişiklik kaydı yok" in v.explanation(None, NOW)
    far = chg("CHG2", start=NOW - 90 * H, end=NOW - 80 * H)
    pending = chg("CHG1", state="assess", approval="requested", start=NOW - H, end=NOW)
    v = evaluate(NOW - H, NOW, [far, pending], POLICY, NOW)
    assert [c["number"] for c in v.considered] == ["CHG1", "CHG2"]
    assert "örtüşmüyor" in v.considered[1]["reason"]
    # birden çok kapsayan kayıtta sonuç belirlenimci (numara sırası)
    both = [chg("CHG9", start=NOW - H, end=NOW), chg("CHG3", start=NOW - H, end=NOW)]
    assert evaluate(NOW - H, NOW, both, POLICY, NOW).change.number == "CHG3"


# ---------------------------------------------------------------------------------------------- dönüşüm
def test_change_record_coercion():
    out = coerce_reference("change_request", "a" * 32, {
        "number": "CHG0030001", "state": "implement", "approval": "approved", "type": "normal",
        "start_date": "2026-11-26 09:00:00", "end_date": "2026-11-26T12:00:00+03:00"})
    assert out["start_date"] == datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    assert out["end_date"] == datetime(2026, 11, 26, 9, 0, tzinfo=UTC)  # saat dilimi UTC'ye çevrilir
    with pytest.raises(ReferenceDataError, match="state: sözlükte olmayan değer"):
        coerce_reference("change_request", "a" * 32, {"number": "CHG1", "state": "-1"})
    with pytest.raises(ReferenceDataError, match="start_date: tarih saat dilimi"):
        coerce_reference("change_request", "a" * 32, {"number": "CHG1", "start_date": "2026-11-26T09:00:00"})
    with pytest.raises(ReferenceDataError, match="number boş olamaz"):
        coerce_reference("change_request", "a" * 32, {"state": "closed"})
    with pytest.raises(ReferenceDataError, match="ci_item boş olamaz"):
        coerce_reference("task_ci", "b" * 32, {"task": "a" * 32})


# ---------------------------------------------------------------------------------------------- bağlayıcı
def connector(clock_at: datetime):
    sim = ServiceNowSimulator.from_dir(SAMPLES)
    src = SOURCES["servicenow_change"].build(
        HttpClient("https://sn.test", transport=sim, auth_header=HttpClient.basic("u", secrets.token_hex(4)),
                   retries=0), {"lookback_days": 30})
    src.clock = lambda: clock_at
    return src, sim


def test_change_connector_maps_states_and_skips_non_change_task_ci():
    src, sim = connector(NOW)
    gw = DryRunGateway()
    out: list = []
    gw.send = lambda run_id, records: out.extend(records) or len(records)  # type: ignore[method-assign]
    report = ConnectorRunner(src, load_mappings(REPO / "connectors/servicenow_change", "ServiceNowChange"), gw,
                             clock=lambda: NOW).run()
    assert report.completed and report.by_class == {"change_request": 4, "task_ci": 2}
    by_key = {r.source_key: r for r in out}
    assert by_key["c1a0000000000000000000000000c001"].attributes["state"] == "implement"
    assert by_key["c1a0000000000000000000000000c003"].attributes["approval"] == "not_requested"
    assert by_key["c1a0000000000000000000000000c004"].attributes["state"] == "canceled"
    assert "c1b0000000000000000000000000d099" not in by_key  # olay kaydının etkilenen CI'ı alınmaz
    queries = [c for c in sim.calls if "sysparm_query" in c]
    assert any("task.sys_class_name%3Dchange_request" in q for q in queries)
    assert all("sysparm_fields=sys_id%2Cnumber" in q for q in queries if "/change_request?" in q)


def test_lookback_window_and_delta_queries():
    src, _ = connector(datetime(2027, 6, 1, tzinfo=UTC))
    keys = {r["sys_id"] for r in src.fetch("snapshot")}
    # 2027 ortasında 30 günlük pencere: açık veya bitişi ileride olan kayıtlar; açık değişikliklerin etkilenen CI'ları
    # (satır uzun süredir güncellenmemiş olsa da). Kapanmış ve eski CHG0030003 alınmaz.
    assert keys == {"c1a0000000000000000000000000c001", "c1a0000000000000000000000000c002",
                    "c1a0000000000000000000000000c004", "c1b0000000000000000000000000d001",
                    "c1b0000000000000000000000000d002"}
    src, sim = connector(NOW)
    delta = list(src.fetch("delta", since=datetime(2026, 11, 25, 7, 30, tzinfo=UTC)))
    assert {r["number"] for r in delta if r["_table"] == "change_request"} == {"CHG0030004"}
    # delta'da task_ci kendisi veya bağlı değişikliği güncellenenleri alır (CHG0030004'ün etkilenen CI'ı yok)
    assert [r for r in delta if r["_table"] == "task_ci"] == []
    assert any("sys_updated_on%3E%3D2026-11-25+07%3A30%3A00" in c for c in sim.calls)
    with pytest.raises(Exception, match="Geriye bakış"):
        SOURCES["servicenow_change"].build(HttpClient("https://sn.test", transport=sim), {"lookback_days": 0})


def test_sample_file_is_marked_synthetic():
    doc = json.loads((SAMPLES / "tables.json").read_text(encoding="utf-8"))
    assert doc["_aciklama"].startswith("SENTETİK")
