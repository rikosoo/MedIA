import pytest

from finance_engine.csvio import (
    clean_text,
    format_amount,
    norm_key,
    parse_amount,
    parse_bool,
    parse_competence,
    parse_date,
    read_table,
    sanitize_cell,
    to_csv,
    unsanitize_cell,
)


@pytest.mark.parametrize("cell", ["=SOMA(A1:A9)", "+55 11 9999", "-2+3", "@cmd", "\tx", "\rx"])
def test_formula_injection_is_neutralized(cell):
    out = sanitize_cell(cell)
    assert out.startswith("'") and out[1:] == cell
    assert unsanitize_cell(out) == cell  # o arquivo exportado reimporta igual


def test_plain_text_untouched_and_nulls_removed():
    assert sanitize_cell("Aluguel da sala") == "Aluguel da sala"
    assert sanitize_cell(None) == "" and sanitize_cell("a\x00b") == "ab"
    assert unsanitize_cell("'Maria") == "'Maria"  # só desfaz o que nós criamos


def test_to_csv_sanitizes_only_text_columns_and_has_bom():
    rows = [{"d": '=HYPERLINK("http://x")', "v": "-25,00"}]
    out = to_csv(rows, ["d", "v"], {"d"})
    assert out.startswith("﻿d;v\r\n")
    assert "'=HYPERLINK" in out and ";-25,00" in out  # número negativo gerado por nós fica intacto


@pytest.mark.parametrize(
    "text,cents",
    [
        ("R$ 1.234,56", 123_456), ("1234.56", 123_456), ("2500,00", 250_000), ("1.234.567", 123_456_700),
        ("(50,00)", -5_000), ("-12,5", -1_250), ("0,10", 10), ("+7", 700), ("10", 1_000),
    ],
)  # fmt: skip
def test_parse_amount(text, cents):
    assert parse_amount(text) == cents


@pytest.mark.parametrize("bad", ["", "abc", "1.234", "1,234,5", "12,345", "1e3", "--5"])
def test_parse_amount_rejects_ambiguous_or_invalid(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)


def test_no_float_drift_in_amounts():
    assert sum(parse_amount("0,10") for _ in range(10)) == 100
    assert format_amount(-250_005) == "-2500,05" and format_amount(5) == "0,05"


def test_dates_and_competence():
    assert parse_date("05/10/2026").isoformat() == "2026-10-05"
    assert parse_date("2026-10-05").isoformat() == "2026-10-05"
    assert parse_competence("2026-10").isoformat() == "2026-10-01"
    assert parse_competence("10/2026").isoformat() == "2026-10-01"
    assert parse_competence("15/10/2026").isoformat() == "2026-10-01"
    for bad in ("31/02/2026", "2026-13", "ontem", "13/2026"):
        with pytest.raises(ValueError):
            parse_competence(bad) if bad != "31/02/2026" else parse_date(bad)


def test_bool_and_headers():
    assert parse_bool("Sim") and parse_bool("x") and not parse_bool("") and not parse_bool("Não")
    with pytest.raises(ValueError):
        parse_bool("talvez")
    assert norm_key("  Descrição do Lançamento ") == "descricao_do_lancamento"
    assert clean_text("  a \x07 b\n c ") == "a b c"


def test_read_table_detects_delimiter_bom_and_skips_blank_lines():
    text = "﻿Data;Descrição;Valor\r\n05/10/2026;Aluguel;2.500,00\r\n\r\n06/10/2026;Luz;180,00\r\n"
    headers, rows = read_table(text)
    assert headers == ["data", "descricao", "valor"]
    assert [(n, r["valor"]) for n, r in rows] == [(2, "2.500,00"), (4, "180,00")]
    comma = read_table("a,b\n1,2\n")
    assert comma[1][0][1] == {"a": "1", "b": "2"}


def test_read_table_limits_and_errors():
    with pytest.raises(ValueError):
        read_table("")
    with pytest.raises(ValueError):
        read_table("a;a\n1;2")
    with pytest.raises(ValueError):
        read_table("a\n" + "\n".join(["x"] * 6), max_rows=5)
    _, rows = read_table("a;b\n1")  # linha curta: célula faltante vira vazio
    assert rows[0][1] == {"a": "1", "b": ""}
