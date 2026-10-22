"""Registrar o realizado e compará-lo com o planejado (PRD US-007, dashboard esperado x obtido)."""

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_FIN_WRITE, P_GOAL_READ, P_GOAL_WRITE
from app.db.models import ClinicService, Transaction, WorkSource
from app.db.session import get_db
from app.modules.finances.router import (
    EXPENSE_CATEGORIES,
    TransactionIn,
    _check_duplicate,
    _dedupe_hash,
    _materialized,
    _rec_q,
    _tx_out,
    _tx_q,
    parse_month,
)
from app.modules.goals.projection import _streams, month_numbers, month_str
from app.modules.schedule.router import _tz
from finance_engine import occurrences_in_month

router = APIRouter(prefix="/api/v1/realized", tags=["realized"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
CATEGORY_BY_WORK = {
    "SHIFT": "plantoes", "ON_CALL": "plantoes", "EMPLOYMENT": "plantoes",
    "TELEMEDICINE": "procedimentos", "PROCEDURE": "procedimentos", "OTHER": "outras_receitas",
}  # fmt: skip


class RealizedIn(BaseModel):
    source_type: str = Field(pattern="^(WORK|SERVICE)$")
    source_id: uuid.UUID
    month: str = Field(pattern=MONTH_RE)  # competência
    units: int = Field(ge=1, le=100_000)  # plantões feitos / pacientes atendidos
    amount_cents: int = Field(gt=0, le=10**12)  # o que foi recebido
    paid_at: date
    note: str | None = Field(default=None, max_length=120)
    allow_duplicate: bool = False


@router.post("", status_code=status.HTTP_201_CREATED)
def register_realized(
    body: RealizedIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Lança uma receita REALIZADA ligada a uma fonte. É a única forma de o planejado virar
    realizado: o plano nunca conta como dinheiro recebido."""
    ctx.require(P_GOAL_WRITE)
    ctx.require(P_FIN_WRITE)
    if body.source_type == "WORK":
        w = db.scalar(
            select(WorkSource).where(
                WorkSource.id == body.source_id,
                WorkSource.organization_id == ctx.organization_id,
                WorkSource.owner_user_id == ctx.user.id,
                WorkSource.deleted_at.is_(None),
            )
        )
        if w is None:
            raise HTTPException(422, "Trabalho inválido")
        name, category = w.name, CATEGORY_BY_WORK.get(w.work_type, "outras_receitas")
        link = {"work_source_id": w.id}
    else:
        s = db.scalar(
            select(ClinicService).where(
                ClinicService.id == body.source_id,
                ClinicService.organization_id == ctx.organization_id,
                ClinicService.deleted_at.is_(None),
            )
        )
        if s is None:
            raise HTTPException(422, "Serviço inválido")
        name, category = s.name, "consultas"
        link = {"clinic_service_id": s.id}
    desc = f"{name} · {body.note}" if body.note else name
    tx = TransactionIn(
        type="INCOME", status="REALIZED", competence_month=body.month, paid_at=body.paid_at,
        amount_cents=body.amount_cents, category=category, description=desc[:200],
        units=body.units, allow_duplicate=body.allow_duplicate, **link,
    )  # fmt: skip
    h = _dedupe_hash(tx)
    if not body.allow_duplicate:
        _check_duplicate(db, ctx, h)
    t = Transaction(
        organization_id=ctx.organization_id, created_by=ctx.user.id, dedupe_hash=h,
        competence_month=parse_month(body.month),
        **tx.model_dump(exclude={"allow_duplicate", "competence_month"}),
    )  # fmt: skip
    db.add(t)
    db.flush()
    audit(db, action="realized.register", object_type="transaction", object_id=t.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"source_type": body.source_type, "units": body.units})  # fmt: skip
    db.commit()
    return _tx_out(t)


def _signed_income(t: Transaction) -> int:
    return (-1 if t.is_reversal else 1) * t.amount_cents


def _pct_bps(num: int, den: int) -> int | None:
    return None if den <= 0 else (num * 10_000) // den


def _status(
    planned_units: int, realized_units: int, planned_gross: int, realized_gross: int
) -> str:
    if planned_gross == 0 and planned_units == 0:
        return "UNPLANNED" if (realized_gross or realized_units) else "NONE"
    if realized_gross == 0 and realized_units == 0:
        return "NOT_STARTED"
    if planned_units > 0 and realized_units > 0:
        a, b = realized_units, planned_units
    else:
        a, b = realized_gross, planned_gross
    return "BEHIND" if a < b else ("ON_PLAN" if a == b else "AHEAD")


@router.get("/compare")
def compare(
    month: str = Query(pattern=MONTH_RE),
    as_of: date | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Esperado x obtido por fonte e por categoria de despesa, com o ritmo do mês."""
    ctx.require(P_GOAL_READ)
    m = parse_month(month)
    tz = _tz(db, ctx)
    today = as_of or datetime.now(tz).date()
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    days = (nxt - m).days
    elapsed = max(0, min((today - m).days + 1, days))
    phase = "FUTURE" if today < m else ("CLOSED" if today >= nxt else "OPEN")

    inputs, meta = _streams(db, ctx, m)
    nums = month_numbers(db, ctx, m, "PERSONAL_NET")
    unit = {i.key: i for i in inputs}
    planned: dict[tuple[str, str], dict] = {}
    for r in nums["results"]:
        sid = nums["meta"][r.key]["source_id"]
        p = planned.setdefault((r.kind, sid), {"name": nums["meta"][r.key]["name"].replace(
            " (adicional do otimizador)", ""), "units": 0, "gross": 0, "net": 0, "extra_units": 0})  # fmt: skip
        p["units"] += r.units
        p["gross"] += r.gross_cents
        p["net"] += r.net_cents
        if r.key.endswith(":extra"):
            p["extra_units"] += r.units

    realized: dict[tuple[str, str], dict] = {}
    unlinked = 0
    for t in db.scalars(
        _tx_q(ctx).where(
            Transaction.type == "INCOME",
            Transaction.status == "REALIZED",
            Transaction.competence_month == m,
        )
    ):
        if t.work_source_id:
            key = ("WORK", str(t.work_source_id))
        elif t.clinic_service_id:
            key = ("SERVICE", str(t.clinic_service_id))
        else:
            unlinked += _signed_income(t)
            continue
        d = realized.setdefault(key, {"gross": 0, "units": 0})
        sign = -1 if t.is_reversal else 1
        d["gross"] += sign * t.amount_cents
        d["units"] += sign * (t.units or 0)

    names: dict[tuple[str, str], str] = {}
    for kind, sid in realized:
        if (kind, sid) in planned:
            continue
        row = db.get(WorkSource if kind == "WORK" else ClinicService, uuid.UUID(sid))
        names[(kind, sid)] = row.name if row is not None else "(fonte removida)"

    sources = []
    for key in sorted(set(planned) | set(realized), key=lambda k: (k[0], k[1])):
        p = planned.get(
            key, {"name": names.get(key, ""), "units": 0, "gross": 0, "net": 0, "extra_units": 0}
        )
        r = realized.get(key, {"gross": 0, "units": 0})
        inp = unit.get(f"{key[0]}:{key[1]}")
        if inp is not None:  # líquido estimado do realizado com a mesma retenção/custo do plano
            tax = (r["gross"] * inp.tax_rate_bps + 5_000) // 10_000
            net_est = r["gross"] - tax - inp.variable_unit_cents * max(r["units"], 0)
        else:
            net_est = None
        sources.append({
            "source_type": key[0], "source_id": key[1], "name": p["name"],
            "planned_units": p["units"], "extra_units": p["extra_units"],
            "realized_units": r["units"], "planned_gross_cents": p["gross"],
            "realized_gross_cents": r["gross"], "variance_gross_cents": r["gross"] - p["gross"],
            "variance_pct_bps": _pct_bps(r["gross"], p["gross"]),
            "planned_net_cents": p["net"], "realized_net_estimate_cents": net_est,
            "status": _status(p["units"], r["units"], p["gross"], r["gross"]),
        })  # fmt: skip

    # despesas: planejado (lançamentos planejados + recorrências ainda sem lançamento) x realizado
    exp: dict[str, dict] = {}
    for t in db.scalars(_tx_q(ctx).where(Transaction.type == "EXPENSE",
                                         Transaction.competence_month == m)):  # fmt: skip
        e = exp.setdefault(t.category, {"planned": 0, "realized": 0})
        sign = -1 if t.is_reversal else 1
        e["realized" if t.status == "REALIZED" else "planned"] += sign * t.amount_cents
    for rec in db.scalars(_rec_q(ctx)):
        if occurrences_in_month(
            rec.frequency, rec.start_date, rec.stop_date, m
        ) and not _materialized(db, ctx, rec.id, m):
            exp.setdefault(rec.category, {"planned": 0, "realized": 0})["planned"] += (
                rec.amount_cents
            )
    expenses = [
        {"category": c, "label": EXPENSE_CATEGORIES.get(c, c), "planned_cents": v["planned"],
         "realized_cents": v["realized"]}
        for c, v in sorted(exp.items())
    ]  # fmt: skip

    planned_gross = sum(s["planned_gross_cents"] for s in sources)
    linked_gross = sum(s["realized_gross_cents"] for s in sources)
    return {
        "month": month_str(m),
        "as_of": today.isoformat(),
        "phase": phase,
        "pace": {
            "days_in_month": days,
            "days_elapsed": elapsed,
            "elapsed_pct_bps": elapsed * 10_000 // days,
            "realized_vs_planned_gross_pct_bps": _pct_bps(linked_gross + unlinked, planned_gross),
        },  # fmt: skip
        "sources": sources,
        "totals": {
            "planned_gross_cents": planned_gross,
            "realized_gross_cents": linked_gross,
            "unlinked_income_cents": unlinked,
            "planned_expenses_cents": sum(e["planned_cents"] for e in expenses),
            "realized_expenses_cents": sum(e["realized_cents"] for e in expenses),
        },
        "expenses": expenses,
        "notes": [
            "Valores realizados são os recebidos (brutos); o líquido estimado usa a mesma "
            "retenção e os mesmos custos do plano. Tributos não são validados.",
            "Receitas sem vínculo a uma fonte (ex.: importadas) entram no total, mas não "
            "comparam com nenhum plano: vincule-as a um trabalho ou serviço.",
            "O plano nunca é somado ao realizado: só vira realizado quando você o registra.",
        ],
    }
