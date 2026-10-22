def goal_gap_cents(target_net_cents: int, projected_net_cents: int) -> int:
    """lacuna = max(0, meta_liquida − liquido_projetado_base) (PRD 7.4)."""
    if target_net_cents < 0:
        raise ValueError("meta não pode ser negativa")
    return max(0, target_net_cents - projected_net_cents)


# ---------------------------------------------------------------------------
# Projeção do mês (PRD 7.3–7.4). Tudo em centavos/minutos inteiros.
# ---------------------------------------------------------------------------
from dataclasses import dataclass  # noqa: E402

from .consult import _div  # noqa: E402

CERTAINTIES = ("GUARANTEED", "PROBABLE")
METRICS = ("PERSONAL_NET", "CLINIC_PROFIT", "GROSS_REVENUE")


@dataclass(frozen=True)
class StreamInput:
    """Uma fonte de renda no mês: `units` × valores por unidade."""

    key: str
    kind: str  # WORK | SERVICE
    units: int
    certainty: str  # GUARANTEED | PROBABLE
    gross_unit_cents: int
    variable_unit_cents: int
    tax_rate_bps: int  # estimativa; nunca tratada como tributo apurado
    active_minutes_x10_unit: int
    committed_minutes_x10_unit: int


@dataclass(frozen=True)
class StreamResult:
    key: str
    kind: str
    certainty: str
    units: int
    gross_cents: int
    variable_cost_cents: int
    tax_cents: int
    net_cents: int
    active_minutes: int
    committed_minutes: int
    net_per_committed_hour_cents: int | None


def compute_stream(s: StreamInput) -> StreamResult:
    if s.units < 0 or min(s.gross_unit_cents, s.variable_unit_cents) < 0:
        raise ValueError("unidades e valores não podem ser negativos")
    if s.certainty not in CERTAINTIES:
        raise ValueError(f"certeza desconhecida: {s.certainty}")
    if not 0 <= s.tax_rate_bps <= 10_000:
        raise ValueError("alíquota fora de 0–10000 bps")
    gross = s.units * s.gross_unit_cents
    variable = s.units * s.variable_unit_cents
    tax = _div(gross * s.tax_rate_bps, 10_000)  # sobre o total, não por unidade
    net = gross - variable - tax
    active = _div(s.units * s.active_minutes_x10_unit, 10)
    committed = _div(s.units * s.committed_minutes_x10_unit, 10)
    per_hour = (net * 60) // committed if committed > 0 else None
    return StreamResult(
        s.key, s.kind, s.certainty, s.units, gross, variable, tax, net, active, committed, per_hour
    )


@dataclass(frozen=True)
class Projection:
    metric: str
    gross_cents: int
    income_net_cents: int
    expenses_cents: int
    value_cents: int  # valor da métrica com tudo que foi planejado
    assured_cents: int  # só fontes GUARANTEED (despesas continuam inteiras)
    probable_cents: int  # value − assured


def _metric_value(
    streams: list[StreamResult], expenses_cents: int, metric: str
) -> tuple[int, int, int]:
    """(valor da métrica, bruto, líquido das rendas) para um conjunto de fontes."""
    gross = sum(s.gross_cents for s in streams)
    net = sum(s.net_cents for s in streams)
    if metric == "GROSS_REVENUE":
        return gross, gross, net
    if metric == "CLINIC_PROFIT":
        clinic = sum(s.net_cents for s in streams if s.kind == "SERVICE")
        return clinic - expenses_cents, gross, net
    if metric == "PERSONAL_NET":
        return net - expenses_cents, gross, net
    raise ValueError(f"métrica desconhecida: {metric}")


def project(streams: list[StreamResult], expenses_cents: int, metric: str) -> Projection:
    """`expenses_cents` = despesas do mês (realizadas + planejadas), abatidas uma única vez.

    Custos variáveis por unidade já estão no líquido de cada fonte; lançar o mesmo custo também
    como despesa o contaria duas vezes (premissa exibida ao usuário).
    """
    if expenses_cents < 0:
        raise ValueError("despesas não podem ser negativas (use estornos já abatidos)")
    value, gross, net = _metric_value(streams, expenses_cents, metric)
    assured, _, _ = _metric_value(
        [s for s in streams if s.certainty == "GUARANTEED"], expenses_cents, metric
    )
    return Projection(metric, gross, net, expenses_cents, value, assured, value - assured)


def effective_target_cents(target_cents: int, reserve_cents: int, use_reserves: bool) -> int:
    """Reservas só reduzem a meta se o usuário ligar a opção (desligada por padrão)."""
    if target_cents < 0 or reserve_cents < 0:
        raise ValueError("valores não podem ser negativos")
    return max(0, target_cents - reserve_cents) if use_reserves else target_cents


def goal_status(
    effective_target_cents: int, projected_cents: int, constraints_ok: bool | None
) -> str:
    """MET exige meta batida *e* restrições duras satisfeitas (PRD 7.4)."""
    if projected_cents < effective_target_cents:
        return "SHORT"
    return "MET_WITH_ISSUES" if constraints_ok is False else "MET"
