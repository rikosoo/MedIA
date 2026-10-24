"""Plano direto: o mínimo para o médico ver quanto ganha, quanto gasta e como bater a meta.

Somente leitura e sem IA: rendas e custos vêm do que o usuário cadastrou, e as ideias são contas
simples e auditáveis (quantas unidades a mais, quanto cortar). Planejado não é realizado.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_GOAL_READ
from app.db.models import MonthlyGoal
from app.db.session import get_db
from app.modules.finances.router import EXPENSE_CATEGORIES, _rec_q
from app.modules.goals.projection import breakdown, month_numbers, month_str
from finance_engine import format_brl, goal_gap_cents
from finance_engine.recurrence import occurrences_in_month

router = APIRouter(prefix="/api/v1/quick", tags=["quick"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
NOTES = [
    "Tudo é estimativa a partir do que você cadastrou; planejado não é dinheiro recebido.",
    "Tributos entram só se você informou a retenção ou habilitou as alíquotas padrão.",
    "As ideias não consideram sua agenda: confira no Otimizador se cabem nas suas horas.",
]


def _ceil(a: int, b: int) -> int:
    return -(-a // b)


def _ideas(gap: int, sources: list[dict], costs_total: int) -> list[dict]:
    """Ideias em ordem de simplicidade. Cada uma traz os números que a explicam."""
    ideas: list[dict] = []
    usable = [s for s in sources if s["net_per_unit_cents"] > 0]
    ranked = sorted(usable, key=lambda s: -(s["net_per_hour_cents"] or 0))
    for s in ranked[:2]:
        n = _ceil(gap, s["net_per_unit_cents"])
        minutes = n * s["minutes_per_unit"]
        ideas.append({
            "kind": "MORE_OF", "title": f"Mais {s['name']}", "source_id": s["id"], "units": n,
            "extra_minutes": minutes, "extra_net_cents": n * s["net_per_unit_cents"],
            "detail": f"{n} a mais por mês rendem {format_brl(n * s['net_per_unit_cents'])} líquidos",
        })  # fmt: skip
    if len(ranked) >= 2:
        a, b = ranked[0], ranked[1]
        half = _ceil(gap, 2)
        na, nb = _ceil(half, a["net_per_unit_cents"]), _ceil(half, b["net_per_unit_cents"])
        ideas.append({
            "kind": "MIX", "title": f"{a['name']} + {b['name']}", "units": [na, nb],
            "extra_minutes": na * a["minutes_per_unit"] + nb * b["minutes_per_unit"],
            "extra_net_cents": na * a["net_per_unit_cents"] + nb * b["net_per_unit_cents"],
            "detail": f"{na} de {a['name']} e {nb} de {b['name']}, dividindo a diferença",
        })  # fmt: skip
    if costs_total > 0:
        cut = min(gap, costs_total)
        ideas.append({
            "kind": "CUT_COSTS", "title": "Reduzir custos", "cut_cents": cut,
            "closes_gap": gap <= costs_total,
            "detail": f"Cortar {format_brl(cut)} dos {format_brl(costs_total)} de custos do mês"
            + ("" if gap <= costs_total else " ajuda, mas não fecha sozinho"),
        })  # fmt: skip
    for i in ideas:
        i.setdefault("closes_gap", True)  # as ideias de "mais renda" são calculadas para fechar
    return ideas


@router.get("/summary")
def summary(
    month: str = Query(pattern=MONTH_RE),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_GOAL_READ)
    m = date(int(month[:4]), int(month[5:]), 1)
    nums = month_numbers(db, ctx, m, "PERSONAL_NET")
    proj = nums["projection"]

    sources = []
    for r in breakdown(nums):
        if r["source_id"] is None or r.get("name", "").endswith("(adicional do otimizador)"):
            continue
        per_unit = r["net_cents"] // r["units"] if r["units"] else 0
        minutes = _ceil(r["committed_minutes"], r["units"]) if r["units"] else 0
        sources.append({
            "id": r["source_id"], "kind": r["kind"], "name": r["name"], "units": r["units"],
            "gross_cents": r["gross_cents"], "tax_cents": r["tax_cents"], "net_cents": r["net_cents"],
            "tax_rate_bps": r.get("tax_rate_bps", 0), "tax_source": r.get("tax_source", "NENHUMA"),
            "net_per_unit_cents": per_unit, "minutes_per_unit": minutes,
            "net_per_hour_cents": r["net_per_committed_hour_cents"],
        })  # fmt: skip

    recurring = []
    for rec in db.scalars(_rec_q(ctx)):
        if rec.frequency == "MONTHLY" and occurrences_in_month(
            rec.frequency, rec.start_date, rec.stop_date, m
        ):
            recurring.append({"id": str(rec.id), "category": rec.category,
                              "label": EXPENSE_CATEGORIES.get(rec.category, rec.category),
                              "amount_cents": rec.amount_cents})  # fmt: skip
    total_costs = nums["expenses"]
    recurring_sum = sum(c["amount_cents"] for c in recurring)

    goal = db.scalar(select(MonthlyGoal).where(
        MonthlyGoal.organization_id == ctx.organization_id, MonthlyGoal.user_id == ctx.user.id,
        MonthlyGoal.month == m, MonthlyGoal.metric_type == "PERSONAL_NET",
        MonthlyGoal.deleted_at.is_(None)))  # fmt: skip
    result = proj.value_cents
    gap = goal_gap_cents(goal.target_cents, result) if goal else None
    if goal is None:
        status = "NO_GOAL"
    elif not sources:
        status = "NO_SOURCES"
    else:
        status = "MET" if gap == 0 else "SHORT"
    return {
        "month": month_str(m),
        "goal": {"id": str(goal.id), "target_cents": goal.target_cents} if goal else None,
        "sources": sources,
        "income_net_cents": proj.income_net_cents,
        "costs": {"total_cents": total_costs, "recurring": recurring,
                  "other_cents": max(total_costs - recurring_sum, 0)},  # fmt: skip
        "result_cents": result, "gap_cents": gap, "status": status,
        "ideas": _ideas(gap, sources, total_costs) if status == "SHORT" else [],
        "next_step": {"NO_GOAL": "GOAL", "NO_SOURCES": "SOURCES"}.get(status),
        "tax_is_estimate": True, "notes": NOTES,
    }  # fmt: skip
