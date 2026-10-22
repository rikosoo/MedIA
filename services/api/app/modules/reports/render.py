"""Documento de relatório estruturado e seus renderizadores (JSON, CSV e PDF).

Células: texto, inteiro (contagem) ou dicionário tipado:
  {"cents": n} dinheiro · {"bps": n} percentual · {"minutes": n} duração.
"""

import csv
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from finance_engine import format_amount, format_brl, sanitize_cell

NAVY = colors.HexColor("#0b2a5b")
EMERALD = colors.HexColor("#047857")
GRID = colors.HexColor("#d8dfea")
ZEBRA = colors.HexColor("#f6f8fb")

# A fonte padrão do PDF (Helvetica/WinAnsi) não tem estes glifos: troca por equivalentes.
PDF_SUBS = {"×": "x", "−": "-", "≈": "~", "→": "->", "≥": ">=", "≤": "<=", "•": "-", "…": "..."}


def money(cents: int) -> dict:
    return {"cents": cents}


def pct(bps: int | None) -> dict | str:
    return {"bps": bps} if bps is not None else "-"


def minutes(n: int) -> dict:
    return {"minutes": n}


def fmt_minutes(n: int) -> str:
    h, m = divmod(n, 60)
    return f"{m} min" if h == 0 else (f"{h}h" if m == 0 else f"{h}h{m:02d}")


def fmt_cell(c) -> str:
    if isinstance(c, dict):
        if "cents" in c:
            return format_brl(c["cents"])
        if "bps" in c:
            return f"{round(c['bps'] / 100)}%"
        if "minutes" in c:
            return fmt_minutes(c["minutes"])
    return "" if c is None else str(c)


def csv_cell(c):
    """Planilha: números como número (vírgula decimal), texto neutralizado contra fórmulas."""
    if isinstance(c, dict):
        if "cents" in c:
            return format_amount(c["cents"])
        if "bps" in c:
            return f"{c['bps'] / 100:.2f}".replace(".", ",")
        if "minutes" in c:
            return c["minutes"]
    if isinstance(c, int):
        return c
    return sanitize_cell(c)


def section(heading: str, kpis=None, tables=None, notes=None) -> dict:
    return {"heading": heading, "kpis": kpis or [], "tables": tables or [], "notes": notes or []}


def table(title: str, columns: list[str], rows: list[list]) -> dict:
    return {"title": title, "columns": columns, "rows": rows}


def document(template: str, title: str, subtitle: str, period: str, sections: list[dict],
             generated_at: datetime) -> dict:  # fmt: skip
    return {
        "template": template, "title": title, "subtitle": subtitle, "period": period,
        "generated_at": generated_at.strftime("%d/%m/%Y %H:%M"),
        "sections": sections,
        "disclaimer": (
            "Relatório gerencial com estimativas informadas por você. Não é consultoria "
            "tributária, contábil ou jurídica; tributos e retenções não foram validados."
        ),
    }  # fmt: skip


def to_csv_bytes(doc: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow([sanitize_cell(doc["title"])])
    w.writerow([sanitize_cell(doc["subtitle"]), sanitize_cell(f"Gerado em {doc['generated_at']}")])
    for sec in doc["sections"]:
        w.writerow([])
        w.writerow([sanitize_cell(sec["heading"])])
        for label, value in sec["kpis"]:
            w.writerow([sanitize_cell(label), csv_cell(value)])
        for t in sec["tables"]:
            w.writerow([])
            w.writerow([sanitize_cell(t["title"])])
            w.writerow([sanitize_cell(c) for c in t["columns"]])
            for row in t["rows"]:
                w.writerow([csv_cell(c) for c in row])
        for n in sec["notes"]:
            w.writerow([sanitize_cell(n)])
    w.writerow([])
    w.writerow([sanitize_cell(doc["disclaimer"])])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _pdf_text(s: str) -> str:
    for a, b in PDF_SUBS.items():
        s = s.replace(a, b)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_pdf_bytes(doc: dict) -> bytes:
    buf = io.BytesIO()
    wide = any(len(t["columns"]) > 6 for s in doc["sections"] for t in s["tables"])
    page = landscape(A4) if wide else A4
    pdf = SimpleDocTemplate(buf, pagesize=page, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=_plain(doc["title"]),
                            author="FinanceMed AI")  # fmt: skip
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=NAVY, alignment=TA_LEFT, fontSize=20)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=NAVY, spaceBefore=10, fontSize=13)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], textColor=EMERALD, fontSize=10.5)
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=9, leading=12)
    cell = ParagraphStyle("cell", parent=body, fontSize=8, leading=10)
    cellh = ParagraphStyle("cellh", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    small = ParagraphStyle("small", parent=body, fontSize=7.5, textColor=colors.HexColor("#52607a"))
    avail = page[0] - 32 * mm

    P = lambda text, style: Paragraph(_pdf_text(text), style)  # noqa: E731
    story = [P(doc["title"], h1), P(f"{doc['subtitle']} - gerado em {doc['generated_at']}", small),
             Spacer(1, 4 * mm)]  # fmt: skip
    for sec in doc["sections"]:
        story.append(P(sec["heading"], h2))
        if sec["kpis"]:
            rows = [[P(k, cell), P(fmt_cell(v), ParagraphStyle("kv", parent=cell, fontName="Helvetica-Bold"))]
                    for k, v in sec["kpis"]]  # fmt: skip
            t = Table(rows, colWidths=[avail * 0.55, avail * 0.45])
            t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
                                   ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))  # fmt: skip
            story += [t, Spacer(1, 2 * mm)]
        for tb in sec["tables"]:
            story.append(P(tb["title"], h3))
            data = [[P(c, cellh) for c in tb["columns"]]] + [
                [P(fmt_cell(c), cell) for c in row] for row in tb["rows"]
            ]
            if not tb["rows"]:
                data.append([P("Sem registros", cell)] + [P("", cell)] * (len(tb["columns"]) - 1))
            ncol = len(tb["columns"])
            t = Table(data, colWidths=[avail / ncol] * ncol, repeatRows=1)
            style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.3, GRID),
                     ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA])]  # fmt: skip
            t.setStyle(TableStyle(style))
            story += [t, Spacer(1, 2 * mm)]
        for n in sec["notes"]:
            story.append(P("- " + n, small))
    story += [Spacer(1, 6 * mm), P(doc["disclaimer"], small)]

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#52607a"))
        canvas.drawString(16 * mm, 9 * mm, "FinanceMed AI - " + _plain(doc["title"]))
        canvas.drawRightString(page[0] - 16 * mm, 9 * mm, f"Página {d.page}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _plain(s: str) -> str:
    for a, b in PDF_SUBS.items():
        s = s.replace(a, b)
    return s
