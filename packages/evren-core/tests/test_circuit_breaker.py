from evren_core.circuit_breaker import BreakerState, evaluate_run

BASELINE = [40_000, 40_120, 39_950, 40_030, 40_080]


def test_normal_run_allows_destructive_operations():
    v = evaluate_run(40_010, BASELINE, changed_count=600)
    assert v.state is BreakerState.OK and v.allows_destructive


def test_partial_vcenter_run_opens_breaker():
    v = evaluate_run(26_000, BASELINE, changed_count=200)
    assert v.state is BreakerState.OPEN
    assert not v.allows_destructive
    assert any("düştü" in r for r in v.reasons)


def test_moderate_drop_warns():
    v = evaluate_run(37_500, BASELINE)
    assert v.state is BreakerState.WARN


def test_mass_change_suggests_mapping_bug():
    v = evaluate_run(40_000, BASELINE, changed_count=20_000)
    assert v.state is BreakerState.OPEN


def test_incomplete_or_empty_or_erroneous_runs_open():
    assert evaluate_run(40_000, BASELINE, completed=False).state is BreakerState.OPEN
    assert evaluate_run(0, BASELINE).state is BreakerState.OPEN
    assert evaluate_run(40_000, BASELINE, error_count=2_000).state is BreakerState.OPEN


def test_without_history_staleness_is_held_back():
    v = evaluate_run(40_000, [39_000])
    assert v.state is BreakerState.WARN and not v.allows_destructive


def test_single_outlier_does_not_poison_baseline():
    v = evaluate_run(40_000, [40_000, 40_050, 5_000, 39_980, 40_020])
    assert v.state is BreakerState.OK
