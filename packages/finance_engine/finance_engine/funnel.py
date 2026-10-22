"""Funil de captação (PRD §6.9, §9): só números agregados, sem dados de pacientes.

Etapas: contatos (leads) -> agendamentos -> comparecimentos. "Respondidos no prazo" é uma métrica de
apoio (tempo de resposta), não uma etapa. Taxas em pontos-base; dinheiro em centavos.
"""

from dataclasses import dataclass

BPS = 10_000

STAGES = ("RESPONSE_TIME", "BOOKING", "ATTENDANCE")


def _rate(num: int, den: int) -> int | None:
    return None if den <= 0 else (num * BPS) // den


def _div_round(a: int, b: int) -> int | None:
    return None if b <= 0 else (2 * a + b) // (2 * b)


def validate_counts(leads: int, responded: int, bookings: int, attended: int, spend: int) -> None:
    if min(leads, responded, bookings, attended, spend) < 0:
        raise ValueError("valores não podem ser negativos")
    if responded > leads:
        raise ValueError("respondidos no prazo não podem exceder os contatos")
    if bookings > leads:
        raise ValueError("agendamentos não podem exceder os contatos")
    if attended > bookings:
        raise ValueError("comparecimentos não podem exceder os agendamentos")


@dataclass(frozen=True)
class Funnel:
    leads: int
    responded_in_time: int
    bookings: int
    attended: int
    spend_cents: int
    response_rate_bps: int | None  # respondidos no prazo / contatos
    lead_to_booking_bps: int | None
    booking_to_attendance_bps: int | None
    lead_to_attendance_bps: int | None
    cost_per_booking_cents: int | None
    cost_per_attended_cents: int | None


def funnel(leads: int, responded: int, bookings: int, attended: int, spend_cents: int) -> Funnel:
    validate_counts(leads, responded, bookings, attended, spend_cents)
    return Funnel(
        leads, responded, bookings, attended, spend_cents,
        _rate(responded, leads), _rate(bookings, leads), _rate(attended, bookings),
        _rate(attended, leads), _div_round(spend_cents, bookings),
        _div_round(spend_cents, attended),
    )  # fmt: skip


def combine(items: list[Funnel]) -> Funnel:
    """Soma funis (canais ou meses) e recalcula as taxas sobre o total (não média de taxas)."""
    return funnel(
        sum(f.leads for f in items), sum(f.responded_in_time for f in items),
        sum(f.bookings for f in items), sum(f.attended for f in items),
        sum(f.spend_cents for f in items),
    )  # fmt: skip


def bottleneck(f: Funnel) -> str | None:
    """Etapa com a menor taxa (compara só as suas próprias etapas; não há referência externa)."""
    rates = {"RESPONSE_TIME": f.response_rate_bps, "BOOKING": f.lead_to_booking_bps,
             "ATTENDANCE": f.booking_to_attendance_bps}  # fmt: skip
    known = {k: v for k, v in rates.items() if v is not None}
    if len(known) < 3:
        return None
    return min(STAGES, key=lambda k: (known[k], STAGES.index(k)))


# Recomendações permitidas (PRD §9.2): operacionais e compatíveis com publicidade médica.
RECOMMENDATIONS = {
    "RESPONSE_TIME": [
        "Reduza o tempo de primeira resposta: defina um prazo-alvo e quem responde a cada horário.",
        "Verifique a cobertura da secretaria em férias, feriados e horários de pico.",
    ],
    "BOOKING": [
        "Facilite o agendamento: mais de um canal e horários claros na primeira resposta.",
        "Confira se há horários livres compatíveis com a demanda (veja Agenda & Capacidade).",
    ],
    "ATTENDANCE": [
        "Reduza faltas com confirmação de consulta e política de remarcação clara.",
        "Revise horários de maior falta e a antecedência dos agendamentos.",
    ],
}


@dataclass(frozen=True)
class Need:
    patients: int
    bookings: int | None
    leads: int | None
    enough_data: bool


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def required_leads(patients: int, f: Funnel, min_leads: int = 30) -> Need:
    """Quantos contatos e agendamentos são necessários para `patients` comparecimentos, com as
    taxas observadas. Poucos contatos no histórico => `enough_data=False` (estimativa frágil)."""
    if patients < 0:
        raise ValueError("pacientes não pode ser negativo")
    if not f.booking_to_attendance_bps or not f.lead_to_booking_bps:
        return Need(patients, None, None, False)
    bookings = _ceil_div(patients * BPS, f.booking_to_attendance_bps)
    leads = _ceil_div(bookings * BPS, f.lead_to_booking_bps)
    return Need(patients, bookings, leads, f.leads >= min_leads)


def conversion_drop(current: Funnel, history: list[Funnel], min_leads: int = 20,
                    drop_bps: int = 2_500) -> int | None:  # fmt: skip
    """Queda relativa (bps) da conversão contato->agendamento contra o histórico, se relevante."""
    base = combine(history) if history else None
    if base is None or base.lead_to_booking_bps in (None, 0) or current.lead_to_booking_bps is None:
        return None
    if current.leads < min_leads or base.leads < min_leads:
        return None
    drop = (
        (base.lead_to_booking_bps - current.lead_to_booking_bps) * BPS // base.lead_to_booking_bps
    )
    return drop if drop >= drop_bps else None
