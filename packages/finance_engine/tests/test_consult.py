from datetime import date

import pytest

from finance_engine import (
    capacity_new_patients,
    consult_economics,
    expected_minutes_per_new_patient,
    occurrences_in_month,
    resolve_return_minutes,
    resolve_return_price_cents,
    return_month,
    returns_by_month,
)


def econ(**over):
    base = dict(
        price_cents=30_000,
        consult_minutes=40,
        return_rate_bps=7600,
        return_minutes=20,
        return_price_cents=0,
    )
    return consult_economics(**{**base, **over})


def test_prd_example_55_2_minutes_matches_legacy_formula():
    e = econ()
    assert e.minutes_x10 == 552
    assert e.minutes_x10 == expected_minutes_per_new_patient(40, 20, 7600)


def test_free_return_costs_time_but_adds_no_revenue():
    e = econ()
    assert e.revenue_returns_cents == 0
    assert e.gross_revenue_cents == 30_000
    assert e.expected_returns_bps == 7600


def test_paid_return_revenue():
    e = econ(return_price_cents=15_000)
    assert e.revenue_returns_cents == 11_400  # 76% x 150,00
    assert e.gross_revenue_cents == 41_400


def test_no_show_new_reduces_revenue_and_returns_but_not_slot_time():
    e = econ(new_no_show_bps=1000, return_price_cents=15_000)  # 10% faltam
    assert e.revenue_new_cents == 27_000
    assert e.expected_returns_bps == 6840  # só quem compareceu retorna
    assert e.minutes_x10 == 537  # 400 + 136,8 arredondado para 137


def test_return_no_show_costs_slot_but_no_money():
    e = econ(return_price_cents=15_000, return_no_show_bps=5000)
    assert e.revenue_returns_cents == 5_700
    assert e.minutes_x10 == 552


def test_fee_and_cost_and_margin():
    e = econ(fee_bps=300, cost_cents=2_000)
    assert e.fee_cents == 900
    assert e.variable_cost_cents == 2_000
    assert e.margin_cents == 30_000 - 900 - 2_000
    assert e.margin_per_hour_cents == e.margin_cents * 600 // 552


@pytest.mark.parametrize(
    "kw",
    [
        dict(consult_minutes=0),
        dict(return_rate_bps=10_001),
        dict(new_no_show_bps=-1),
        dict(price_cents=-1),
        dict(fee_bps=20_000),
    ],
)
def test_invalid_inputs_rejected(kw):
    with pytest.raises(ValueError):
        econ(**kw)


def test_zero_rate_zero_returns():
    e = econ(return_rate_bps=0)
    assert e.minutes_x10 == 400 and e.expected_returns_bps == 0


def test_capacity_floor():
    assert capacity_new_patients(60 * 100, 552) == 108  # 6000 min / 55,2
    with pytest.raises(ValueError):
        capacity_new_patients(100, 0)


def test_return_minutes_modes():
    assert resolve_return_minutes(40, "PERCENT", percent_bps=5000) == 20
    assert resolve_return_minutes(40, "MINUTES", minutes=15) == 15
    with pytest.raises(ValueError):
        resolve_return_minutes(0, "MINUTES", minutes=1)
    with pytest.raises(ValueError):
        resolve_return_minutes(40, "PERCENT")


def test_return_price_modes_and_free_window():
    assert resolve_return_price_cents(30_000, "FREE", 30) == 0
    assert resolve_return_price_cents(30_000, "FULL", 30) == 30_000
    assert resolve_return_price_cents(30_000, "PARTIAL", 30, partial_bps=5000) == 15_000
    assert resolve_return_price_cents(30_000, "CUSTOM", 30, custom_cents=9_900) == 9_900
    # dentro da janela gratuita vale 0 mesmo no modo FULL; fora dela cobra
    assert resolve_return_price_cents(30_000, "FULL", 30, free_within_days=30) == 0
    assert resolve_return_price_cents(30_000, "FULL", 31, free_within_days=30) == 30_000


def test_free_return_30_days_later_lands_next_month_without_revenue():
    """PRD 20.1.6: retorno ocupa o mês seguinte e não gera receita duplicada."""
    assert return_month(date(2026, 10, 20), 30) == date(2026, 11, 1)
    e = econ()
    by_month = returns_by_month([(date(2026, 10, 20), 10)], e.expected_returns_bps, 30)
    assert by_month == {date(2026, 11, 1): 10 * 7600}


def test_returns_split_across_months():
    by_month = returns_by_month([(date(2026, 10, 1), 10), (date(2026, 10, 25), 10)], 7600, 30)
    assert by_month == {date(2026, 10, 1): 76_000, date(2026, 11, 1): 76_000}


def test_recurrence_clamps_day_and_respects_bounds():
    start = date(2026, 1, 31)
    assert occurrences_in_month("MONTHLY", start, None, date(2026, 2, 1)) == [date(2026, 2, 28)]
    assert occurrences_in_month("MONTHLY", start, None, date(2025, 12, 1)) == []
    assert occurrences_in_month("MONTHLY", start, date(2026, 3, 31), date(2026, 4, 1)) == []
    assert occurrences_in_month("YEARLY", date(2024, 2, 29), None, date(2025, 2, 1)) == [
        date(2025, 2, 28)
    ]
    assert occurrences_in_month("YEARLY", date(2024, 2, 29), None, date(2025, 3, 1)) == []
    with pytest.raises(ValueError):
        occurrences_in_month("DAILY", start, None, start)
