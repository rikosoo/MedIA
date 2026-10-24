"""Custo fixo do consultório, custo por hora e ponto de equilíbrio (somente leitura).

Os números vêm do motor (`finance_engine.breakeven`) e dos lançamentos/recorrências do mês;
nada é gravado. Margem de contribuição por consulta é antes de tributos e de custos fixos.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_CLINIC_READ, P_FIN_READ, P_SCHED_READ
from app.db.models import ClinicService, Membership
from app.db.session import get_db
from app.modules.clinic.router import _economics, _policy_of
from app.modules.finances.router import (
    EXPENSE_CATEGORIES,
    MONTH_RE,
    _rec_q,
    _signed,
    _tx_q,
    parse_month,
)
from app.modules.goals.projection import effective_plans
from app.modules.schedule.router import capacity
from finance_engine import format_brl
from finance_engine.breakeven import ServiceInput, breakeven, fixed_per_hour_cents, per_service
from finance_engine.recurrence import occurrences_in_month

router = APIRouter(prefix="/api/v1/costs", tags=["costs"])

# Custos que não variam com o número de consultas. Fora do padrão: material e taxas de cartão (já
# entram na margem de cada serviço), impostos (não são custo de estrutura) e deslocamento/treinamento.
DEFAULT_FIXED = [
    "aluguel", "condominio", "utilidades", "secretaria", "papelaria", "copa", "limpeza",
    "software", "equipamentos", "manutencao", "marketing", "contabilidade",
]  # fmt: skip


def _fixed_by_category(db: Session, ctx: TenantContext, m: date) -> tuple[dict[str, dict], int]:
    """Despesas do mês por categoria, separando realizado, planejado e recorrências ainda sem
    lançamento. Recorrências anuais são rateadas em 12 meses (não pesam só no mês de vencimento)."""
    recs = {r.id: r for r in db.scalars(_rec_q(ctx))}
    yearly = {i for i, r in recs.items() if r.frequency == "YEARLY"}
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    out: dict[str, dict] = {}

    def add(cat: str, key: str, cents: int):
        row = out.setdefault(cat, {"realized_cents": 0, "planned_cents": 0, "projected_cents": 0,
                                   "amortized_cents": 0})  # fmt: skip
        row[key] += cents

    for t in db.scalars(_tx_q(ctx).where(_tx_q_month(m))):
        if t.type != "EXPENSE" or t.recurrence_id in yearly:
            continue
        add(
            t.category, "realized_cents" if t.status == "REALIZED" else "planned_cents", -_signed(t)
        )
    materialized = {
        t.recurrence_id for t in db.scalars(_tx_q(ctx).where(_tx_q_month(m))) if t.recurrence_id
    }
    for r in recs.values():
        if r.frequency == "YEARLY":
            if r.start_date < nxt and (r.stop_date is None or r.stop_date >= m):
                add(r.category, "amortized_cents", r.amount_cents // 12)
        elif r.id not in materialized and occurrences_in_month(
            r.frequency, r.start_date, r.stop_date, m
        ):
            add(r.category, "projected_cents", r.amount_cents)
    for row in out.values():
        row["total_cents"] = sum(
            row[k]
            for k in ("realized_cents", "planned_cents", "projected_cents", "amortized_cents")
        )
    return out, len(yearly)


def _tx_q_month(m: date):
    from app.db.models import Transaction

    return Transaction.competence_month == m


@router.get("/breakeven")
def breakeven_report(
    month: str = Query(pattern=MONTH_RE),
    categories: str | None = Query(default=None, max_length=400),
    fixed_cents: int | None = Query(default=None, ge=0, le=10**11),
    available_minutes: int | None = Query(default=None, ge=0, le=60 * 24 * 31 * 20),
    basis: str | None = Query(default=None, pattern="^(PLANNED|AVAILABLE)$"),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_FIN_READ)
    ctx.require(P_CLINIC_READ)
    m = parse_month(month)
    chosen = DEFAULT_FIXED if categories is None else [c for c in categories.split(",") if c]
    bad = [c for c in chosen if c not in EXPENSE_CATEGORIES]
    if bad:
        raise HTTPException(422, f"Categoria desconhecida: {bad[0]}")

    spent, _ = _fixed_by_category(db, ctx, m)
    cats = []
    for code, label in EXPENSE_CATEGORIES.items():
        row = spent.get(code)
        if row is None and code not in chosen:
            continue
        row = row or {
            "realized_cents": 0,
            "planned_cents": 0,
            "projected_cents": 0,
            "amortized_cents": 0,
            "total_cents": 0,
        }
        cats.append({"category": code, "label": label, "included": code in chosen, **row})
    computed = sum(c["total_cents"] for c in cats if c["included"])
    fixed = computed if fixed_cents is None else fixed_cents

    plans = effective_plans(db, ctx, m)
    inputs, names = [], {}
    for s in db.scalars(select(ClinicService).where(
        ClinicService.organization_id == ctx.organization_id, ClinicService.deleted_at.is_(None),
        ClinicService.status == "active")):  # fmt: skip
        econ, _ = _economics(s, _policy_of(db, ctx, s), s.price_cents)
        plan = plans.get(("SERVICE", s.id))
        units = plan.units if plan else 0
        inputs.append(ServiceInput(str(s.id), units, econ.margin_cents, econ.minutes_x10))
        names[str(s.id)] = s.name

    planned_minutes = sum(i.units * i.minutes_x10 for i in inputs) // 10
    avail = available_minutes
    avail_source = "MANUAL" if available_minutes is not None else None
    warnings: list[str] = []
    if avail is None and ctx.can(P_SCHED_READ):
        cap = capacity(
            month=month, user_id=None, slot_minutes=None, service_id=None, ctx=ctx, db=db
        )
        avail, avail_source = cap["gross_free_minutes"], "AGENDA"
        if not cap["availability_minutes"]:
            warnings.append(
                "Sem disponibilidade cadastrada na agenda: informe as horas do mês ou cadastre a agenda."
            )
        owners = (
            db.query(Membership)
            .filter(
                Membership.organization_id == ctx.organization_id,
                Membership.role.in_(["OWNER", "PHYSICIAN"]),
            )
            .count()
        )
        if owners > 1:
            warnings.append(
                "A agenda considerada é a sua; em clínica com vários profissionais, informe as horas totais do mês."
            )
    use = basis or ("PLANNED" if planned_minutes > 0 else "AVAILABLE")
    base_minutes = planned_minutes if use == "PLANNED" else (avail or 0)

    be = breakeven(fixed, inputs)
    rows = []
    for i in inputs:
        c = per_service(fixed, base_minutes, i)
        rows.append({
            "service_id": i.key, "name": names[i.key], "planned_units": i.units,
            "margin_per_consult_cents": i.margin_cents, "minutes_per_consult_x10": i.minutes_x10,
            "margin_per_hour_cents": c.margin_per_hour_cents,
            "fixed_per_consult_cents": c.fixed_per_unit_cents, "net_per_consult_cents": c.net_per_unit_cents,
            "net_per_hour_cents": c.net_per_hour_cents, "breakeven_alone_units": c.breakeven_alone_units,
        })  # fmt: skip
    by_id = dict(be.units)
    for r in rows:
        r["breakeven_units"] = by_id.get(r["service_id"])

    if not inputs:
        warnings.append(
            "Nenhum serviço do consultório cadastrado: cadastre um serviço para calcular a margem."
        )
    elif planned_minutes == 0:
        warnings.append(
            "Nenhuma consulta planejada para o mês: o equilíbrio por mix não pode ser calculado; veja o equilíbrio de cada serviço isolado."
        )
    if fixed == 0:
        warnings.append(
            "Nenhum custo fixo no mês: cadastre aluguel, secretária e equipamentos como despesas recorrentes."
        )
    if any(i.margin_cents <= 0 for i in inputs):
        warnings.append(
            "Há serviço com margem por consulta zero ou negativa: ele não ajuda a cobrir o custo fixo."
        )

    idle = None
    if avail and avail > planned_minutes and fixed:
        per_h = fixed_per_hour_cents(fixed, avail)
        idle = (per_h * (avail - planned_minutes)) // 60 if per_h is not None else None
    return {
        "month": month,
        "fixed": {"total_cents": fixed, "computed_cents": computed, "manual": fixed_cents is not None,
                  "by_category": cats, "default_categories": DEFAULT_FIXED, "formatted": format_brl(fixed)},  # fmt: skip
        "hours": {
            "planned_minutes": planned_minutes, "available_minutes": avail, "available_source": avail_source,
            "basis": use, "basis_minutes": base_minutes,
            "fixed_per_hour_planned_cents": fixed_per_hour_cents(fixed, planned_minutes),
            "fixed_per_hour_available_cents": fixed_per_hour_cents(fixed, avail or 0),
            "idle_cost_cents": idle,
        },  # fmt: skip
        "services": rows,
        "breakeven": {
            "status": be.status, "planned_units": be.planned_units, "contribution_cents": be.contribution_cents,
            "result_cents": be.result_cents, "avg_margin_cents": be.avg_margin_cents, "total_units": be.total_units,
            "minutes": be.minutes, "safety_bps": be.safety_bps,
            "occupancy_bps": (be.minutes * 10_000 // avail) if avail else None,
            "fits_schedule": (be.minutes <= avail) if (avail and be.total_units) else None,  # sem horas: desconhecido
        },  # fmt: skip
        "warnings": warnings,
        "assumptions": [
            "Margem por consulta = preço (com retornos esperados) − custo variável − taxa de cartão; antes de tributos e de custos fixos.",
            "Custo fixo rateado pelo tempo de cada serviço. Custos anuais entram divididos por 12.",
            "Categorias fora do padrão (material, taxas de cartão, impostos, deslocamento, treinamento) não contam como fixas; você pode incluí-las.",
            "Planejado não é realizado: este relatório mostra o ponto de equilíbrio e a projeção, não o dinheiro recebido.",
        ],
    }  # fmt: skip
