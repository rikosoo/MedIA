"""Validação *independente* de uma solução: não usa o solver nem as variáveis do modelo.

Recalcula dinheiro, tempo, demanda, orçamento e sobreposição a partir do `Problem` e das unidades
escolhidas. Qualquer divergência é bug do motor, não preferência do usuário.
"""

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


def _usage(p: Problem, sol: Solution):
    cands = {c.id: c for c in p.candidates}
    chosen = [cands[i] for i in sol.placements]
    active10 = committed10 = 0
    for a in p.activities:
        n = sol.units.get(a.key, 0)
        if a.kind == "BLOCK":
            active10 += 10 * a.active_minutes * n
            committed10 += 10 * a.committed_minutes * n
        else:
            active10 += a.consult_minutes_x10 * n
            committed10 += a.consult_minutes_x10 * n
    return chosen, active10, committed10


def check(p: Problem, sol: Solution, pessimistic: bool = False) -> list[str]:
    """Lista de violações (vazia = solução válida)."""
    bad: list[str] = []
    cands = {c.id: c for c in p.candidates}
    if len(set(sol.placements)) != len(sol.placements):
        bad.append("candidato escolhido mais de uma vez")
    for cid in sol.placements:
        if cid not in cands:
            bad.append(f"candidato inexistente: {cid}")
    if bad:
        return bad
    chosen, active10, committed10 = _usage(p, sol)

    for a in p.activities:
        n = sol.units.get(a.key, 0)
        if not isinstance(n, int) or n < 0:
            bad.append(f"{a.key}: quantidade inválida")
            continue
        if a.kind == "BLOCK":
            placed = sum(1 for c in chosen if c.activity_key == a.key)
            if placed != n:
                bad.append(f"{a.key}: unidades ({n}) diferem das ocupações escolhidas ({placed})")
        dem = a.demand_limit_pessimistic if pessimistic else a.demand_limit
        if n > a.max_units:
            bad.append(f"{MAX_UNITS}: {a.key} {n} > {a.max_units}")
        if a.kind == "CONSULT":
            if dem is not None and n > dem:
                bad.append(f"{DEMAND}: {a.key} {n} > {dem}")
            if a.slots_limit is not None and n > a.slots_limit:
                bad.append(f"{CONSULT_CAPACITY}: {a.key} excede os horários existentes")

    if p.max_active_minutes is not None and active10 > 10 * p.max_active_minutes:
        bad.append(f"{ACTIVE_LIMIT}: horas ativas acima do limite")
    if p.max_committed_minutes is not None and committed10 > 10 * p.max_committed_minutes:
        bad.append(f"{COMMITTED_LIMIT}: horas comprometidas acima do limite")

    consult10 = sum(
        a.consult_minutes_x10 * sol.units.get(a.key, 0) for a in p.activities if a.kind == "CONSULT"
    )
    lost = sum(c.consult_minutes_lost for c in chosen)
    if consult10 + 10 * lost > 10 * p.consult_free_minutes and consult10 > 0:
        bad.append(f"{CONSULT_CAPACITY}: consultas não cabem nos minutos livres")
    if p.max_night_shifts is not None and sum(1 for c in chosen if c.is_night) > p.max_night_shifts:
        bad.append(f"{NIGHT_LIMIT}: plantões noturnos acima do limite")
    spend = sum(a.acquisition_cost_cents * sol.units.get(a.key, 0) for a in p.activities)
    if p.marketing_budget_cents is not None and spend > p.marketing_budget_cents:
        bad.append(f"{BUDGET}: captação acima do orçamento")
    if p.max_block_events is not None and len(chosen) > p.max_block_events:
        bad.append(f"{MAX_EVENTS}: eventos acima do limite")

    for i, a in enumerate(chosen):
        for b in chosen[i + 1 :]:
            if a.start < b.occ_end and b.start < a.occ_end:
                bad.append(f"{CALENDAR}: {a.id} e {b.id} se sobrepõem (inclui descanso)")

    gain = sum(
        (a.net_gain_pessimistic_cents if pessimistic else a.net_gain_cents)
        * sol.units.get(a.key, 0)
        for a in p.activities
    )
    if gain < p.gap_cents:
        bad.append(f"META: ganho {gain} menor que a lacuna {p.gap_cents}")
    if gain != (sol.gain_pessimistic_cents if pessimistic else sol.gain_cents):
        bad.append("ganho reportado diverge do recalculado")
    return bad


def binding(p: Problem, sol: Solution) -> list[str]:
    """Restrições que estão no limite exato na solução (explicam por que não dá para ir além)."""
    chosen, active10, committed10 = _usage(p, sol)
    out: list[str] = []
    if p.max_active_minutes is not None and active10 == 10 * p.max_active_minutes:
        out.append(ACTIVE_LIMIT)
    if p.max_committed_minutes is not None and committed10 == 10 * p.max_committed_minutes:
        out.append(COMMITTED_LIMIT)
    for a in p.activities:
        n = sol.units.get(a.key, 0)
        if n and n == a.max_units:
            out.append(f"{MAX_UNITS}:{a.key}")
        if a.kind == "CONSULT" and n and a.demand_limit is not None and n == a.demand_limit:
            out.append(f"{DEMAND}:{a.key}")
    if (
        p.max_night_shifts is not None
        and sum(1 for c in chosen if c.is_night) == p.max_night_shifts
    ):
        out.append(NIGHT_LIMIT)
    spend = sum(a.acquisition_cost_cents * sol.units.get(a.key, 0) for a in p.activities)
    if p.marketing_budget_cents is not None and spend and spend == p.marketing_budget_cents:
        out.append(BUDGET)
    return out
