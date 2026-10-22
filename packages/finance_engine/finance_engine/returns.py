"""Consultas e retornos (PRD 7.2). Taxas em pontos-base (10000 = 100%) para evitar float."""

BPS = 10_000


def _check_rate(rate_bps: int) -> None:
    if not 0 <= rate_bps <= BPS:
        raise ValueError("taxa de retorno deve estar entre 0 e 10000 bps")


def expected_minutes_per_new_patient(
    consult_minutes: int, return_minutes: int, return_rate_bps: int
) -> int:
    """Minutos esperados por paciente novo, em décimos de minuto (552 = 55,2 min).

    Retorno só entra se ocorrer no mesmo horizonte analisado.
    """
    _check_rate(return_rate_bps)
    if consult_minutes <= 0 or return_minutes < 0:
        raise ValueError("durações inválidas")
    return consult_minutes * 10 + (return_rate_bps * return_minutes) // 1000


def expected_revenue_per_new_patient_cents(
    consult_price_cents: int, return_price_cents: int, return_rate_bps: int
) -> int:
    _check_rate(return_rate_bps)
    return consult_price_cents + (return_rate_bps * return_price_cents) // BPS
