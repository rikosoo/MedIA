import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_FIN_READ, P_GOAL_READ, P_GOAL_WRITE
from app.db.base import utcnow
from app.db.models import Forecast, Holiday, Leave, WorkSource
from app.db.session import get_db
from app.modules.acquisition.router import secretary_coverage_bps
from app.modules.finances.router import summary as finance_summary
from app.modules.goals.projection import (
    _realized,
    _streams,
    get_goal,
    month_numbers,
    month_str,
    shift_month,
)
from app.modules.schedule.router import _tz
from finance_engine import compute_stream
from finance_engine.forecast import (
    attainment_ratio,
    expand_leave_days,
    inflate,
    reserve_needed,
    scenario_attainment,
    seasonal_indices,
    working_days_factor,
)

router = APIRouter(prefix="/api/v1/forecasts", tags=["forecasts"])

VERSION = "1.0"
MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
HISTORY_WINDOW = 12  # meses completos olhados para trás
RATIO_MONTHS = 6  # no máximo, os últimos 6 meses com dados entram no aproveitamento
SEASONAL_MIN, SEASONAL_MAX = 5_000, 20_000


class Manual(BaseModel):
    conservative_bps: int = Field(default=8_000, ge=0, le=12_000)
    base_bps: int = Field(default=10_000, ge=0, le=12_000)
    optimistic_bps: int = Field(default=10_000, ge=0, le=12_000)


class Adjustment(BaseModel):
    month: str = Field(pattern=MONTH_RE)
    label: str = Field(min_length=1, max_length=80)
    income_delta_cents: int = Field(default=0, ge=-(10**11), le=10**11)
    expense_cents: int = Field(default=0, ge=0, le=10**11)


class ForecastIn(BaseModel):
    horizon_months: int = Field(default=6)
    from_month: str | None = Field(default=None, pattern=MONTH_RE)
    as_of: date | None = None
    floor_cents: int | None = Field(default=None, ge=0, le=10**11)
    expense_inflation_bps: int = Field(default=0, ge=-500, le=500)
    manual: Manual = Field(default_factory=Manual)
    use_seasonality: bool = False
    adjustments: list[Adjustment] = Field(default_factory=list, max_length=24)
    name: str | None = Field(default=None, max_length=120)
    save: bool = False

    @model_validator(mode="after")
    def _ok(self):
        if self.horizon_months not in (3, 6, 12):
            raise ValueError("horizonte deve ser 3, 6 ou 12 meses")
        m = self.manual
        if not (m.conservative_bps <= m.base_bps <= m.optimistic_bps):
            raise ValueError("premissas manuais devem ter conservador <= base <= otimista")
        return self


def _first(d: date) -> date:
    return d.replace(day=1)


def _history(
    db: Session, ctx: TenantContext, current: date
) -> tuple[list[dict], dict[tuple[int, int], int]]:
    """Meses completos anteriores com algum realizado: aproveitamento e receita por mês."""
    rows, monthly = [], {}
    for i in range(1, HISTORY_WINDOW + 1):
        m = shift_month(current, -i)
        real = _realized(db, ctx, m)
        if real["income"] <= 0 and real["expense"] <= 0:
            continue  # mês sem nenhum lançamento: provavelmente antes de usar o app
        planned = sum(r.gross_cents for r in month_numbers(db, ctx, m, "PERSONAL_NET")["results"])
        monthly[(m.year, m.month)] = real["income"]
        ratio = attainment_ratio(planned, real["income"])
        if ratio is not None:
            rows.append({"month": month_str(m), "planned_gross_cents": planned,
                         "realized_income_cents": real["income"], "attainment_bps": ratio})  # fmt: skip
    return rows, monthly


def _expense_baseline(db: Session, ctx: TenantContext, current: date) -> int | None:
    vals = [_realized(db, ctx, shift_month(current, -i))["expense"] for i in (1, 2, 3)]
    vals = [v for v in vals if v > 0]
    return sum(vals) // len(vals) if vals else None


def run_forecast(db: Session, ctx: TenantContext, body: ForecastIn, now: datetime) -> dict:
    tz = _tz(db, ctx)
    today = body.as_of or now.astimezone(tz).date()
    current = _first(today)
    start = _first(date(int(body.from_month[:4]), int(body.from_month[5:7]), 1)) if body.from_month \
        else shift_month(current, 1)  # fmt: skip
    if start < current:
        raise HTTPException(422, "A previsão começa no mês atual ou depois")

    hist, monthly = _history(db, ctx, current)
    recent = sorted(hist, key=lambda r: r["month"])[-RATIO_MONTHS:]
    att = scenario_attainment([r["attainment_bps"] for r in recent],
                              (body.manual.conservative_bps, body.manual.base_bps, body.manual.optimistic_bps))  # fmt: skip
    idx = seasonal_indices(monthly) if body.use_seasonality else None
    base_exp = _expense_baseline(db, ctx, current) if ctx.can(P_FIN_READ) else None

    unit_types = {
        f"WORK:{w.id}": w.income_rule.unit_type
        for w in db.scalars(
            select(WorkSource).where(
                WorkSource.organization_id == ctx.organization_id,
                WorkSource.owner_user_id == ctx.user.id,
                WorkSource.deleted_at.is_(None),
            )
        )  # fmt: skip
    }
    adj_by_month: dict[str, list[Adjustment]] = {}
    for a in body.adjustments:
        adj_by_month.setdefault(a.month, []).append(a)

    months, nets = [], {"conservative": [], "base": [], "optimistic": []}
    for k in range(body.horizon_months):
        f = shift_month(start, k)
        nxt = shift_month(f, 1)
        last = date.fromordinal(nxt.toordinal() - 1)
        holidays = {h.date: h.name for h in db.scalars(select(Holiday).where(
            Holiday.organization_id == ctx.organization_id, Holiday.confirmed.is_(True),
            Holiday.date >= f, Holiday.date <= last))}  # fmt: skip
        leave_days: set[date] = set()
        for lv in db.scalars(select(Leave).where(
            Leave.organization_id == ctx.organization_id, Leave.staff_role == "PHYSICIAN",
            Leave.user_id == ctx.user.id, Leave.deleted_at.is_(None), Leave.start_date <= last, Leave.end_date >= f)):  # fmt: skip
            leave_days |= expand_leave_days(lv.start_date, lv.end_date, f, last)
        factor, wdays, lost = working_days_factor(f.year, f.month, set(holidays), leave_days)

        inputs, _meta = _streams(db, ctx, f)
        plan_net = plan_gross = 0
        for i in inputs:
            r = compute_stream(i)
            fixed = i.kind == "WORK" and unit_types.get(i.key.split(":extra")[0]) == "MONTHLY"
            net, gross = r.net_cents, r.gross_cents
            if not fixed:
                net, gross = net * factor // 10_000, gross * factor // 10_000
                if idx and i.kind == "SERVICE":
                    s = min(max(idx.get(f.month, 10_000), SEASONAL_MIN), SEASONAL_MAX)
                    net, gross = net * s // 10_000, gross * s // 10_000
            plan_net += net
            plan_gross += gross

        known = 0
        if ctx.can(P_FIN_READ):
            known = finance_summary(month=month_str(f), ctx=ctx, db=db)["competence"][
                "expense_planned_cents"
            ]
        inflated = inflate(base_exp, body.expense_inflation_bps, (f.year - current.year) * 12 + f.month - current.month) \
            if base_exp is not None else 0  # fmt: skip
        expenses = max(known, inflated)
        adj = adj_by_month.get(month_str(f), [])
        extra_income = sum(a.income_delta_cents for a in adj)
        expenses += sum(a.expense_cents for a in adj)

        incomes = {"conservative": plan_net * att.conservative_bps // 10_000 + extra_income,
                   "base": plan_net * att.base_bps // 10_000 + extra_income,
                   "optimistic": plan_net * att.optimistic_bps // 10_000 + extra_income}  # fmt: skip
        net = {k_: v - expenses for k_, v in incomes.items()}
        for k_ in nets:
            nets[k_].append(net[k_])
        goal = get_goal(db, ctx, f)
        drivers = []
        if lost:
            names = sorted({h for h in holidays.values()})
            drivers.append(f"{lost} dia(s) útil(eis) a menos de {wdays}"
                           + (f" (feriados: {', '.join(names)})" if names else "")
                           + (" e férias/licença" if leave_days else "") + ".")  # fmt: skip
        cov, _ = secretary_coverage_bps(db, ctx, f)
        if cov < 10_000:
            drivers.append(
                f"Férias da secretária: cobertura de {round(cov / 100)}% dos contatos (pode reduzir a captação)."
            )
        if adj:
            drivers += [f"Ajuste manual: {a.label}." for a in adj]
        flags = [] if inputs else ["SEM_PLANO"]
        months.append({
            "month": month_str(f), "working_days": wdays, "working_days_lost": lost,
            "working_days_factor_bps": factor, "plan_income_cents": plan_net, "plan_gross_cents": plan_gross,
            "income_cents": incomes, "expenses_cents": expenses, "net_cents": net,
            "goal_target_cents": goal.target_cents if goal else None,
            "secretary_coverage_bps": cov, "drivers": drivers, "flags": flags,
        })  # fmt: skip

    reserve = None
    if body.floor_cents is not None:
        reserve = {}
        for k_, vals in nets.items():
            r, below = reserve_needed(vals, body.floor_cents)
            reserve[k_] = {"reserve_cents": r, "months_below_floor": below}
    notes = [
        "Previsão = plano de renda x aproveitamento do cenário - despesas. É uma hipótese com faixa, "
        "não uma promessa de resultado.",
        "O plano de meses sem plano específico herda o padrão mensal; meses sem plano aparecem sinalizados.",
        "O aproveitamento histórico compara o realizado com os planos atuais (o padrão mensal vale para "
        "todos os meses) e só conta meses completos que tiveram lançamentos.",
        "Dias úteis perdidos (feriados confirmados e férias do médico) reduzem as fontes por evento ou "
        "consulta; fontes mensais fixas não são reduzidas.",
        "Despesas: o maior entre o já planejado/recorrente e a média dos últimos 3 meses realizados, "
        "corrigida pela inflação informada.",
        "Tributos são estimativas informadas por você, não validadas.",
    ]
    assumptions = {
        "attainment": {
            "source": att.source,
            "months_used": att.months_used,
            "conservative_bps": att.conservative_bps,
            "base_bps": att.base_bps,
            "optimistic_bps": att.optimistic_bps,
            "history": recent,
        },  # fmt: skip
        "seasonality": {
            "requested": body.use_seasonality,
            "applied": idx is not None,
            "indices_bps": idx,
            "history_months": len(monthly),
        },  # fmt: skip
        "expense_baseline_cents": base_exp,
        "expense_inflation_bps_per_month": body.expense_inflation_bps,
        "floor_cents": body.floor_cents,
        "adjustments": [a.model_dump() for a in body.adjustments],
        "notes": notes,
    }
    if att.source == "MANUAL":
        notes.append(f"Poucos meses de histórico ({att.months_used}): usadas as premissas manuais de "
                     f"aproveitamento ({round(att.conservative_bps / 100)}% / {round(att.base_bps / 100)}% / "
                     f"{round(att.optimistic_bps / 100)}%).")  # fmt: skip
    if body.use_seasonality and idx is None:
        notes.append("Sazonalidade não aplicada: é preciso ao menos 12 meses de receita realizada.")
    result = {"from_month": month_str(start), "horizon_months": body.horizon_months, "months": months,
              "totals": {k_: sum(v) for k_, v in nets.items()}, "reserve": reserve,
              "data_quality": "history" if att.source == "HISTORY" else "manual_assumptions",
              "version": VERSION}  # fmt: skip
    return {"assumptions": assumptions, **result}


@router.post("/run")
def run(body: ForecastIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    """Projeta os próximos 3, 6 ou 12 meses em três cenários. Só grava se `save=true`."""
    ctx.require(P_GOAL_READ)
    if body.save:
        ctx.require(P_GOAL_WRITE)
    out = run_forecast(db, ctx, body, datetime.now(UTC))
    if body.save:
        f = Forecast(
            organization_id=ctx.organization_id, user_id=ctx.user.id, name=body.name,
            period_start=date.fromisoformat(out["from_month"] + "-01"),
            period_end=date.fromisoformat(out["months"][-1]["month"] + "-01"),
            version=VERSION, assumptions_json=out["assumptions"],
            result_json={k: v for k, v in out.items() if k != "assumptions"},
        )  # fmt: skip
        db.add(f)
        db.flush()
        audit(db, action="forecast.save", object_type="forecast", object_id=f.id,
              organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
        db.commit()
        out["saved_id"] = str(f.id)
    return out


def _mine(db: Session, ctx: TenantContext, fid: uuid.UUID) -> Forecast:
    f = db.scalar(select(Forecast).where(Forecast.id == fid, Forecast.organization_id == ctx.organization_id,
                                         Forecast.user_id == ctx.user.id, Forecast.deleted_at.is_(None)))  # fmt: skip
    if f is None:
        raise HTTPException(404, "Previsão não encontrada")
    return f


@router.get("")
def list_forecasts(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_GOAL_READ)
    rows = db.scalars(select(Forecast).where(
        Forecast.organization_id == ctx.organization_id, Forecast.user_id == ctx.user.id,
        Forecast.deleted_at.is_(None)).order_by(Forecast.created_at.desc(), Forecast.id))  # fmt: skip
    return [{"id": str(f.id), "name": f.name, "from_month": month_str(f.period_start),
             "to_month": month_str(f.period_end), "version": f.version,
             "created_at": f.created_at.isoformat()} for f in rows]  # fmt: skip


@router.get("/{forecast_id}")
def get_forecast(
    forecast_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_READ)
    f = _mine(db, ctx, forecast_id)
    return {"id": str(f.id), "name": f.name, "assumptions": f.assumptions_json, **f.result_json}


@router.delete("/{forecast_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_forecast(
    forecast_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_WRITE)
    f = _mine(db, ctx, forecast_id)
    f.deleted_at = utcnow()
    audit(db, action="forecast.delete", object_type="forecast", object_id=f.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
