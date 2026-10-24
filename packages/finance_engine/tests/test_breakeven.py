import pytest

from finance_engine.breakeven import (
    ServiceInput as S,
)
from finance_engine.breakeven import (
    breakeven,
    fixed_per_hour_cents,
    per_service,
)


def test_fixed_per_hour():
    assert fixed_per_hour_cents(1_000_000, 6_000) == 10_000  # R$ 10.000 em 100 h = R$ 100/h
    assert fixed_per_hour_cents(100, 7) == 857  # arredonda ao centavo mais próximo
    assert fixed_per_hour_cents(500_000, 0) is None
    assert fixed_per_hour_cents(0, 600) == 0
    with pytest.raises(ValueError):
        fixed_per_hour_cents(-1, 60)


def test_per_service_allocates_by_time():
    # consulta de 60 min, margem R$ 200; custo fixo R$ 10.000 sobre 100 h -> R$ 100/h
    c = per_service(1_000_000, 6_000, S("a", 10, 20_000, 600))
    assert c.fixed_per_unit_cents == 10_000 and c.net_per_unit_cents == 10_000
    assert c.margin_per_hour_cents == 20_000 and c.net_per_hour_cents == 10_000
    assert c.breakeven_alone_units == 50  # 1.000.000 / 20.000


def test_allocation_sums_back_to_the_fixed_cost():
    svcs = [S("a", 40, 25_000, 600), S("b", 20, 15_000, 300), S("c", 10, 9_000, 450)]
    minutes = sum(s.units * s.minutes_x10 for s in svcs) // 10
    total = sum(per_service(1_200_000, minutes, s).fixed_per_unit_cents * s.units for s in svcs)
    assert abs(total - 1_200_000) <= len(svcs) * max(
        s.units for s in svcs
    )  # só erro de arredondamento


def test_negative_margin_and_zero_time():
    c = per_service(100_000, 6_000, S("x", 5, -500, 600))
    assert c.breakeven_alone_units is None and c.net_per_unit_cents < 0
    z = per_service(100_000, 6_000, S("y", 5, 1000, 0))
    assert z.net_per_hour_cents is None and z.fixed_per_unit_cents is None
    assert per_service(100_000, 0, S("w", 5, 1000, 600)).net_per_unit_cents is None


def test_breakeven_single_service():
    r = breakeven(1_000_000, [S("a", 80, 20_000, 600)])
    assert r.total_units == 50 and dict(r.units) == {"a": 50} and r.minutes == 3_000
    assert r.status == "ABOVE" and r.contribution_cents == 1_600_000 and r.result_cents == 600_000
    assert r.safety_bps == 3_750  # (80-50)/80


def test_breakeven_mix_keeps_proportions_and_covers_the_fixed_cost():
    svcs = [S("a", 60, 20_000, 600), S("b", 20, 10_000, 300)]
    r = breakeven(1_200_000, svcs)
    assert r.avg_margin_cents == 17_500
    by = dict(r.units)
    assert by["a"] > by["b"]
    covered = sum(by[s.key] * s.margin_cents for s in svcs)
    assert covered >= 1_200_000  # o equilíbrio de fato cobre o fixo
    assert covered - 1_200_000 < max(s.margin_cents for s in svcs) * len(
        svcs
    )  # sem folga exagerada
    assert r.total_units == sum(by.values())


def test_below_break_even():
    r = breakeven(2_000_000, [S("a", 40, 20_000, 600)])
    assert r.status == "BELOW" and r.result_cents == -1_200_000 and r.total_units == 100
    assert r.safety_bps < 0


def test_edge_cases():
    assert breakeven(500_000, [S("a", 0, 20_000, 600)]).status == "NO_PLAN"
    assert breakeven(500_000, []).status == "NO_PLAN"
    r = breakeven(500_000, [S("a", 10, -1_000, 600)])
    assert r.status == "UNREACHABLE" and r.units == ()
    z = breakeven(0, [S("a", 10, 20_000, 600)])
    assert z.status == "ABOVE" and z.total_units == 0 and z.safety_bps == 10_000
    with pytest.raises(ValueError):
        breakeven(-1, [S("a", 1, 1, 1)])
    with pytest.raises(ValueError):
        breakeven(1, [S("a", -1, 1, 1)])
