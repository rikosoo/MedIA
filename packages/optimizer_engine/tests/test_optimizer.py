"""Casos do PRD §20.2 e propriedades do solver (determinismo, validação independente)."""

import dataclasses

import pytest
from optimizer_engine import (
    Activity,
    Candidate,
    Problem,
    Solution,
    check,
    explain_infeasible,
    max_achievable,
    optimize,
    solve,
)

DAY = 24 * 60


def block(key, gain, hours=12, max_units=3, pess=None, **kw):
    return Activity(key, "BLOCK", key, gain, pess if pess is not None else gain,
                    active_minutes=hours * 60, committed_minutes=hours * 60, max_units=max_units, **kw)  # fmt: skip


def consult(key="c", gain=30_000, x10=600, max_units=10, demand=None, pess=None, **kw):
    return Activity(key, "CONSULT", key, gain, pess if pess is not None else gain,
                    max_units=max_units, demand_limit=demand,
                    demand_limit_pessimistic=demand, consult_minutes_x10=x10, **kw)  # fmt: skip


def cand(act, day, hour=7, hours=12, recovery=0, night=False, lost=0):
    s = day * DAY + hour * 60
    return Candidate(f"{act}@{day}", act, s, s + hours * 60, s + hours * 60 + recovery,
                     f"d{day}", night, lost)  # fmt: skip


def cands(act, days, **kw):
    return [cand(act, d, **kw) for d in days]


def problem(gap, activities, candidates=(), **kw):
    return Problem(gap_cents=gap, activities=tuple(activities), candidates=tuple(candidates),
                   consult_free_minutes=kw.pop("consult_free_minutes", 10_000), **kw)  # fmt: skip


def mixed(shift_max):
    acts = [block("shift", 200_000, max_units=shift_max), consult(), block("oncall", 100_000)]
    cs = cands("shift", [1, 3, 5, 7, 9]) + cands("oncall", [2, 4, 6, 8])
    return problem(1_200_000, acts, cs)


def test_prd_example_does_not_reach_gap_and_reports_shortfall():
    """2 plantões + 10 consultas + 3 sobreavisos = R$ 10.000 < lacuna de R$ 12.000."""
    p = mixed(shift_max=2)
    res = optimize(p)
    assert res.feasible is False and res.scenarios == []
    assert res.max_gain_cents == 1_000_000  # nunca declara a meta cumprida
    assert p.gap_cents - res.max_gain_cents == 200_000  # faltam R$ 2.000
    assert res.blocking[0]["constraints"] == ["MAX_UNITS"] and res.blocking[0]["closes_gap"] is True


def test_prd_example_one_more_shift_closes_gap_with_82_committed_hours():
    res = optimize(mixed(shift_max=3))
    assert res.feasible
    s = res.scenarios[0].solution
    assert s.units == {"shift": 3, "c": 10, "oncall": 3}
    assert s.gain_cents == 1_200_000
    assert s.committed_minutes == 82 * 60  # 36h + 10h + 36h
    assert check(p := mixed(3), s) == [] and p.gap_cents == s.gain_cents


def test_zero_hours_never_returns_extra_work():
    p = dataclasses.replace(mixed(3), max_active_minutes=0, max_committed_minutes=0)
    res = optimize(p)
    assert res.feasible is False and res.scenarios == [] and res.max_gain_cents == 0
    # nenhuma sozinha resolve: os dois limites protegem o mesmo teto; o par aparece
    assert res.blocking[0]["constraints"] == ["ACTIVE_LIMIT", "COMMITTED_LIMIT"]
    assert res.blocking[0]["closes_gap"] is True


def test_demand_cap_is_a_hard_limit_not_a_guarantee():
    """10 consultas com demanda máxima de 3 não podem ser recomendadas."""
    p = problem(150_000, [consult(max_units=10, demand=3)])
    res = optimize(p)
    assert res.feasible is False
    assert res.max_gain_cents == 3 * 30_000
    assert res.blocking[0]["constraints"] == ["MAX_UNITS", "DEMAND"] or res.blocking[0][
        "constraints"
    ] == ["DEMAND"]
    assert res.blocking[0]["closes_gap"]
    ok = optimize(problem(90_000, [consult(max_units=10, demand=3)]))
    assert ok.scenarios[0].solution.units["c"] == 3


def test_unconfirmed_demand_is_flagged_as_risk():
    sol = optimize(problem(60_000, [consult(max_units=10, demand=None)])).scenarios[0].solution
    assert sol.units["c"] == 2 and sol.risk_cents == 60_000
    confirmed = optimize(problem(60_000, [consult(max_units=10, demand=10)])).scenarios[0].solution
    assert confirmed.risk_cents == 0


def test_on_call_and_shift_at_the_same_time_cannot_coexist():
    """Soma de horas cabe, mas as duas ocupam o mesmo período: inviável."""
    acts = [block("shift", 200_000, max_units=1), block("oncall", 100_000, max_units=1)]
    cs = [cand("shift", 1, 7, 12), cand("oncall", 1, 8, 12)]
    p = problem(300_000, acts, cs)
    res = optimize(p)
    assert res.feasible is False and res.max_gain_cents == 200_000
    assert res.blocking[0]["constraints"] == ["CALENDAR"] and res.blocking[0]["closes_gap"]
    # em dias diferentes, coexistem
    cs2 = [cand("shift", 1), cand("oncall", 2)]
    assert optimize(problem(300_000, acts, cs2)).feasible


def test_rest_window_blocks_the_next_day_early_start():
    shift_a = cand("s", 1, 7, 12, recovery=11 * 60)  # termina 19h, descanso até 06h do dia seguinte
    early = cand("s", 2, 5, 12)  # 05h do dia 2: dentro do descanso
    late = cand("s", 2, 8, 12)
    acts = [block("s", 200_000, max_units=2)]
    assert optimize(problem(400_000, acts, [shift_a, early])).feasible is False
    assert optimize(problem(400_000, acts, [shift_a, late])).feasible is True


def test_consult_capacity_shrinks_when_a_block_takes_consult_time():
    acts = [consult(gain=5_000, max_units=10), block("shift", 200_000, max_units=1)]
    free = 10 * 60
    # sem o plantão, só 10 consultas de 1h (R$ 500)
    assert max_achievable(problem(0, [acts[0]], consult_free_minutes=free)) == 50_000
    # o plantão elimina 8h de consulta: sobram 2h -> 1 plantão + 2 consultas = R$ 2.100
    both = problem(0, acts, [cand("shift", 1, lost=8 * 60)], consult_free_minutes=free)
    assert max_achievable(both) == 200_000 + 2 * 5_000  # sem a perda seria 250.000
    sol = solve(dataclasses.replace(both, gap_cents=205_000), "LESS_HOURS")
    assert (
        sol.units == {"c": 1, "shift": 1}
        and check(dataclasses.replace(both, gap_cents=205_000), sol) == []
    )


def test_budget_limits_acquisition():
    acts = [consult(max_units=10, acquisition_cost_cents=5_000)]
    p = problem(150_000, acts, marketing_budget_cents=20_000)  # 5 pacientes custariam R$ 250
    assert optimize(p).feasible is False
    assert max_achievable(p) == 4 * 30_000
    assert optimize(p).blocking[0]["constraints"] == ["BUDGET"]
    assert optimize(problem(150_000, acts, marketing_budget_cents=25_000)).feasible


def test_night_shift_and_event_limits():
    acts = [block("n", 250_000, max_units=3)]
    cs = cands("n", [1, 3, 5], night=True)
    assert max_achievable(problem(500_000, acts, cs, max_night_shifts=1)) == 250_000
    assert max_achievable(problem(500_000, acts, cs, max_block_events=2)) == 500_000
    third = optimize(problem(750_000, acts, cs, max_block_events=2))
    assert third.blocking[0]["constraints"] == ["MAX_EVENTS"] and third.blocking[0]["closes_gap"]


def test_objectives_diverge_and_duplicates_are_merged():
    # A: 1 plantão (12h, 1 dia). B: 40 consultas de 1h (40h, sem dia novo).
    acts = [block("shift", 300_000, max_units=1), consult(gain=7_500, max_units=40, demand=40)]
    p = problem(300_000, acts, cands("shift", [1]))
    less = solve(p, "LESS_HOURS")
    least = solve(p, "LEAST_CHANGE")
    assert less.units == {"shift": 1, "c": 0} and less.committed_minutes == 12 * 60
    assert least.units == {"shift": 0, "c": 40} and least.days_used == 0
    # quando todos concordam, o cenário aparece uma vez com vários objetivos
    easy = problem(30_000, [consult(max_units=5, demand=5)])
    res = optimize(easy)
    first = res.scenarios[0]
    assert {"LESS_HOURS", "LEAST_CHANGE", "CONSERVATIVE"} <= set(first.objectives)
    assert first.solution.units == {"c": 1}
    assert len(res.scenarios) < 5  # MAX_MARGIN usa a folga toda: é outro cenário


def test_max_margin_uses_all_profitable_headroom_but_meets_floor():
    p = problem(30_000, [consult(max_units=8, demand=8)], max_committed_minutes=8 * 60)
    s = solve(p, "MAX_MARGIN")
    assert s.units["c"] == 8 and s.gain_cents == 240_000


def test_conservative_scenario_uses_pessimistic_values():
    acts = [consult(gain=30_000, pess=24_000, max_units=20, demand=20)]
    p = problem(240_000, acts)
    base = solve(p, "LESS_HOURS")
    cons = solve(p, "CONSERVATIVE")
    assert base.units["c"] == 8 and cons.units["c"] == 10  # 10 x 24.000 = 240.000
    assert cons.gain_pessimistic_cents >= p.gap_cents
    assert check(p, cons, pessimistic=True) == []


def test_conservative_infeasible_while_base_feasible():
    acts = [consult(gain=30_000, pess=20_000, max_units=10, demand=10)]
    res = optimize(problem(250_000, acts))  # central: 10 x 30k = 300k; pessimista: 200k < 250k
    assert res.feasible and res.conservative_feasible is False
    assert res.conservative_max_gain_cents == 200_000
    assert all("CONSERVATIVE" not in s.objectives for s in res.scenarios)
    assert all(
        s.pessimistic_ok is False for s in res.scenarios
    )  # nenhum fecha a meta no pessimista


def test_balanced_is_valid_and_keeps_a_cushion_when_cheap():
    p = problem(100_000, [consult(max_units=10, demand=10)])
    s = solve(p, "BALANCED")
    assert check(p, s) == [] and s.gain_cents >= 100_000
    with pytest.raises(ValueError):
        dataclasses.replace(p, weights={"hours": 50, "change": 50, "risk": 50, "margin": 50})


def test_goal_already_met_recommends_no_extra_work():
    res = optimize(problem(0, [consult()]))
    assert res.already_met and res.scenarios == []


def test_deterministic_and_stable_tie_break():
    acts = [block("s", 200_000, max_units=1)]
    p = problem(200_000, acts, cands("s", [5, 2, 9]))  # empate: qualquer dia serve
    runs = [optimize(p) for _ in range(3)]
    assert all(r.scenarios[0].solution == runs[0].scenarios[0].solution for r in runs)
    assert runs[0].scenarios[0].solution.placements == ("s@2",)  # prefere a data mais cedo


def test_validator_is_independent_and_catches_tampering():
    p = mixed(3)
    good = solve(p, "LESS_HOURS")
    assert check(p, good) == []
    overlap = Solution({"shift": 2, "c": 0, "oncall": 0}, ("shift@1", "shift@1"), 400_000, 400_000, 1440, 1440, 1, 2, 0, 0)  # fmt: skip
    assert any("mais de uma vez" in v for v in check(p, overlap))
    p2 = problem(300_000, [block("a", 200_000, max_units=2), block("b", 100_000, max_units=1)],
                 [cand("a", 1, 7, 12), cand("b", 1, 8, 12)])  # fmt: skip
    clash = Solution({"a": 1, "b": 1}, ("a@1", "b@1"), 300_000, 300_000, 1440, 1440, 1, 2, 0, 0)
    assert any("CALENDAR" in v for v in check(p2, clash))
    wrong_gain = dataclasses.replace(good, gain_cents=good.gain_cents + 1)
    assert any("diverge" in v for v in check(p, wrong_gain))
    too_many = dataclasses.replace(good, units={**good.units, "c": 11})
    assert any("MAX_UNITS" in v or "DEMAND" in v for v in check(p, too_many))


def test_money_and_time_are_integers_not_floats():
    s = optimize(mixed(3)).scenarios[0].solution
    for v in (s.gain_cents, s.gain_pessimistic_cents, s.active_minutes, s.committed_minutes,
              s.days_used, s.block_events, s.consult_units, s.risk_cents, *s.units.values()):  # fmt: skip
        assert type(v) is int


def test_binding_constraints_reported():
    p = problem(60_000, [consult(max_units=2, demand=2)], max_committed_minutes=120)
    b = optimize(p).scenarios[0].binding
    assert "COMMITTED_LIMIT" in b and "MAX_UNITS:c" in b and "DEMAND:c" in b


def test_explain_infeasible_ranks_blockers_by_help():
    acts = [consult(max_units=2, demand=2), block("s", 200_000, max_units=1)]
    p = problem(900_000, acts, [cand("s", 1)], max_committed_minutes=24 * 60)
    base, blocking = explain_infeasible(p)
    assert base == 200_000 + 60_000
    assert blocking and all(b["increase_cents"] > 0 for b in blocking)
    # MAX_UNITS e DEMAND protegem o mesmo teto das consultas: só o par libera ganho
    assert sorted(blocking[0]["constraints"]) == ["DEMAND", "MAX_UNITS"]
    keys = [(not b["closes_gap"], len(b["constraints"]), -b["increase_cents"]) for b in blocking]
    assert keys == sorted(keys)
