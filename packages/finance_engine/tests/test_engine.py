import pytest

from finance_engine import (
    expected_minutes_per_new_patient,
    expected_revenue_per_new_patient_cents,
    format_brl,
    goal_gap_cents,
    parse_brl_to_cents,
    per_hour_cents,
)


def test_gap_example_from_prd():
    assert goal_gap_cents(5_000_000, 3_800_000) == 1_200_000


def test_gap_is_zero_when_goal_met():
    assert goal_gap_cents(1_000_000, 1_500_000) == 0


def test_mixed_scenario_leaves_2000_reais_missing():
    gain = 2 * 200_000 + 10 * 30_000 + 3 * 100_000
    assert gain == 1_000_000
    assert goal_gap_cents(5_000_000, 3_800_000) - gain == 200_000


def test_expected_minutes_prd_example():
    # 40 min, retorno 20 min, 76% -> 55,2 min
    assert expected_minutes_per_new_patient(40, 20, 7600) == 552


def test_free_return_adds_no_revenue():
    assert expected_revenue_per_new_patient_cents(30_000, 0, 7600) == 30_000


def test_invalid_rate_rejected():
    with pytest.raises(ValueError):
        expected_minutes_per_new_patient(40, 20, 10_001)


def test_money_parsing_and_format():
    assert parse_brl_to_cents("R$ 1.234,56") == 123_456
    assert parse_brl_to_cents("0.10") == 10
    assert format_brl(123_456) == "R$ 1.234,56"
    assert format_brl(-5_000) == "-R$ 50,00"
    with pytest.raises(TypeError):
        parse_brl_to_cents(1.1)


def test_no_float_drift():
    assert sum(parse_brl_to_cents("0.10") for _ in range(10)) == 100


def test_per_hour():
    assert per_hour_cents(200_000, 720) == 16_666
    assert per_hour_cents(100, 0) is None
