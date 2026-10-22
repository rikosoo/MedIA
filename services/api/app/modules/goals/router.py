import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_GOAL_READ, P_GOAL_WRITE, P_SCHED_READ
from app.db.base import utcnow
from app.db.models import ClinicService, IncomePlan, MonthlyGoal, WorkSource
from app.db.session import get_db
from app.modules.billing.entitlements import check_goal_limit as check_limit_for_month
from app.modules.goals.projection import (
    breakdown,
    build_gap,
    effective_plans,
    get_goal,
    goal_out,
    month_numbers,
    month_str,
    quality_and_alerts,
    shift_month,
)
from app.modules.schedule.router import overview as schedule_overview
from finance_engine import format_brl

router = APIRouter(prefix="/api/v1", tags=["goals"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
METRIC_RE = "^(PERSONAL_NET|CLINIC_PROFIT|GROSS_REVENUE)$"


def _month(value: str) -> date:
    y, m = value.split("-")
    return date(int(y), int(m), 1)


# ---------- metas ----------


class GoalIn(BaseModel):
    month: str = Field(pattern=MONTH_RE)
    metric_type: str = Field(default="PERSONAL_NET", pattern=METRIC_RE)
    target_cents: int = Field(ge=0, le=10**12)
    priority: str = Field(default="BALANCED", pattern="^(LESS_HOURS|MORE_INCOME|BALANCED)$")
    use_reserves: bool = False
    reserve_cents: int = Field(default=0, ge=0, le=10**12)
    notes: str | None = Field(default=None, max_length=500)


class GoalPatch(BaseModel):
    model_config = {"extra": "forbid"}
    target_cents: int | None = Field(default=None, ge=0, le=10**12)
    priority: str | None = Field(default=None, pattern="^(LESS_HOURS|MORE_INCOME|BALANCED)$")
    use_reserves: bool | None = None
    reserve_cents: int | None = Field(default=None, ge=0, le=10**12)
    notes: str | None = Field(default=None, max_length=500)


class DuplicateIn(BaseModel):
    month: str = Field(pattern=MONTH_RE)


def _goal_q(ctx: TenantContext):
    # Metas são pessoais: nem o dono da organização enxerga as de outro usuário.
    return select(MonthlyGoal).where(
        MonthlyGoal.organization_id == ctx.organization_id,
        MonthlyGoal.user_id == ctx.user.id,
        MonthlyGoal.deleted_at.is_(None),
    )


def _own_goal(db: Session, ctx: TenantContext, gid: uuid.UUID) -> MonthlyGoal:
    g = db.scalar(_goal_q(ctx).where(MonthlyGoal.id == gid))
    if g is None:
        raise HTTPException(404, "Meta não encontrada")
    return g


def _upsert_goal(
    db: Session, ctx: TenantContext, month: date, metric: str, **fields
) -> MonthlyGoal:
    """Cria a meta; se havia uma excluída (exclusão lógica) para o mesmo mês/tipo, a reaproveita."""
    old = db.scalar(
        select(MonthlyGoal).where(
            MonthlyGoal.organization_id == ctx.organization_id,
            MonthlyGoal.user_id == ctx.user.id,
            MonthlyGoal.month == month,
            MonthlyGoal.metric_type == metric,
            MonthlyGoal.deleted_at.is_not(None),
        )
    )
    if old is not None:
        old.deleted_at = None
        for k, v in fields.items():
            setattr(old, k, v)
        return old
    g = MonthlyGoal(
        organization_id=ctx.organization_id, user_id=ctx.user.id, month=month,
        metric_type=metric, **fields,
    )  # fmt: skip
    db.add(g)
    return g


def _exists(db: Session, ctx: TenantContext, month: date, metric: str) -> bool:
    return (
        db.scalar(_goal_q(ctx).where(MonthlyGoal.month == month, MonthlyGoal.metric_type == metric))
        is not None
    )


@router.get("/goals")
def list_goals(
    month: str | None = Query(default=None, pattern=MONTH_RE),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_GOAL_READ)
    q = _goal_q(ctx).order_by(MonthlyGoal.month.desc(), MonthlyGoal.metric_type)
    if month:
        q = q.where(MonthlyGoal.month == _month(month))
    return [goal_out(g) for g in db.scalars(q)]


@router.post("/goals", status_code=status.HTTP_201_CREATED)
def create_goal(
    body: GoalIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_WRITE)
    m = _month(body.month)
    if not _exists(db, ctx, m, body.metric_type):
        check_limit_for_month(db, ctx, m)
    if _exists(db, ctx, m, body.metric_type):
        raise HTTPException(409, "Já existe uma meta deste tipo para o mês. Edite a existente.")
    g = _upsert_goal(
        db, ctx, m, body.metric_type, **body.model_dump(exclude={"month", "metric_type"})
    )
    db.flush()
    audit(db, action="goal.create", object_type="monthly_goal", object_id=g.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return goal_out(g)


@router.patch("/goals/{goal_id}")
def update_goal(
    goal_id: uuid.UUID,
    body: GoalPatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_GOAL_WRITE)
    g = _own_goal(db, ctx, goal_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(g, k, v)
    audit(db, action="goal.update", object_type="monthly_goal", object_id=g.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return goal_out(g)


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(
    goal_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_WRITE)
    g = _own_goal(db, ctx, goal_id)
    g.deleted_at = utcnow()
    audit(db, action="goal.delete", object_type="monthly_goal", object_id=g.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/goals/{goal_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_goal(
    goal_id: uuid.UUID,
    body: DuplicateIn,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_GOAL_WRITE)
    src = _own_goal(db, ctx, goal_id)
    m = _month(body.month)
    if _exists(db, ctx, m, src.metric_type):
        raise HTTPException(409, "Já existe uma meta deste tipo para o mês de destino.")
    check_limit_for_month(db, ctx, m)
    g = _upsert_goal(
        db, ctx, m, src.metric_type, target_cents=src.target_cents, priority=src.priority,
        use_reserves=src.use_reserves, reserve_cents=src.reserve_cents, notes=src.notes,
    )  # fmt: skip
    db.flush()
    audit(db, action="goal.duplicate", object_type="monthly_goal", object_id=g.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return goal_out(g)


@router.get("/goals/{goal_id}/gap")
def goal_gap(
    goal_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Quanto falta para a meta, com o que foi usado na conta (`Calcular diferença`)."""
    ctx.require(P_GOAL_READ)
    g = _own_goal(db, ctx, goal_id)
    nums = month_numbers(db, ctx, g.month, g.metric_type)
    return {**build_gap(db, ctx, g, nums), "breakdown": breakdown(nums)}


# ---------- planos de renda (composição do mês) ----------


class PlanIn(BaseModel):
    month: str | None = Field(default=None, pattern=MONTH_RE)  # nulo = padrão de todo mês
    source_type: str = Field(pattern="^(WORK|SERVICE)$")
    source_id: uuid.UUID
    units: int = Field(ge=0, le=100_000)
    certainty: str = Field(default="PROBABLE", pattern="^(GUARANTEED|PROBABLE)$")
    tax_rate_bps: int | None = Field(default=None, ge=0, le=10_000)


def _plan_out(p: IncomePlan, name: str) -> dict:
    return {
        "id": str(p.id),
        "month": month_str(p.month) if p.month else None,
        "inherited": p.month is None,
        "source_type": p.source_type,
        "source_id": str(p.source_id),
        "name": name,
        "units": p.units,
        "certainty": p.certainty,
        "tax_rate_bps": p.tax_rate_bps,
        "extra_units": p.extra_units,
    }


def _check_source(db: Session, ctx: TenantContext, body: PlanIn) -> str:
    """Trabalho: precisa ser do usuário e não pode ser do tipo Consultório (renda dupla)."""
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
        if w.work_type == "CLINIC":
            raise HTTPException(
                422,
                "A renda do consultório é projetada por serviços; use um serviço para não "
                "contar a mesma renda duas vezes.",
            )
        return w.name
    s = db.scalar(
        select(ClinicService).where(
            ClinicService.id == body.source_id,
            ClinicService.organization_id == ctx.organization_id,
            ClinicService.deleted_at.is_(None),
        )
    )
    if s is None:
        raise HTTPException(422, "Serviço inválido")
    return s.name


@router.get("/income-plans")
def list_plans(
    month: str = Query(pattern=MONTH_RE),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Fontes disponíveis para o mês com o plano efetivo (do mês ou herdado do padrão)."""
    ctx.require(P_GOAL_READ)
    m = _month(month)
    plans = effective_plans(db, ctx, m)
    out = []
    for w in db.scalars(
        select(WorkSource).where(
            WorkSource.organization_id == ctx.organization_id,
            WorkSource.owner_user_id == ctx.user.id,
            WorkSource.deleted_at.is_(None),
            WorkSource.status == "active",
        )
    ):
        p = plans.get(("WORK", w.id))
        out.append({
            "source_type": "WORK", "source_id": str(w.id), "name": w.name,
            "eligible": w.work_type != "CLINIC",
            "plan": _plan_out(p, w.name) if p else None,
        })  # fmt: skip
    for s in db.scalars(
        select(ClinicService).where(
            ClinicService.organization_id == ctx.organization_id,
            ClinicService.deleted_at.is_(None),
            ClinicService.status == "active",
        )
    ):
        p = plans.get(("SERVICE", s.id))
        out.append({
            "source_type": "SERVICE", "source_id": str(s.id), "name": s.name, "eligible": True,
            "plan": _plan_out(p, s.name) if p else None,
        })  # fmt: skip
    return out


@router.put("/income-plans")
def put_plan(body: PlanIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_GOAL_WRITE)
    name = _check_source(db, ctx, body)
    m = _month(body.month) if body.month else None
    p = db.scalar(
        select(IncomePlan).where(
            IncomePlan.organization_id == ctx.organization_id,
            IncomePlan.source_type == body.source_type,
            IncomePlan.source_id == body.source_id,
            IncomePlan.month.is_(None) if m is None else IncomePlan.month == m,
        )
    )
    if p is None:
        p = IncomePlan(
            organization_id=ctx.organization_id, created_by=ctx.user.id, month=m,
            source_type=body.source_type, source_id=body.source_id,
            units=body.units, certainty=body.certainty,
        )  # fmt: skip
        db.add(p)
    p.units, p.certainty, p.tax_rate_bps = body.units, body.certainty, body.tax_rate_bps
    db.flush()
    audit(db, action="goal.plan_put", object_type="income_plan", object_id=p.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return _plan_out(p, name)


@router.delete("/income-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(
    plan_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_WRITE)
    p = db.scalar(
        select(IncomePlan).where(
            IncomePlan.id == plan_id, IncomePlan.organization_id == ctx.organization_id
        )
    )
    if p is None:
        raise HTTPException(404, "Plano não encontrado")
    if p.source_type == "WORK":  # plano de trabalho só pelo dono do trabalho
        w = db.get(WorkSource, p.source_id)
        if w is None or w.owner_user_id != ctx.user.id:
            raise HTTPException(404, "Plano não encontrado")
    db.delete(p)
    audit(db, action="goal.plan_delete", object_type="income_plan", object_id=p.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- dashboard ----------

ASSUMPTIONS = [
    "Projetado = planos de renda do mês; realizado = lançamentos com data de caixa. Um nunca "
    "é somado ao outro.",
    "Tributos e retenções são estimativas informadas por você, não validadas por contador.",
    "Despesas lançadas (realizadas e planejadas) são abatidas uma única vez; custos variáveis "
    "por unidade já estão no líquido de cada fonte. Não lance o mesmo custo nos dois lugares.",
    "Serviços do consultório incluem retornos esperados no mesmo mês; retornos que caem em "
    "meses seguintes não são redistribuídos.",
    "Metas e rendas são pessoais; despesas e serviços são da organização inteira.",
]


@router.get("/dashboard")
def dashboard(
    month: str = Query(pattern=MONTH_RE),
    goal_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_GOAL_READ)
    m = _month(month)
    g = get_goal(db, ctx, m, goal_id)
    metric = g.metric_type if g else "PERSONAL_NET"
    m = g.month if (g and goal_id) else m
    nums = month_numbers(db, ctx, m, metric)
    gap = build_gap(db, ctx, g, nums) if g else None
    quality, reasons, alerts = quality_and_alerts(nums, gap, metric)

    proj = nums["projection"]
    hours = None
    if ctx.can(P_SCHED_READ):
        ov = schedule_overview(month=month_str(m), user_id=None, ctx=ctx, db=db)
        hours = {
            "active_minutes": ov["active_minutes"],
            "committed_minutes": ov["committed_minutes"],
            "planned_active_minutes": sum(r.active_minutes for r in nums["results"]),
            "planned_committed_minutes": sum(r.committed_minutes for r in nums["results"]),
            "schedule_feasible": ov["feasible"],
        }

    series = []
    for i in range(-11, 1):  # evolução: 12 meses terminando no mês consultado
        mm = shift_month(m, i)
        n = month_numbers(db, ctx, mm, metric)
        gm = get_goal(db, ctx, mm)
        series.append({
            "month": month_str(mm),
            "projected_cents": n["projection"].value_cents,
            "realized_cents": n["realized_value"],
            "target_cents": gm.target_cents if (gm and gm.metric_type == metric) else None,
        })  # fmt: skip

    fin = nums["finance"]
    return {
        "month": month_str(m),
        "metric_type": metric,
        "goal": gap,
        "data_quality": quality,
        "data_quality_reasons": reasons,
        "projection": {
            "gross_cents": proj.gross_cents,
            "income_net_cents": proj.income_net_cents,
            "expenses_cents": proj.expenses_cents,
            "value_cents": proj.value_cents,
            "assured_cents": proj.assured_cents,
            "probable_cents": proj.probable_cents,
        },
        "realized": {
            "income_cents": nums["realized"]["income"],
            "expense_cents": nums["realized"]["expense"],
            "value_cents": nums["realized_value"],
        },
        "expenses_detail": {
            "realized_cents": fin["expense_realized_cents"],
            "planned_cents": fin["expense_planned_cents"],
            "by_category_cents": fin["expense_by_category_cents"],
        },
        "breakdown": breakdown(nums),
        "hours": hours,
        "series": series,
        "alerts": alerts,
        "assumptions": ASSUMPTIONS,
        "next_actions": [
            {"label": "Atualizar o realizado", "route": "/app/finances"},
            {"label": "Ajustar a composição do mês", "route": "/app/goals"},
            {"label": "Revisar a agenda", "route": "/app/schedule"},
        ],
        "formatted": {"projected": format_brl(proj.value_cents)},
    }
