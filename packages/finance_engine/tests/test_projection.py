import pytest

from finance_engine import (
    StreamInput,
    compute_stream,
    effective_target_cents,
    goal_gap_cents,
    goal_status,
    project,
)


def work(units=8, certainty="GUARANTEED", **kw):
    base = dict(
        key="w", kind="WORK", units=units, certainty=certainty, gross_unit_cents=200_000,
        variable_unit_cents=0, tax_rate_bps=1500, active_minutes_x10_unit=7200,
        committed_minutes_x10_unit=7200,
    )  # fmt: skip
    return compute_stream(StreamInput(**{**base, **kw}))


def service(units=50, certainty="PROBABLE", **kw):
    base = dict(
        key="s", kind="SERVICE", units=units, certainty=certainty, gross_unit_cents=30_000,
        variable_unit_cents=0, tax_rate_bps=1000, active_minutes_x10_unit=552,
        committed_minutes_x10_unit=552,
    )  # fmt: skip
    return compute_stream(StreamInput(**{**base, **kw}))


def test_stream_math_is_integer_and_taxed_on_total():
    w = work()
    assert (w.gross_cents, w.tax_cents, w.net_cents) == (1_600_000, 240_000, 1_360_000)
    assert w.active_minutes == 8 * 720
    assert w.net_per_committed_hour_cents == 1_360_000 * 60 // (8 * 720)


def test_tax_rounding_on_total_not_per_unit():
    # 3 x 0,10 com 33,33% sobre o total: 30 * 3333 / 10000 = 9,999 -> 10 centavos
    s = compute_stream(StreamInput("k", "WORK", 3, "PROBABLE", 10, 0, 3333, 0, 0))
    assert s.tax_cents == 10 and s.net_cents == 20
    assert s.net_per_committed_hour_cents is None  # sem tempo informado: sem divisão por zero


def test_service_minutes_come_from_expected_55_2():
    s = service()
    assert s.active_minutes == 2760  # 50 x 55,2 min
    assert (s.gross_cents, s.tax_cents, s.net_cents) == (1_500_000, 150_000, 1_350_000)


def test_personal_net_assured_vs_probable():
    p = project([work(), service()], 360_000, "PERSONAL_NET")
    assert p.value_cents == 1_360_000 + 1_350_000 - 360_000 == 2_350_000
    assert p.assured_cents == 1_360_000 - 360_000 == 1_000_000
    assert p.probable_cents == 1_350_000


def test_other_metrics():
    streams = [work(), service()]
    assert project(streams, 360_000, "GROSS_REVENUE").value_cents == 3_100_000
    assert project(streams, 360_000, "CLINIC_PROFIT").value_cents == 1_350_000 - 360_000


def test_deficit_keeps_its_sign():
    p = project([], 360_000, "PERSONAL_NET")
    assert p.value_cents == -360_000 and p.assured_cents == -360_000
    assert goal_gap_cents(3_000_000, p.value_cents) == 3_360_000


def test_prd_example_gap_12000():
    # meta 50.000, projeção 38.000 -> lacuna 12.000 (PRD 7.4)
    assert goal_gap_cents(5_000_000, 3_800_000) == 1_200_000


def test_reserves_only_when_enabled():
    assert effective_target_cents(3_000_000, 500_000, False) == 3_000_000
    assert effective_target_cents(3_000_000, 500_000, True) == 2_500_000
    assert effective_target_cents(300_000, 500_000, True) == 0


def test_status_requires_constraints():
    assert goal_status(100, 99, None) == "SHORT"
    assert goal_status(100, 100, True) == "MET"
    assert goal_status(100, 100, None) == "MET"
    assert goal_status(100, 500, False) == "MET_WITH_ISSUES"
    assert goal_status(100, 99, False) == "SHORT"


@pytest.mark.parametrize(
    "kw",
    [dict(units=-1), dict(certainty="MAYBE"), dict(tax_rate_bps=10_001), dict(gross_unit_cents=-1)],
)
def test_invalid_streams(kw):
    with pytest.raises(ValueError):
        work(**kw)


def test_invalid_projection_inputs():
    with pytest.raises(ValueError):
        project([], -1, "PERSONAL_NET")
    with pytest.raises(ValueError):
        project([], 0, "NOPE")
