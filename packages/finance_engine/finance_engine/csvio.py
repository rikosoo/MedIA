"""Leitura e escrita de CSV (PRD 20.3): planilhas brasileiras e proteção contra injeção de fórmula.

Funções puras. Valores monetários em centavos inteiros; nada de float.
"""

import csv
import io
import re
import unicodedata
from datetime import date

FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")
MAX_CELL = 1000


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_key(s: str) -> str:
    """'  Descrição do Lançamento ' -> 'descricao_do_lancamento'."""
    s = strip_accents(s).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def sanitize_cell(value) -> str:
    """Neutraliza fórmulas (CSV injection): células de texto que começam com = + - @ viram texto."""
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    if text.startswith(FORMULA_CHARS):
        return "'" + text
    return text


def unsanitize_cell(text: str) -> str:
    """Desfaz o apóstrofo que a nossa exportação põe, para o arquivo exportado reimportar igual."""
    if len(text) > 1 and text[0] == "'" and text[1] in FORMULA_CHARS:
        return text[1:]
    return text


def clean_text(text: str) -> str:
    """Remove caracteres de controle e espaços repetidos de um campo de texto importado."""
    text = "".join(c for c in text if c == "\t" or c >= " ")
    return " ".join(text.split())


def to_csv(
    rows: list[dict], columns: list[str], text_columns: set[str], delimiter: str = ";"
) -> str:
    """CSV UTF-8 com BOM (abre certo no Excel). `text_columns` são sanitizadas contra fórmulas;
    as demais (números gerados pelo sistema) saem como estão."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([sanitize_cell(r[c]) if c in text_columns else ("" if r[c] is None else r[c])
                    for c in columns])  # fmt: skip
    return "﻿" + buf.getvalue()


def format_amount(cents: int) -> str:
    """2500 -> '25,00' (vírgula decimal, sem milhar: ambíguo para quem reimporta)."""
    sign = "-" if cents < 0 else ""
    reais, rest = divmod(abs(cents), 100)
    return f"{sign}{reais},{rest:02d}"


def parse_amount(text: str) -> int:
    """'R$ 1.234,56', '1234.56', '(50,00)', '-12,5' -> centavos. Valores ambíguos dão erro."""
    t = text.strip().replace("R$", "").replace(" ", "").replace(" ", "")
    if not t:
        raise ValueError("valor vazio")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.startswith("-"):
        neg, t = True, t[1:]
    elif t.startswith("+"):
        t = t[1:]
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        thou = "." if dec == "," else ","
        t = t.replace(thou, "").replace(dec, ".")
    elif "," in t:
        if not re.fullmatch(r"\d+(,\d{1,2})?", t.replace(".", "")) or t.count(",") > 1:
            raise ValueError(f"valor inválido: {text!r}")
        t = t.replace(",", ".")
    elif "." in t:
        if re.fullmatch(r"\d+\.\d{1,2}", t):
            pass
        elif re.fullmatch(r"\d{1,3}(\.\d{3}){2,}", t):  # 1.234.567 só pode ser milhar
            t = t.replace(".", "")
        else:
            raise ValueError(f"valor ambíguo: {text!r}; use vírgula como separador decimal")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", t):
        raise ValueError(f"valor inválido: {text!r}")
    whole, _, frac = t.partition(".")
    cents = int(whole) * 100 + int(frac.ljust(2, "0")) if frac else int(whole) * 100
    return -cents if neg else cents


def parse_date(text: str) -> date:
    """dd/mm/aaaa, dd-mm-aaaa, dd.mm.aaaa ou aaaa-mm-dd."""
    t = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t)
        if not m:
            raise ValueError(f"data inválida: {text!r} (use dd/mm/aaaa ou aaaa-mm-dd)")
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError as exc:
        raise ValueError(f"data inexistente: {text!r}") from exc


def parse_competence(text: str) -> date:
    """aaaa-mm, mm/aaaa ou uma data completa (usa o mês dela); sempre o dia 1."""
    t = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})", t) or None
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{4})", t)
        if m:
            mo, y = int(m.group(1)), int(m.group(2))
        else:
            d = parse_date(t)
            return d.replace(day=1)
    if not 1 <= mo <= 12:
        raise ValueError(f"mês inválido: {text!r}")
    return date(y, mo, 1)


TRUE_WORDS = {"sim", "s", "1", "true", "x", "yes"}
FALSE_WORDS = {"", "nao", "n", "0", "false", "no"}


def parse_bool(text: str) -> bool:
    t = norm_key(text)
    if t in TRUE_WORDS:
        return True
    if t in FALSE_WORDS:
        return False
    raise ValueError(f"valor sim/não inválido: {text!r}")


def detect_delimiter(first_line: str) -> str:
    counts = {d: first_line.count(d) for d in (";", ",", "\t")}
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else ";"


def read_table(text: str, max_rows: int = 5000) -> tuple[list[str], list[tuple[int, dict]]]:
    """(cabeçalhos normalizados, [(nº da linha, {cabeçalho: texto})]). Ignora linhas em branco."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n")]
    first = next((ln for ln in lines if ln.strip()), "")
    if not first:
        raise ValueError("arquivo vazio")
    reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(first))
    headers: list[str] | None = None
    out: list[tuple[int, dict]] = []
    for lineno, cells in enumerate(reader, start=1):
        if not any(c.strip() for c in cells):
            continue
        if headers is None:
            headers = [norm_key(c) for c in cells]
            if len(set(headers)) != len(headers):
                raise ValueError("cabeçalhos repetidos")
            continue
        if len(out) >= max_rows:
            raise ValueError(f"arquivo com mais de {max_rows} linhas")
        row = {
            h: (cells[i].strip()[:MAX_CELL] if i < len(cells) else "")
            for i, h in enumerate(headers)
        }
        out.append((lineno, row))
    return headers or [], out
