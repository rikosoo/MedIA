"""Dinheiro sempre em centavos inteiros; nunca float binário (PRD 13.2)."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation


def parse_brl_to_cents(value: str | int | Decimal) -> int:
    """Converte '1.234,56' / '1234.56' / Decimal em centavos (arredondamento bancário)."""
    if isinstance(value, float):
        raise TypeError("float não é aceito para valores monetários")
    if isinstance(value, int):
        return value * 100
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"valor monetário inválido: {value!r}") from exc
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def format_brl(cents: int) -> str:
    """Formata centavos como 'R$ 1.234,56', preservando o sinal (déficit nunca é escondido)."""
    sign = "-" if cents < 0 else ""
    reais, rest = divmod(abs(cents), 100)
    return f"{sign}R$ {reais:,}".replace(",", ".") + f",{rest:02d}"
