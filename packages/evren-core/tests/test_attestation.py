"""Sahiplik onayı (F1-17, ADR-0027): politika dosyası, doğrulama, durum türetme ve kapsam SQL'i. Veriler sentetiktir."""
from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from evren_core.attestation import ATTESTED, REJECTED, AttestationError, AttestationPolicy, validate_attestation
from evren_core.metadata import validate

REPO = Path(__file__).resolve().parents[3]
DOC = yaml.safe_load((REPO / "metadata" / "attestation.yaml").read_text(encoding="utf-8"))
POLICY = AttestationPolicy.from_document(DOC)
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
OWNER, OTHER = "a" * 32, "b" * 32
D = timedelta(days=1)


def attrs(**kw):
    return {"owned_by": OWNER, "used_for": "Production", **kw}


def test_repository_policy_loads_and_validates(metadata):
    assert metadata.attestation_policy.enabled and validate(metadata).ok
    assert [p.name for p in POLICY.policies] == ["uretim", "genel"]
    assert POLICY.owner_fields == ("owned_by", "managed_by") and POLICY.reminder == 30 * D


def test_first_matching_policy_wins_and_subclasses_inherit(hierarchy):
    assert POLICY.policy_for("cmdb_ci_linux_server", attrs(), hierarchy).name == "uretim"
    assert POLICY.policy_for("cmdb_ci_linux_server", attrs(used_for="Test"), hierarchy).name == "genel"
    assert POLICY.policy_for("cmdb_ci_ip_switch", {}, hierarchy).period == 365 * D  # boş kullanım amacı → genel
    assert POLICY.policy_for("cmdb_ci_vmware_instance", attrs(), hierarchy) is None  # vCenter nesnesi kapsam dışı
    assert POLICY.policy_for("yok_sinif", attrs(), hierarchy) is None


@pytest.mark.parametrize(("extra", "state"), [
    ({}, "not_reviewed"),
    ({"attestation_status": "Not Yet Reviewed", "attested_date": NOW - 10 * D, "attested_by": OWNER}, "not_reviewed"),
    ({"attestation_status": ATTESTED, "attested_date": NOW - 10 * D, "attested_by": OWNER}, "valid"),
    ({"attestation_status": ATTESTED, "attested_date": NOW - 160 * D, "attested_by": OWNER}, "due_soon"),
    ({"attestation_status": ATTESTED, "attested_date": NOW - 180 * D, "attested_by": OWNER}, "expired"),
    ({"attestation_status": ATTESTED, "attested_date": NOW - 10 * D, "attested_by": OTHER}, "expired"),  # sahip değişti
    ({"attestation_status": ATTESTED, "attested_by": OWNER}, "not_reviewed"),  # tarihsiz onay geçerli sayılmaz
    ({"attestation_status": REJECTED, "attested_date": NOW - 1 * D, "attested_by": OWNER}, "rejected"),
])
def test_state(hierarchy, extra, state):
    st = POLICY.state("cmdb_ci_linux_server", attrs(**extra), hierarchy, NOW)
    assert st["state"] == state and st["policy"]["name"] == "uretim" and st["owners"] == [OWNER]
    if state in ("valid", "due_soon"):
        assert st["valid_until"] == extra["attested_date"] + 180 * D


def test_managed_by_can_attest_and_orphan_is_flagged(hierarchy):
    st = POLICY.state("cmdb_ci_linux_server", {"managed_by": OTHER, "attestation_status": ATTESTED,
                                               "attested_by": OTHER, "attested_date": NOW - D}, hierarchy, NOW)
    assert st["state"] == "valid" and st["owners"] == [OTHER] and not st["orphan"]
    orphan = POLICY.state("cmdb_ci_linux_server", {}, hierarchy, NOW)
    assert orphan["orphan"] and orphan["state"] == "not_reviewed"
    out = POLICY.state("cmdb_ci_vmware_instance", {}, hierarchy, NOW)
    assert out["state"] == "out_of_scope" and not out["orphan"]


def test_period_sql_is_parametrised_and_covers_subclasses(hierarchy):
    text, params = POLICY.period_sql(hierarchy)
    assert text.count("%s") == len(params) and "Production" not in text
    assert "cmdb_ci_linux_server" in params[0] and params[1] == ["Production"] and params[2] == 180
    assert params[-1] == 365 and POLICY.owners_sql() == "ARRAY[ci.owned_by::text, ci.managed_by::text]::text[]"
    with pytest.raises(AttestationError):
        POLICY.period_sql(hierarchy, alias="ci; DROP")


def _errors(metadata, doc):
    def choices(element):
        ch = metadata.choice_list("cmdb_ci", element)
        return [v["value"] for v in ch["values"]] if ch else None
    return validate_attestation(doc, hierarchy=metadata.hierarchy, fields_of=metadata.fields_of,
                                choice_values=choices, products=["BMC", "vCenter"])


@pytest.mark.parametrize(("mutate", "needle"), [
    (lambda d: d["policies"][0].update(applies_to=["yok_sinif"]), "tanımsız sınıf yok_sinif"),
    (lambda d: d["policies"][0].update(where={"used_for": ["Üretim değil"]}), "tanımsız değer"),
    (lambda d: d["policies"][0].update(where={"os": ["Linux"]}), "sütun olarak saklanan"),
    (lambda d: d["policies"][0].update(where={"used_for; DROP": ["X"]}), "alan adı geçersiz"),
    (lambda d: d["policies"][0].update(period_days=7), "30–730"),
    (lambda d: d["policies"][1].update(name="uretim"), "benzersiz"),
    (lambda d: d.update(owner_fields=["support_group"]), "sys_user başvurusu"),
    (lambda d: d["policies"][0].update(label_tr="BMC sunucuları"), "ürün adı"),
    (lambda d: d.update(extra=1), "bilinmeyen anahtar"),
    (lambda d: d.update(policies=[]), "boş olmayan liste"),
])
def test_validation_rejects(metadata, mutate, needle):
    doc = copy.deepcopy(DOC)
    mutate(doc)
    errors = _errors(metadata, doc)
    assert any(needle in e for e in errors), errors


def test_disabled_without_document(hierarchy):
    off = AttestationPolicy.from_document(None)
    assert not off.enabled and off.state("cmdb_ci_server", {}, hierarchy, NOW)["state"] == "out_of_scope"
    assert off.period_sql(hierarchy) == ("NULL::integer", [])
    with pytest.raises(AttestationError):
        AttestationPolicy.from_document({"version": 1, "policies": [{"name": "x"}]})
