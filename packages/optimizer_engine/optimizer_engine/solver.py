"""CP-SAT determinístico (1 worker, semente fixa) com otimização lexicográfica por estágios."""

from ortools.sat.python import cp_model

from .model import (
    ACTIVE_LIMIT,
    BUDGET,
    CALENDAR,
    COMMITTED_LIMIT,
    CONSULT_CAPACITY,
    DEMAND,
    MAX_EVENTS,
    MAX_UNITS,
    NIGHT_LIMIT,
    Problem,
    Solution,
)  # fmt: skip

UNBOUNDED_UNITS = 10_000
K = 1_000_000  # escala dos coeficientes do cenário Equilíbrio
CHANGE_DAY, CHANGE_EVENT, CHANGE_CONSULT = 1000, 100, 1


def _pairs(cands):
    """Pares de candidatos cujas ocupações (ativo + descanso) se sobrepõem."""
    order = sorted(range(len(cands)), key=lambda i: (cands[i].start, cands[i].id))
    out = []
    for ai, i in enumerate(order):
        a = cands[i]
        for j in order[ai + 1 :]:
            b = cands[j]
            if b.start >= a.occ_end:
                break
            if a.start < b.occ_end and b.start < a.occ_end:
                out.append((i, j))
    return out


class _Model:
    def __init__(self, p: Problem, relax: frozenset, pessimistic: bool, require_gap: bool):
        self.p, self.m = p, cp_model.CpModel()
        m = self.m
        self.pess = pessimistic
        self.z = [m.NewBoolVar(f"z{i}") for i in range(len(p.candidates))]
        self.y: dict[str, cp_model.IntVar] = {}
        by_act: dict[str, list[int]] = {a.key: [] for a in p.activities}
        for i, c in enumerate(p.candidates):
            by_act[c.activity_key].append(i)

        for a in p.activities:
            if a.kind == "BLOCK":
                if MAX_UNITS not in relax:
                    m.Add(sum(self.z[i] for i in by_act[a.key]) <= a.max_units)
            else:
                ub = UNBOUNDED_UNITS if MAX_UNITS in relax else a.max_units
                dem = a.demand_limit_pessimistic if pessimistic else a.demand_limit
                if dem is not None and DEMAND not in relax:
                    ub = min(ub, dem)
                if a.slots_limit is not None and CONSULT_CAPACITY not in relax:
                    ub = min(ub, a.slots_limit)
                self.y[a.key] = m.NewIntVar(0, max(ub, 0), f"y_{a.key}")

        def units(a):
            return sum(self.z[i] for i in by_act[a.key]) if a.kind == "BLOCK" else self.y[a.key]

        self.units = {a.key: units(a) for a in p.activities}
        gain_of = (
            (lambda a: a.net_gain_pessimistic_cents)
            if pessimistic
            else (lambda a: a.net_gain_cents)
        )
        self.gain = sum(gain_of(a) * self.units[a.key] for a in p.activities)
        self.gain_central = sum(a.net_gain_cents * self.units[a.key] for a in p.activities)
        self.gain_pess = sum(a.net_gain_pessimistic_cents * self.units[a.key] for a in p.activities)

        blocks = [a for a in p.activities if a.kind == "BLOCK"]
        consults = [a for a in p.activities if a.kind == "CONSULT"]
        self.active10 = sum(10 * a.active_minutes * self.units[a.key] for a in blocks) + sum(
            a.consult_minutes_x10 * self.units[a.key] for a in consults
        )
        self.committed10 = sum(10 * a.committed_minutes * self.units[a.key] for a in blocks) + sum(
            a.consult_minutes_x10 * self.units[a.key] for a in consults
        )
        if p.max_active_minutes is not None and ACTIVE_LIMIT not in relax:
            m.Add(self.active10 <= 10 * p.max_active_minutes)
        if p.max_committed_minutes is not None and COMMITTED_LIMIT not in relax:
            m.Add(self.committed10 <= 10 * p.max_committed_minutes)

        if consults and CONSULT_CAPACITY not in relax:
            lost = sum(c.consult_minutes_lost * self.z[i] for i, c in enumerate(p.candidates))
            m.Add(
                sum(a.consult_minutes_x10 * self.units[a.key] for a in consults) + 10 * lost
                <= 10 * p.consult_free_minutes
            )
        if p.max_night_shifts is not None and NIGHT_LIMIT not in relax:
            m.Add(
                sum(self.z[i] for i, c in enumerate(p.candidates) if c.is_night)
                <= p.max_night_shifts
            )
        if p.marketing_budget_cents is not None and BUDGET not in relax:
            m.Add(
                sum(a.acquisition_cost_cents * self.units[a.key] for a in consults)
                <= p.marketing_budget_cents
            )
        if p.max_block_events is not None and MAX_EVENTS not in relax:
            m.Add(sum(self.z) <= p.max_block_events)
        if CALENDAR not in relax:
            for i, j in _pairs(p.candidates):
                m.Add(self.z[i] + self.z[j] <= 1)

        self.day_vars: dict[str, cp_model.IntVar] = {}
        for i, c in enumerate(p.candidates):
            d = self.day_vars.setdefault(c.day, m.NewBoolVar(f"d_{c.day}"))
            m.AddImplication(self.z[i], d)
        self.days_used = sum(self.day_vars.values())
        self.block_events = sum(self.z)
        self.consult_units = sum(self.units[a.key] for a in consults)
        self.change = (
            CHANGE_DAY * self.days_used
            + CHANGE_EVENT * self.block_events
            + CHANGE_CONSULT * self.consult_units
        )
        self.risk = sum(_risk_coef(a) * self.units[a.key] for a in p.activities)
        # desempate canônico: prefere datas mais cedo e, depois, a ordem das atividades
        order = sorted(
            range(len(p.candidates)), key=lambda i: (p.candidates[i].start, p.candidates[i].id)
        )
        rank = {i: r + 1 for r, i in enumerate(order)}
        self.tie = sum(rank[i] * self.z[i] for i in range(len(p.candidates)))
        if require_gap:
            m.Add(self.gain >= p.gap_cents)


def _risk_coef(a) -> int:
    """Risco por unidade: incerteza do ganho + ganho que depende de demanda não confirmada."""
    unconfirmed = a.net_gain_cents if (a.kind == "CONSULT" and a.demand_limit is None) else 0
    return (a.net_gain_cents - a.net_gain_pessimistic_cents) + unconfirmed


def _consult_cap(a) -> int:
    return a.max_units if a.demand_limit is None else min(a.max_units, a.demand_limit)


def _upper(p: Problem):
    """Limites superiores usados só para normalizar o cenário Equilíbrio."""
    blocks = [a for a in p.activities if a.kind == "BLOCK"]
    consults = [a for a in p.activities if a.kind == "CONSULT"]
    h = sum(10 * a.committed_minutes * a.max_units for a in blocks)
    h += sum(a.consult_minutes_x10 * _consult_cap(a) for a in consults)
    c = CHANGE_DAY * len({c.day for c in p.candidates}) + CHANGE_EVENT * len(p.candidates)
    c += sum(CHANGE_CONSULT * _consult_cap(a) for a in consults)
    r = sum(
        _risk_coef(a) * (a.max_units if a.kind == "BLOCK" else _consult_cap(a))
        for a in p.activities
    )
    return max(h, 1), max(c, 1), max(r, 1)


def _extract(p: Problem, mdl: _Model, solver: cp_model.CpSolver, optimal: bool) -> Solution:
    chosen = tuple(sorted(p.candidates[i].id for i, v in enumerate(mdl.z) if solver.Value(v)))
    units = {a.key: solver.Value(mdl.units[a.key]) for a in p.activities}
    return Solution(
        units=units,
        placements=chosen,
        gain_cents=solver.Value(mdl.gain_central),
        gain_pessimistic_cents=solver.Value(mdl.gain_pess),
        active_minutes=(solver.Value(mdl.active10) + 9) // 10,
        committed_minutes=(solver.Value(mdl.committed10) + 9) // 10,
        days_used=solver.Value(mdl.days_used),
        block_events=solver.Value(mdl.block_events),
        consult_units=solver.Value(mdl.consult_units),
        risk_cents=solver.Value(mdl.risk),
        optimal=optimal,
    )


def _run_stages(p: Problem, mdl: _Model, stages, time_limit: float = 20.0) -> Solution | None:
    """Resolve cada estágio em ordem, fixando o ótimo do anterior (otimização lexicográfica)."""
    last, optimal = None, True
    for expr, sense in stages:
        if sense == "min":
            mdl.m.Minimize(expr)
        else:
            mdl.m.Maximize(expr)
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1  # determinismo
        solver.parameters.random_seed = 0
        solver.parameters.max_time_in_seconds = time_limit
        status = solver.Solve(mdl.m)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        best = round(solver.ObjectiveValue())
        if status == cp_model.OPTIMAL:
            mdl.m.Add(expr == best)
        else:  # tempo esgotado: mantém o que foi achado, sem afirmar otimalidade
            optimal = False
            mdl.m.Add(expr <= best if sense == "min" else expr >= best)
        last = _extract(p, mdl, solver, optimal)
        mdl.m.ClearObjective()
    return last


def _lex(p, relax, pessimistic, require_gap, order):
    """`order`: sequência de (atributo do modelo, 'min'|'max'), em ordem lexicográfica."""
    mdl = _Model(p, relax, pessimistic, require_gap)
    return _run_stages(p, mdl, [(getattr(mdl, attr), sense) for attr, sense in order])


def _balanced(p: Problem, relax: frozenset) -> Solution | None:
    """Pontuação normalizada: horas, mudança de rotina, risco e folga financeira."""
    mdl = _Model(p, relax, False, True)
    hn, cn, rn = _upper(p)
    w = p.weights
    cushion = max(p.gap_cents // 10, 10_000)  # folga desejável: 10% da lacuna (mín. R$ 100)
    cap = mdl.m.NewIntVar(0, 10**12, "cap")
    mdl.m.AddMinEquality(cap, [mdl.gain - p.gap_cents, cushion])

    def coef(weight, denom):  # termo normalizado vira coeficiente inteiro por variável
        return round(weight * K / denom)

    score = (
        coef(w["hours"], hn) * mdl.committed10
        + coef(w["change"], cn) * mdl.change
        + coef(w["risk"], rn) * mdl.risk
        - coef(w["margin"], cushion) * cap
    )
    return _run_stages(p, mdl, [(score, "min"), (mdl.committed10, "min"), (mdl.tie, "min")])


# (usa gap pessimista?, ordem lexicográfica). `tie` = desempate canônico (datas mais cedo).
STAGES = {
    "LESS_HOURS": (
        False,
        (("committed10", "min"), ("change", "min"), ("gain", "max"), ("tie", "min")),
    ),
    "MAX_MARGIN": (
        False,
        (("gain", "max"), ("committed10", "min"), ("change", "min"), ("tie", "min")),
    ),
    "LEAST_CHANGE": (
        False,
        (("change", "min"), ("committed10", "min"), ("gain", "max"), ("tie", "min")),
    ),
    "CONSERVATIVE": (
        True,
        (("committed10", "min"), ("change", "min"), ("gain", "max"), ("tie", "min")),
    ),
}


def solve(p: Problem, objective: str, relax: frozenset = frozenset()) -> Solution | None:
    """Resolve um objetivo. `None` = inviável (a meta não cabe nas restrições)."""
    if objective == "BALANCED":
        return _balanced(p, relax)
    if objective not in STAGES:
        raise ValueError(f"objetivo desconhecido: {objective}")
    pessimistic, order = STAGES[objective]
    return _lex(p, relax, pessimistic, True, order)


def max_achievable(p: Problem, relax: frozenset = frozenset(), pessimistic: bool = False) -> int:
    """Maior ganho líquido possível ignorando a meta (zero unidades é sempre viável)."""
    order = (("gain", "max"), ("committed10", "min"), ("tie", "min"))
    sol = _lex(p, relax, pessimistic, False, order)
    if sol is None:
        return 0
    return sol.gain_pessimistic_cents if pessimistic else sol.gain_cents
