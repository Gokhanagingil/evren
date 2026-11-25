"""CMDB sağlık puanlaması (F1-09): oranlar, boyut ve genel puan, derece; metadata kuralları."""
from __future__ import annotations

from pathlib import Path

from evren_core.health import Ratio, completeness_ratio, dimension_score, grade, overall_score
from evren_core.metadata import load_metadata, validate

REPO = Path(__file__).resolve().parents[3]


def test_ratio_and_bad_counts():
    assert Ratio(3, 4).pct == 75.0 and Ratio(0, 0).pct is None
    assert Ratio.from_bad(1, 4).pct == 75.0 and Ratio.from_bad(9, 4).pct == 0.0


def test_completeness_weights_recommended_half():
    # 10 CI; 2 zorunlu alan tamamen dolu, 2 önerilen alan boş → 2·2·10 / (10·(4+2)) = %66,7
    r = completeness_ratio(10, {"name": 10, "ip_address": 10}, {"fqdn": 0, "owned_by": 0})
    assert r.pct == 66.7
    assert completeness_ratio(0, {"name": 0}, {}).pct is None


def test_dimension_and_overall_scores_ignore_unmeasurable():
    assert dimension_score([Ratio(1, 2), Ratio(0, 0), Ratio(2, 2)]) == 75.0
    assert dimension_score([Ratio(0, 0)]) is None
    scores = {"completeness": 80.0, "correctness": 90.0, "compliance": None, "coverage": 60.0}
    # Uyum ölçülemedi: ağırlıklar 30/30/20 → (80·30 + 90·30 + 60·20) / 80 = 78,75 → 78,8
    assert overall_score(scores) == 78.8
    assert overall_score(dict.fromkeys(scores)) is None


def test_grade_thresholds():
    assert [grade(s) for s in (90, 85, 75, 69.9, None)] == ["good", "good", "warn", "bad", "unknown"]
    assert grade(80, {"good": 80, "warn": 50}) == "good"


def test_health_rules_are_valid_and_reference_dictionary_fields():
    md = load_metadata(REPO / "metadata")
    assert md.health["weights"] == {"completeness": 30, "correctness": 30, "compliance": 20, "coverage": 20}
    assert validate(md).ok
    md.health = {**md.health, "completeness": [{"applies_to": "cmdb_ci_server", "required": ["uydurma"]}]}
    assert any("alan tanımsız: uydurma" in e for e in validate(md).errors)
