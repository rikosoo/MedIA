"""Orquestra os objetivos, deduplica cenários, valida e explica a inviabilidade."""

from dataclasses import dataclass, field

from .model import (
    ACTIVE_LIMIT,
    BUDGET,
    CALENDAR,
    COMMITTED_LIMIT,
    CONSTRAINT_FAMILIES,
    CONSULT_CAPACITY,
    DEMAND,
    MAX_EVENTS,
    MAX_UNITS,
    NIGHT_LIMIT,
    OBJECTIVES,
    Problem,
    Solution,
)  # fmt: skip
from .solver import max_achievable, solve
from .validate import binding, check


@dataclass
class Scenario:
    objectives: list[str]  # vários objetivos podem convergir para o mesmo cenário
    solution: Solution
    binding: list[str]
    pessimistic_ok: bool = False  # fecha a meta mesmo com ganhos e demanda pessimistas


@dataclass
class Result:
    already_met: bool = False
    feasible: bool = False
    scenarios: list[Scenario] = field(default_factory=list)
    max_gain_cents: int | None = None  # só quando inviável
    blocking: list[dict] = field(default_factory=list)
    conservative_feasible: bool | None = None
    conservative_max_gain_cents: int | None = None
    optimal: bool = True


def _applicable(p: Problem) -> list[str]:
    """Só relaxa famílias que de fato existem no problema."""
    fams = []
    has_consult = any(a.kind == "CONSULT" for a in p.activities)
    if p.max_active_minutes is not None:
        fams.append(ACTIVE_LIMIT)
    if p.max_committed_minutes is not None:
        fams.append(COMMITTED_LIMIT)
    fams.append(MAX_UNITS)
    if any(a.kind == "CONSULT" and (a.demand_limit is not None) for a in p.activities):
        fams.append(DEMAND)
    if has_consult:
        fams.append(CONSULT_CAPACITY)
    if len(p.candidates) > 1:
        fams.append(CALENDAR)
    if p.marketing_budget_cents is not None:
        fams.append(BUDGET)
    if p.max_night_shifts is not None:
        fams.append(NIGHT_LIMIT)
    if p.max_block_events is not None:
        fams.append(MAX_EVENTS)
    return [f for f in CONSTRAINT_FAMILIES if f in fams]


def explain_infeasible(p: Problem) -> tuple[int, list[dict]]:
    """(maior ganho alcançável, restrições que o limitam).

    Relaxa uma família por vez; se nenhuma sozinha fecha a meta, testa pares (duas restrições
    podem proteger o mesmo limite, ex.: horas ativas e comprometidas). Ordem: as que fecham a
    meta primeiro, depois menos restrições, depois maior ganho.
    """
    base = max_achievable(p)
    fams = _applicable(p)
    seen: dict[frozenset, int] = {}

    def gain(group: frozenset) -> int:
        if group not in seen:
            seen[group] = max_achievable(p, group)
        return seen[group]

    found = [frozenset({f}) for f in fams if gain(frozenset({f})) > base]
    if not any(gain(g) >= p.gap_cents for g in found):
        for i, a in enumerate(fams):
            for b in fams[i + 1 :]:
                pair = frozenset({a, b})
                if gain(pair) > max(gain(frozenset({a})), gain(frozenset({b})), base):
                    found.append(pair)
    out = []
    for g in found:
        m = gain(g)
        out.append({"constraints": [f for f in CONSTRAINT_FAMILIES if f in g],
                    "max_gain_if_relaxed_cents": m, "increase_cents": m - base,
                    "closes_gap": m >= p.gap_cents})  # fmt: skip
    out.sort(
        key=lambda d: (
            not d["closes_gap"],
            len(d["constraints"]),
            -d["increase_cents"],
            d["constraints"],
        )
    )
    return base, out[:6]


def optimize(p: Problem, objectives: tuple[str, ...] = OBJECTIVES) -> Result:
    if p.gap_cents == 0:
        return Result(already_met=True, feasible=True)  # meta batida: sem trabalho extra
    res = Result()
    merged: dict[tuple, Scenario] = {}
    for obj in objectives:
        pess = obj == "CONSERVATIVE"
        sol = solve(p, obj)
        if sol is None:
            if pess:
                res.conservative_feasible = False
                res.conservative_max_gain_cents = max_achievable(p, pessimistic=True)
            continue
        if pess:
            res.conservative_feasible = True
        bad = check(p, sol, pessimistic=pess)
        if bad:  # nunca devolve um cenário que a validação independente rejeita
            raise RuntimeError(f"solução inválida para {obj}: {bad}")
        res.optimal = res.optimal and sol.optimal
        key = (tuple(sorted(sol.units.items())), sol.placements)
        if key in merged:
            merged[key].objectives.append(obj)
        else:
            merged[key] = Scenario([obj], sol, binding(p, sol), not check(p, sol, pessimistic=True))
    res.scenarios = list(merged.values())
    res.feasible = any(o != "CONSERVATIVE" for s in res.scenarios for o in s.objectives)
    if not res.feasible:
        res.max_gain_cents, res.blocking = explain_infeasible(p)
    return res
