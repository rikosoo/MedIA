"""Custo fixo por hora e ponto de equilíbrio do consultório (tudo em centavos/minutos inteiros).

Margem de contribuição = o que sobra de cada consulta depois do custo variável e da taxa de cartão
(vem de `consult_economics`), ainda antes de tributos e de custos fixos. O custo fixo do mês é
rateado pelo *tempo* de cada serviço; o ponto de equilíbrio é o volume em que a contribuição
cobre o custo fixo. Nada aqui usa float.
"""

from dataclasses import dataclass

BPS = 10_000


@dataclass(frozen=True)
class ServiceInput:
    key: str
    units: int  # consultas novas planejadas no mês (0 = sem plano)
    margin_cents: int  # contribuição por consulta nova agendada (pode ser <= 0)
    minutes_x10: int  # tempo esperado por consulta, com retornos (552 = 55,2 min)


@dataclass(frozen=True)
class ServiceCost:
    key: str
    fixed_per_unit_cents: int | None  # parcela do custo fixo desta consulta (rateio por tempo)
    net_per_unit_cents: int | None  # contribuição - custo fixo rateado
    margin_per_hour_cents: int | None  # contribuição por hora de atendimento
    net_per_hour_cents: int | None  # contribuição por hora - custo fixo por hora
    breakeven_alone_units: int | None  # consultas/mês só deste serviço para cobrir o fixo


@dataclass(frozen=True)
class BreakEven:
    fixed_cents: int
    planned_units: int
    contribution_cents: int  # planejado: soma(unidades x margem)
    result_cents: int  # contribuição planejada - custo fixo
    status: str  # NO_PLAN | ABOVE | BELOW | UNREACHABLE
    avg_margin_cents: int | None  # margem média ponderada pelo mix planejado
    units: tuple[tuple[str, int], ...]  # equilíbrio por serviço, no mix planejado
    total_units: int
    minutes: int  # tempo de atendimento no equilíbrio
    safety_bps: int | None  # (planejado - equilíbrio) / planejado, em bps (pode ser < 0)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def fixed_per_hour_cents(fixed_cents: int, minutes: int) -> int | None:
    """Custo fixo por hora de atendimento; None se não há horas para ratear."""
    if fixed_cents < 0:
        raise ValueError("custo fixo não pode ser negativo")
    if minutes <= 0:
        return None
    return (fixed_cents * 60 + minutes // 2) // minutes  # arredonda ao centavo mais próximo


def per_service(fixed_cents: int, minutes: int, s: ServiceInput) -> ServiceCost:
    """Rateio do custo fixo pelo tempo; `minutes` é o total-base (planejado ou da agenda)."""
    if s.minutes_x10 <= 0:
        return ServiceCost(s.key, None, None, None, None, None)
    per_hour = fixed_per_hour_cents(fixed_cents, minutes)
    fixed_unit = (
        None if minutes <= 0 else (fixed_cents * s.minutes_x10 + 5 * minutes) // (10 * minutes)
    )
    margin_hour = (s.margin_cents * 600 + s.minutes_x10 // 2) // s.minutes_x10
    return ServiceCost(
        key=s.key,
        fixed_per_unit_cents=fixed_unit,
        net_per_unit_cents=None if fixed_unit is None else s.margin_cents - fixed_unit,
        margin_per_hour_cents=margin_hour,
        net_per_hour_cents=None if per_hour is None else margin_hour - per_hour,
        breakeven_alone_units=_ceil_div(fixed_cents, s.margin_cents)
        if s.margin_cents > 0
        else None,
    )


def breakeven(fixed_cents: int, services: list[ServiceInput]) -> BreakEven:
    """Volume em que a contribuição cobre o custo fixo, mantendo a proporção do mix planejado."""
    if fixed_cents < 0:
        raise ValueError("custo fixo não pode ser negativo")
    if any(s.units < 0 for s in services):
        raise ValueError("unidades negativas")
    planned = sum(s.units for s in services)
    contribution = sum(s.units * s.margin_cents for s in services)
    result = contribution - fixed_cents
    if planned == 0:
        return BreakEven(fixed_cents, 0, 0, -fixed_cents, "NO_PLAN", None, (), 0, 0, None)
    avg = contribution // planned
    if contribution <= 0 or avg <= 0:
        return BreakEven(
            fixed_cents, planned, contribution, result, "UNREACHABLE", avg, (), 0, 0, None
        )
    if fixed_cents == 0:
        by = tuple((s.key, 0) for s in services if s.units > 0)
        return BreakEven(fixed_cents, planned, contribution, result, "ABOVE", avg, by, 0, 0, BPS)
    # total de consultas no mix: ceil(fixo * planejado / contribuição); cada serviço leva sua fração
    total = _ceil_div(fixed_cents * planned, contribution)
    by = tuple((s.key, _ceil_div(total * s.units, planned)) for s in services if s.units > 0)
    minutes = sum(u * s.minutes_x10 for (k, u) in by for s in services if s.key == k) // 10
    safety = (contribution - (total * contribution) // planned) * BPS // contribution
    status = "ABOVE" if result >= 0 else "BELOW"
    total_units = sum(u for _, u in by)
    return BreakEven(
        fixed_cents, planned, contribution, result, status, avg, by, total_units, minutes, safety
    )
