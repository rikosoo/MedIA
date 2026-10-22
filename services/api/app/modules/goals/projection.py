"""Projeção do mês: junta planos de renda, despesas e agenda. Toda conta fica em finance_engine."""

import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.rbac import P_SCHED_READ
from app.db.models import ClinicService, IncomePlan, MonthlyGoal, Transaction, WorkSource
from app.modules.clinic.router import _economics, _policy_of
from app.modules.finances.router import summary as finance_summary
from app.modules.schedule.router import overview as schedule_overview
from finance_engine import (
    StreamInput,
    compute_stream,
    effective_target_cents,
    format_brl,
    goal_gap_cents,
    goal_status,
    project,
)

CLINIC_INCOME = ("consultas", "procedimentos")


def month_str(d: date) -> str:
    return d.strftime("%Y-%m")


def shift_month(m: date, delta: int) -> date:
    idx = m.year * 12 + (m.month - 1) + delta
    return date(idx // 12, idx % 12 + 1, 1)


def effective_plans(
    db: Session, ctx: TenantContext, month: date
) -> dict[tuple[str, uuid.UUID], IncomePlan]:
    """Plano do mês sobrescreve o padrão (`month` nulo)."""
    rows = db.scalars(
        select(IncomePlan).where(
            IncomePlan.organization_id == ctx.organization_id,
            (IncomePlan.month == month) | (IncomePlan.month.is_(None)),
        )
    )
    out: dict[tuple[str, uuid.UUID], IncomePlan] = {}
    for p in sorted(rows, key=lambda r: r.month is not None):  # padrão primeiro, mês depois
        out[(p.source_type, p.source_id)] = p
    return out


def _streams(db: Session, ctx: TenantContext, month: date):
    """(entradas, metadados). Trabalhos: só os do usuário. Serviços: os da organização."""
    plans = effective_plans(db, ctx, month)
    inputs: list[StreamInput] = []
    meta: dict[str, dict] = {}

    works = db.scalars(
        select(WorkSource).where(
            WorkSource.organization_id == ctx.organization_id,
            WorkSource.owner_user_id == ctx.user.id,
            WorkSource.deleted_at.is_(None),
            WorkSource.status == "active",
        )
    )
    for w in works:
        plan = plans.get(("WORK", w.id))
        if plan is None or w.work_type == "CLINIC":
            continue
        r = w.income_rule
        key = f"WORK:{w.id}"
        inputs.append(
            StreamInput(
                key, "WORK", plan.units, plan.certainty, r.gross_cents, r.variable_cost_cents,
                plan.tax_rate_bps if plan.tax_rate_bps is not None else r.tax_rate_bps,
                r.duration_minutes * 10, (r.commitment_minutes or r.duration_minutes) * 10,
            )
        )  # fmt: skip
        meta[key] = {"name": w.name, "source_id": str(w.id), "inherited": plan.month is None}

    services = db.scalars(
        select(ClinicService).where(
            ClinicService.organization_id == ctx.organization_id,
            ClinicService.deleted_at.is_(None),
            ClinicService.status == "active",
        )
    )
    for s in services:
        plan = plans.get(("SERVICE", s.id))
        if plan is None:
            continue
        econ, _ = _economics(s, _policy_of(db, ctx, s), s.price_cents)
        key = f"SERVICE:{s.id}"
        inputs.append(
            StreamInput(
                key, "SERVICE", plan.units, plan.certainty, econ.gross_revenue_cents,
                econ.fee_cents + econ.variable_cost_cents, plan.tax_rate_bps or 0,
                econ.minutes_x10, econ.minutes_x10,
            )
        )  # fmt: skip
        meta[key] = {"name": s.name, "source_id": str(s.id), "inherited": plan.month is None}
    return inputs, meta


def _realized(db: Session, ctx: TenantContext, month: date) -> dict:
    rows = db.scalars(
        select(Transaction).where(
            Transaction.organization_id == ctx.organization_id,
            Transaction.deleted_at.is_(None),
            Transaction.status == "REALIZED",
            Transaction.competence_month == month,
        )
    )
    income = clinic_income = expense = 0
    for t in rows:
        sign = -1 if t.is_reversal else 1
        if t.type == "INCOME":
            income += sign * t.amount_cents
            if t.category in CLINIC_INCOME:
                clinic_income += sign * t.amount_cents
        else:
            expense += sign * t.amount_cents
    return {"income": income, "clinic_income": clinic_income, "expense": expense}


def realized_value(real: dict, metric: str) -> int:
    if metric == "GROSS_REVENUE":
        return real["income"]
    if metric == "CLINIC_PROFIT":
        return real["clinic_income"] - real["expense"]
    return real["income"] - real["expense"]


def month_numbers(db: Session, ctx: TenantContext, month: date, metric: str) -> dict:
    inputs, meta = _streams(db, ctx, month)
    results = [compute_stream(i) for i in inputs]
    fin = finance_summary(month=month_str(month), ctx=ctx, db=db)["competence"]
    expenses = fin["expense_realized_cents"] + fin["expense_planned_cents"]
    proj = project(results, max(expenses, 0), metric)
    real = _realized(db, ctx, month)
    return {
        "results": results, "meta": meta, "projection": proj, "finance": fin, "realized": real,
        "realized_value": realized_value(real, metric), "expenses": expenses,
    }  # fmt: skip


def get_goal(
    db: Session, ctx: TenantContext, month: date, goal_id: uuid.UUID | None = None
) -> MonthlyGoal | None:
    q = select(MonthlyGoal).where(
        MonthlyGoal.organization_id == ctx.organization_id,
        MonthlyGoal.user_id == ctx.user.id,
        MonthlyGoal.deleted_at.is_(None),
    )
    if goal_id is not None:
        g = db.scalar(q.where(MonthlyGoal.id == goal_id))
        if g is None:
            raise HTTPException(404, "Meta não encontrada")
        return g
    goals = list(db.scalars(q.where(MonthlyGoal.month == month)))
    goals.sort(
        key=lambda g: ("PERSONAL_NET", "CLINIC_PROFIT", "GROSS_REVENUE").index(g.metric_type)
    )
    return goals[0] if goals else None


def goal_out(g: MonthlyGoal) -> dict:
    return {
        "id": str(g.id),
        "month": month_str(g.month),
        "metric_type": g.metric_type,
        "target_cents": g.target_cents,
        "priority": g.priority,
        "use_reserves": g.use_reserves,
        "reserve_cents": g.reserve_cents,
        "notes": g.notes,
    }


def build_gap(db: Session, ctx: TenantContext, g: MonthlyGoal, nums: dict | None = None) -> dict:
    month = g.month
    nums = nums or month_numbers(db, ctx, month, g.metric_type)
    proj = nums["projection"]
    eff = effective_target_cents(g.target_cents, g.reserve_cents, g.use_reserves)
    feasible = None
    if ctx.can(P_SCHED_READ):
        feasible = schedule_overview(month=month_str(month), user_id=None, ctx=ctx, db=db)[
            "feasible"
        ]
    status = goal_status(eff, proj.value_cents, feasible)
    return {
        "goal": goal_out(g),
        "effective_target_cents": eff,
        "projected_cents": proj.value_cents,
        "assured_cents": proj.assured_cents,
        "probable_cents": proj.probable_cents,
        "realized_cents": nums["realized_value"],
        "gap_cents": goal_gap_cents(eff, proj.value_cents),
        "assured_gap_cents": goal_gap_cents(eff, proj.assured_cents),
        "realized_gap_cents": goal_gap_cents(eff, nums["realized_value"]),
        "status": status,
        "schedule_feasible": feasible,
    }


def breakdown(nums: dict) -> list[dict]:
    out = []
    for r in nums["results"]:
        m = nums["meta"][r.key]
        out.append({
            **m, "kind": r.kind, "certainty": r.certainty, "units": r.units,
            "gross_cents": r.gross_cents, "variable_cost_cents": r.variable_cost_cents,
            "tax_cents": r.tax_cents, "net_cents": r.net_cents,
            "active_minutes": r.active_minutes, "committed_minutes": r.committed_minutes,
            "net_per_committed_hour_cents": r.net_per_committed_hour_cents,
        })  # fmt: skip
    return out


def quality_and_alerts(
    nums: dict, gap: dict | None, metric: str
) -> tuple[str, list[str], list[dict]]:
    reasons: list[str] = []
    if not nums["results"]:
        reasons.append("Nenhum plano de renda para o mês: a projeção considera só as despesas.")
    if (
        nums["results"]
        and all(r.tax_cents == 0 for r in nums["results"])
        and metric != "GROSS_REVENUE"
    ):
        reasons.append("Nenhuma retenção/tributo informado: o líquido pode estar superestimado.")
    if nums["realized"]["income"] > 0 and not nums["results"]:
        reasons.append("Há receita lançada, mas nenhum plano para compará-la.")
    quality = "insufficient" if not nums["results"] else ("partial" if reasons else "complete")

    alerts: list[dict] = []

    def add(severity: str, code: str, message: str) -> None:
        alerts.append({"severity": severity, "code": code, "message": message})

    if gap:
        if gap["status"] == "SHORT":
            add(
                "warning",
                "GAP",
                f"Faltam {format_brl(gap['gap_cents'])} para a meta "
                f"(só com o garantido faltariam {format_brl(gap['assured_gap_cents'])}).",
            )
        else:
            add(
                "info",
                "MET",
                "Meta atingida pela projeção: priorize estabilidade e reserva em vez de mais "
                "trabalho.",
            )
        if gap["status"] == "MET_WITH_ISSUES":
            add(
                "warning",
                "SCHEDULE",
                "A meta é atingida na projeção, mas a agenda tem conflitos, descanso "
                "insuficiente ou limites estourados.",
            )
    value = nums["projection"].value_cents
    if value < 0:
        add("warning", "DEFICIT", f"Resultado projetado negativo: {format_brl(value)}.")
    return quality, reasons, alerts
