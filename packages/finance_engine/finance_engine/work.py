def per_hour_cents(amount_cents: int, minutes: int) -> int | None:
    """Valor por hora em centavos; None quando não há tempo informado (sem divisão por zero)."""
    if minutes <= 0:
        return None
    return (amount_cents * 60) // minutes
