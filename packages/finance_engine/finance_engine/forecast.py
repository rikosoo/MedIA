"""Previsão simples (PRD §9.3): cenários explícitos, sem prometer nada.

Sem histórico suficiente usa premissas manuais declaradas; com histórico usa o aproveitamento
observado do plano (realizado / planejado), excluindo meses incompletos.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

BPS = 10_000
MIN_HISTORY_MONTHS = 3  # meses completos com plano e realizado para usar histórico
MIN_SEASONALITY_MONTHS = 12
MAX_OPTIMISTIC_BPS = 12_000  # teto do aproveitamento otimista


@dataclass(frozen=True)
class Attainment:
    conservative_bps: int
    base_bps: int
    optimistic_bps: int
    source: str  # HISTORY | MANUAL
    months_used: int


def attainment_ratio(planned_gross: int, realized_gross: int) -> int | None:
    """Aproveitamento do mês em bps; None se não havia plano (não há o que comparar)."""
    if planned_gross <= 0:
        return None
    if realized_gross < 0:
        raise ValueError("realizado negativo")
    return (realized_gross * BPS) // planned_gross


def scenario_attainment(
    ratios: list[int], manual: tuple[int, int, int] = (8_000, 10_000, 10_000)
) -> Attainment:
    """Conservador = pior mês, base = média, otimista = melhor mês (com teto). Com menos de
    `MIN_HISTORY_MONTHS` meses, usa as premissas manuais (conservador, base, otimista)."""
    if len(ratios) >= MIN_HISTORY_MONTHS:
        mean = sum(ratios) // len(ratios)
        return Attainment(
            min(ratios), mean, min(max(ratios), MAX_OPTIMISTIC_BPS), "HISTORY", len(ratios)
        )
    c, b, o = manual
    if not (0 <= c <= b <= o <= MAX_OPTIMISTIC_BPS):
        raise ValueError("premissas manuais devem ter conservador <= base <= otimista (máx. 120%)")
    return Attainment(c, b, o, "MANUAL", len(ratios))


def weekdays_in_month(year: int, month: int) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1) if date(year, month, d).weekday() < 5]


def working_days_factor(
    year: int, month: int, holidays: set[date], leave_days: set[date]
) -> tuple[int, int, int]:
    """(fator em bps, dias úteis do mês, dias úteis perdidos). Feriado confirmado e dia de
    férias/licença do médico em dia útil reduzem a capacidade de atender."""
    days = weekdays_in_month(year, month)
    lost = sum(1 for d in days if d in holidays or d in leave_days)
    if not days:
        return BPS, 0, 0
    return ((len(days) - lost) * BPS) // len(days), len(days), lost


def expand_leave_days(start: date, end: date, window_start: date, window_end: date) -> set[date]:
    out: set[date] = set()
    d = max(start, window_start)
    while d <= min(end, window_end):
        out.add(d)
        d += timedelta(days=1)
    return out


def seasonal_indices(monthly: dict[tuple[int, int], int]) -> dict[int, int] | None:
    """Índice por mês do calendário (10000 = média), a partir de >= 12 meses completos de receita.
    Com anos repetidos, tira a média por mês. None se o histórico não basta (sem inventar)."""
    if len(monthly) < MIN_SEASONALITY_MONTHS:
        return None
    by_month: dict[int, list[int]] = {}
    for (_, m), v in monthly.items():
        by_month.setdefault(m, []).append(v)
    if len(by_month) < 12:
        return None
    avg = {m: sum(v) // len(v) for m, v in by_month.items()}
    overall = sum(avg.values()) // 12
    if overall <= 0:
        return None
    return {m: (v * BPS) // overall for m, v in avg.items()}


def inflate(amount_cents: int, monthly_bps: int, months_ahead: int) -> int:
    """Despesa corrigida por inflação mensal composta, em inteiros (arredonda para cima)."""
    if months_ahead < 0 or monthly_bps < -BPS:
        raise ValueError("parâmetros inválidos")
    value, scale = amount_cents, 1
    for _ in range(months_ahead):
        value *= BPS + monthly_bps
        scale *= BPS
        g = _gcd(value, scale)
        value, scale = value // g, scale // g
    return -(-value // scale) if value >= 0 else -((-value) // scale)


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a) or 1


def reserve_needed(nets: list[int], floor_cents: int) -> tuple[int, int]:
    """(reserva, meses abaixo do piso): soma do que falta para atingir `floor_cents` em cada mês."""
    short = [max(floor_cents - n, 0) for n in nets]
    return sum(short), sum(1 for s in short if s > 0)
