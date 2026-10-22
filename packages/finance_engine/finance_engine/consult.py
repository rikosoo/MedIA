"""Economia de consultas e retornos (PRD 7.2). Taxas em pontos-base; tempo em décimos de minuto.

Tudo em inteiros: nenhum float entra em dinheiro ou tempo. Arredondamento: meio para cima,
aplicado uma única vez por grandeza (não encadeado) para evitar deriva de centavos.
"""

from dataclasses import dataclass
from datetime import date, timedelta

BPS = 10_000

RETURN_PRICE_MODES = ("FREE", "PARTIAL", "FULL", "CUSTOM")
RETURN_DURATION_MODES = ("MINUTES", "PERCENT")


def _div(a: int, b: int) -> int:
    """Divisão inteira com arredondamento meio-para-cima (a >= 0, b > 0)."""
    return (2 * a + b) // (2 * b)


def _bps(value: int, name: str) -> int:
    if not 0 <= value <= BPS:
        raise ValueError(f"{name} deve estar entre 0 e {BPS} bps")
    return value


def resolve_return_minutes(
    consult_minutes: int,
    mode: str,
    minutes: int | None = None,
    percent_bps: int | None = None,
) -> int:
    if consult_minutes <= 0:
        raise ValueError("duração da consulta deve ser positiva")
    if mode == "MINUTES":
        if minutes is None or minutes < 0:
            raise ValueError("minutos do retorno inválidos")
        return minutes
    if mode == "PERCENT":
        if percent_bps is None:
            raise ValueError("percentual de duração do retorno ausente")
        return _div(consult_minutes * _bps(percent_bps, "percentual"), BPS)
    raise ValueError(f"modo de duração desconhecido: {mode}")


def resolve_return_price_cents(
    consult_price_cents: int,
    mode: str,
    delay_days: int,
    partial_bps: int | None = None,
    custom_cents: int | None = None,
    free_within_days: int | None = None,
) -> int:
    """Preço do retorno. `free_within_days`: dentro dessa janela o retorno é gratuito."""
    if consult_price_cents < 0 or delay_days < 0:
        raise ValueError("valores inválidos")
    if free_within_days is not None and delay_days <= free_within_days:
        return 0
    if mode == "FREE":
        return 0
    if mode == "FULL":
        return consult_price_cents
    if mode == "PARTIAL":
        if partial_bps is None:
            raise ValueError("percentual do retorno parcial ausente")
        return _div(consult_price_cents * _bps(partial_bps, "percentual"), BPS)
    if mode == "CUSTOM":
        if custom_cents is None or custom_cents < 0:
            raise ValueError("valor personalizado do retorno inválido")
        return custom_cents
    raise ValueError(f"modo de preço desconhecido: {mode}")


@dataclass(frozen=True)
class ConsultEconomics:
    """Valores por consulta nova *agendada* (inclui faltas), considerando retornos no horizonte."""

    expected_returns_bps: int  # 10000 = 1 retorno agendado por consulta nova agendada
    minutes_x10: int  # 552 = 55,2 min
    revenue_new_cents: int
    revenue_returns_cents: int
    gross_revenue_cents: int
    fee_cents: int
    variable_cost_cents: int
    margin_cents: int  # antes de tributos e despesas fixas
    margin_per_hour_cents: int | None


def consult_economics(
    *,
    price_cents: int,
    consult_minutes: int,
    return_rate_bps: int,
    return_minutes: int,
    return_price_cents: int,
    new_no_show_bps: int = 0,
    return_no_show_bps: int = 0,
    fee_bps: int = 0,
    cost_cents: int = 0,
    return_cost_cents: int = 0,
) -> ConsultEconomics:
    """Retorno nasce de quem compareceu; faltas ocupam a agenda mas não geram receita/custo."""
    if consult_minutes <= 0 or return_minutes < 0:
        raise ValueError("durações inválidas")
    if min(price_cents, return_price_cents, cost_cents, return_cost_cents) < 0:
        raise ValueError("valores monetários não podem ser negativos")
    att_new = BPS - _bps(new_no_show_bps, "falta em consultas novas")
    att_ret = BPS - _bps(return_no_show_bps, "falta em retornos")
    _bps(return_rate_bps, "taxa de retorno")
    _bps(fee_bps, "taxa de pagamento")

    returns_bps = _div(att_new * return_rate_bps, BPS)
    minutes_x10 = consult_minutes * 10 + _div(returns_bps * return_minutes * 10, BPS)

    revenue_new = _div(price_cents * att_new, BPS)
    revenue_ret = _div(return_price_cents * returns_bps * att_ret, BPS * BPS)
    gross = revenue_new + revenue_ret
    fee = _div(gross * fee_bps, BPS)
    cost = _div(cost_cents * att_new, BPS) + _div(
        return_cost_cents * returns_bps * att_ret, BPS * BPS
    )
    margin = gross - fee - cost
    per_hour = (margin * 600) // minutes_x10 if minutes_x10 > 0 else None
    return ConsultEconomics(
        expected_returns_bps=returns_bps,
        minutes_x10=minutes_x10,
        revenue_new_cents=revenue_new,
        revenue_returns_cents=revenue_ret,
        gross_revenue_cents=gross,
        fee_cents=fee,
        variable_cost_cents=cost,
        margin_cents=margin,
        margin_per_hour_cents=per_hour,
    )


def capacity_new_patients(available_minutes: int, minutes_x10: int) -> int:
    """floor(minutos / minutos esperados por paciente novo) — aproximação (PRD 7.2)."""
    if available_minutes < 0 or minutes_x10 <= 0:
        raise ValueError("entradas inválidas")
    return (available_minutes * 10) // minutes_x10


def return_month(consult_date: date, delay_days: int) -> date:
    """Primeiro dia do mês em que o retorno ocupa a agenda."""
    d = consult_date + timedelta(days=delay_days)
    return d.replace(day=1)


def returns_by_month(
    consults: list[tuple[date, int]], expected_returns_bps: int, delay_days: int
) -> dict[date, int]:
    """Retornos esperados por mês, em pontos-base (10000 = 1 retorno)."""
    out: dict[date, int] = {}
    for d, count in consults:
        if count < 0:
            raise ValueError("quantidade negativa")
        m = return_month(d, delay_days)
        out[m] = out.get(m, 0) + count * expected_returns_bps
    return dict(sorted(out.items()))


def return_load(
    returns_bps: int, return_minutes: int, return_price_cents: int, return_no_show_bps: int = 0
) -> tuple[int, int]:
    """(minutos x10 ocupados, receita em centavos) de `returns_bps` retornos agendados."""
    if returns_bps < 0 or return_minutes < 0 or return_price_cents < 0:
        raise ValueError("entradas inválidas")
    att = BPS - _bps(return_no_show_bps, "falta em retornos")
    minutes_x10 = _div(returns_bps * return_minutes * 10, BPS)
    revenue = _div(return_price_cents * returns_bps * att, BPS * BPS)
    return minutes_x10, revenue
