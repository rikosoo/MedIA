import calendar
from datetime import date

FREQUENCIES = ("MONTHLY", "YEARLY")


def occurrences_in_month(frequency: str, start: date, stop: date | None, month: date) -> list[date]:
    """Datas em que uma recorrência cai no mês de `month` (dia ajustado ao fim do mês)."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequência desconhecida: {frequency}")
    if frequency == "YEARLY" and month.month != start.month:
        return []
    last_day = calendar.monthrange(month.year, month.month)[1]
    d = date(month.year, month.month, min(start.day, last_day))
    if d < start or (stop is not None and d > stop):
        return []
    return [d]
