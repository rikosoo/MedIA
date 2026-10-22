from datetime import date

import pytest

from finance_engine.forecast import (
    attainment_ratio,
    expand_leave_days,
    inflate,
    reserve_needed,
    scenario_attainment,
    seasonal_indices,
    weekdays_in_month,
    working_days_factor,
)
from finance_engine.funnel import (
    RECOMMENDATIONS,
    bottleneck,
    combine,
    conversion_drop,
    funnel,
    required_leads,
)

# ---------- funil ----------


def test_funnel_rates_and_costs():
    f = funnel(leads=100, responded=80, bookings=40, attended=30, spend_cents=60_000)
    assert (f.response_rate_bps, f.lead_to_booking_bps, f.booking_to_attendance_bps) == (
        8000,
        4000,
        7500,
    )
    assert f.lead_to_attendance_bps == 3000
    assert f.cost_per_booking_cents == 1_500 and f.cost_per_attended_cents == 2_000


def test_funnel_without_denominators_has_no_rates():
    f = funnel(0, 0, 0, 0, 5_000)
    assert (
        f.response_rate_bps is None
        and f.cost_per_booking_cents is None
        and f.cost_per_attended_cents is None
    )
    assert funnel(10, 5, 0, 0, 100).cost_per_booking_cents is None


@pytest.mark.parametrize(
    "args",
    [(10, 11, 5, 5, 0), (10, 5, 11, 5, 0), (10, 5, 5, 6, 0), (-1, 0, 0, 0, 0), (1, 0, 0, 0, -5)],
)
def test_funnel_rejects_impossible_counts(args):
    with pytest.raises(ValueError):
        funnel(*args)


def test_combine_recomputes_rates_over_totals_not_average_of_rates():
    a, b = funnel(100, 100, 50, 50, 0), funnel(10, 10, 1, 1, 0)
    c = combine([a, b])
    assert c.lead_to_booking_bps == 51 * 10_000 // 110  # 4636, não (5000+1000)/2
    assert c.leads == 110


def test_bottleneck_is_the_lowest_rate_and_needs_all_three():
    f = funnel(100, 90, 20, 18, 0)  # resposta 90%, agendamento 20%, comparecimento 90%
    assert bottleneck(f) == "BOOKING"
    assert bottleneck(funnel(100, 40, 60, 54, 0)) == "RESPONSE_TIME"
    assert bottleneck(funnel(100, 90, 60, 20, 0)) == "ATTENDANCE"
    assert bottleneck(funnel(0, 0, 0, 0, 0)) is None
    assert set(RECOMMENDATIONS) == {"RESPONSE_TIME", "BOOKING", "ATTENDANCE"}
    text = " ".join(" ".join(v) for v in RECOMMENDATIONS.values()).lower()
    assert "lista" not in text and "compra" not in text and "garant" not in text  # nada antiético


def test_required_leads_uses_observed_rates_and_flags_thin_data():
    f = funnel(100, 80, 40, 30, 0)  # 40% agendam, 75% comparecem
    n = required_leads(15, f)
    assert (n.bookings, n.leads, n.enough_data) == (20, 50, True)
    assert required_leads(15, funnel(10, 8, 4, 3, 0)).enough_data is False
    assert required_leads(5, funnel(0, 0, 0, 0, 0)).leads is None
    assert required_leads(0, f).leads == 0


def test_conversion_drop_needs_volume_and_a_real_drop():
    hist = [funnel(100, 80, 40, 30, 0), funnel(100, 80, 40, 30, 0)]  # 40%
    assert conversion_drop(funnel(100, 80, 25, 20, 0), hist) == 3750  # 25% vs 40%: -37,5%
    assert conversion_drop(funnel(100, 80, 35, 30, 0), hist) is None  # queda pequena
    assert conversion_drop(funnel(10, 8, 1, 1, 0), hist) is None  # poucos contatos
    assert conversion_drop(funnel(100, 80, 25, 20, 0), []) is None


# ---------- previsão ----------


def test_attainment_ratio_ignores_months_without_plan():
    assert attainment_ratio(1_000_000, 850_000) == 8500
    assert attainment_ratio(0, 500) is None
    assert attainment_ratio(100, 250) == 25_000  # pode passar de 100%


def test_attainment_scenarios_from_history_or_manual():
    h = scenario_attainment([8000, 9000, 10_000])
    assert (h.conservative_bps, h.base_bps, h.optimistic_bps, h.source) == (
        8000,
        9000,
        10_000,
        "HISTORY",
    )
    cap = scenario_attainment([5_000, 10_000, 30_000])
    assert cap.optimistic_bps == 12_000  # teto
    m = scenario_attainment(
        [9_000], manual=(7_000, 9_000, 10_000)
    )  # poucos meses: manual declarado
    assert (m.source, m.months_used, m.conservative_bps) == ("MANUAL", 1, 7_000)
    assert scenario_attainment([]).base_bps == 10_000
    with pytest.raises(ValueError):
        scenario_attainment([], manual=(9_000, 8_000, 10_000))


def test_working_days_factor_for_holidays_and_vacation():
    assert len(weekdays_in_month(2026, 10)) == 22
    f, total, lost = working_days_factor(2026, 10, {date(2026, 10, 12)}, set())
    assert (total, lost, f) == (22, 1, 21 * 10_000 // 22)
    vac = expand_leave_days(
        date(2026, 10, 19), date(2026, 10, 23), date(2026, 10, 1), date(2026, 10, 31)
    )
    f2, _, lost2 = working_days_factor(2026, 10, {date(2026, 10, 12)}, vac)
    assert lost2 == 6
    f3, _, lost3 = working_days_factor(
        2026, 10, {date(2026, 10, 10)}, set()
    )  # sábado: não é dia útil
    assert (f3, lost3) == (10_000, 0)
    assert expand_leave_days(
        date(2026, 9, 25), date(2026, 10, 3), date(2026, 10, 1), date(2026, 10, 31)
    ) == {date(2026, 10, d) for d in (1, 2, 3)}


def test_seasonality_needs_a_full_year_and_never_invents():
    assert seasonal_indices({(2026, m): 100 for m in range(1, 10)}) is None
    flat = seasonal_indices({(2025, m): 100_000 for m in range(1, 13)})
    assert flat and set(flat.values()) == {10_000}
    data = {(2025, m): 100_000 for m in range(1, 13)}
    data[(2025, 12)] = 200_000
    idx = seasonal_indices(data)
    assert idx[12] > 10_000 > idx[6] and idx[12] == 200_000 * 10_000 // (1_300_000 // 12)
    two_years = {(2024, m): 100_000 for m in range(1, 13)} | {
        (2025, m): 100_000 for m in range(1, 13)
    }
    assert seasonal_indices(two_years) == flat


def test_inflation_is_compound_integer_math():
    assert inflate(100_000, 0, 5) == 100_000
    assert inflate(100_000, 100, 2) == 102_010  # 1% ao mês, 2 meses
    assert inflate(100_000, 100, 0) == 100_000
    assert inflate(100_001, 50, 1) == 100_502  # arredonda para cima
    with pytest.raises(ValueError):
        inflate(1, 0, -1)


def test_reserve_needed_sums_shortfalls_below_the_floor():
    assert reserve_needed([500_000, 300_000, 450_000], 400_000) == (100_000, 1)
    r, n = reserve_needed([500_000, 300_000, 350_000], 400_000)
    assert (r, n) == (100_000 + 50_000, 2)
    assert reserve_needed([500_000], 400_000) == (0, 0)
    assert reserve_needed([-100_000], 400_000) == (500_000, 1)  # prejuízo também conta
