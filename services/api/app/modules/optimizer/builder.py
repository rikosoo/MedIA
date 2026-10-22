"""Monta o problema do otimizador a partir dos dados reais e traduz as soluções para a API."""

import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from optimizer_engine import Activity, Candidate, Problem
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.rbac import P_SCHED_READ
from app.db.models import ClinicService, ScheduleEvent, WorkSource
from app.modules.clinic.router import _economics, _policy_of
from app.modules.goals.projection import effective_plans, get_goal, month_numbers, month_str
from app.modules.schedule.router import (
    _closures,
    _constraint,
    _engine_event,
    _events_q,
    _tz,
    capacity_intervals,
)
from app.modules.schedule.router import overview as schedule_overview
from finance_engine import (
    StreamInput,
    compute_stream,
    effective_target_cents,
    format_brl,
    goal_gap_cents,
    return_month,
)
from finance_engine.schedule import (
    ACTIVE_TYPES,
    Event,
    Interval,
    clip,
    count_slots,
    is_night,
    local_month,
    total_minutes,
)

LABELS = {
    "LESS_HOURS": "Menor tempo comprometido",
    "MAX_MARGIN": "Maior margem",
    "LEAST_CHANGE": "Menor alteração de rotina",
    "BALANCED": "Mais equilibrado",
    "CONSERVATIVE": "Plano conservador",
}
CONSTRAINT_LABELS = {
    "ACTIVE_LIMIT": "Limite de horas ativas adicionais",
    "COMMITTED_LIMIT": "Limite de horas comprometidas adicionais",
    "MAX_UNITS": "Número de oportunidades disponíveis",
    "DEMAND": "Demanda estimada de pacientes",
    "CONSULT_CAPACITY": "Horários livres de consulta na agenda",
    "CALENDAR": "Agenda: conflitos de horário e descanso",
    "BUDGET": "Orçamento de captação",
    "NIGHT_LIMIT": "Limite de plantões noturnos",
    "MAX_EVENTS": "Máximo de plantões/sobreavisos novos",
}
SUGGESTIONS = {
    "ACTIVE_LIMIT": "Só dá para chegar lá aceitando mais horas ativas no mês.",
    "COMMITTED_LIMIT": "Só dá para chegar lá aceitando mais horas comprometidas no mês.",
    "MAX_UNITS": "Faltam oportunidades: plantões, sobreavisos ou vagas além das informadas.",
    "DEMAND": "A demanda informada limita as consultas; seria preciso captar mais pacientes.",
    "CONSULT_CAPACITY": "Faltam horários livres de consulta; abra mais disponibilidade na agenda.",
    "CALENDAR": "Os horários possíveis se sobrepõem ou ferem o descanso; libere outros dias.",
    "BUDGET": "O orçamento de captação não cobre os pacientes necessários.",
    "NIGHT_LIMIT": "O limite de plantões noturnos impede os plantões necessários.",
    "MAX_EVENTS": "O teto de eventos novos impede chegar à meta.",
}


def minutes_of(dt: datetime) -> int:
    return int(dt.timestamp() // 60)


def dec(x: float) -> str:
    """Decimal pt-BR com uma casa (48,2)."""
    return f"{x:.1f}".replace(".", ",")


def label_of(code: str) -> str:
    return CONSTRAINT_LABELS.get(code.split(":")[0], code)


@dataclass
class Built:
    problem: Problem
    month: date
    metric: str
    goal_id: uuid.UUID | None
    baseline_cents: int
    gap_cents: int
    effective_target_cents: int
    meta: dict[str, dict] = field(default_factory=dict)
    cand_meta: dict[str, dict] = field(default_factory=dict)
    excluded: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    haircut_bps: int = 2000


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def build(db: Session, ctx: TenantContext, req) -> Built:
    m = date(int(req.month[:4]), int(req.month[5:7]), 1)
    tz = _tz(db, ctx)
    uid = ctx.user.id
    win = local_month(m, tz)

    goal = None
    if req.goal_id is not None:
        goal = get_goal(db, ctx, m, req.goal_id)
        if goal.month != m:
            raise HTTPException(422, "A meta informada é de outro mês")
        metric, eff = (
            goal.metric_type,
            effective_target_cents(goal.target_cents, goal.reserve_cents, goal.use_reserves),
        )
    else:
        if req.target_cents is None:
            raise HTTPException(422, "Informe goal_id ou target_cents")
        metric, eff = req.goal_type, req.target_cents
    if req.baseline_cents is not None:
        baseline = req.baseline_cents
    else:
        baseline = month_numbers(db, ctx, m, metric)["projection"].value_cents
    gap = goal_gap_cents(eff, baseline)

    tc = _constraint(db, ctx, uid, m)
    ov = schedule_overview(month=month_str(m), user_id=None, ctx=ctx, db=db)
    limits = ov["limits"] or {}
    ci = capacity_intervals(db, ctx, uid, tz, m, tc, win)
    remaining = ci["remaining"]
    _, closure_ivs = _closures(db, ctx, uid, win, tz, tc)

    def headroom(limit, current):
        return None if limit is None else max(limit - current, 0)

    caps_a = [v for v in (req.max_additional_active_minutes,
                          headroom(limits.get("max_active_minutes"), ov["active_minutes"])) if v is not None]  # fmt: skip
    caps_c = [v for v in (req.max_additional_committed_minutes,
                          headroom(limits.get("max_committed_minutes"), ov["committed_minutes"])) if v is not None]  # fmt: skip
    night_room = headroom(limits.get("max_night_shifts"), ov["night_shifts"])

    # tempo já ocupado na agenda (inclui o descanso exigido após plantões)
    busy: list[Interval] = []
    for e in db.scalars(
        _events_q(ctx, uid).where(
            ScheduleEvent.starts_at < (win.end + timedelta(days=8)).astimezone(ZoneInfo("UTC")),
            ScheduleEvent.ends_at > (win.start - timedelta(days=8)).astimezone(ZoneInfo("UTC")),
        )
    ):
        ev: Event = _engine_event(e, tc)
        if e.type == "ON_CALL" and e.allow_overlap:
            continue  # sobreposição condicional já permitida pelo usuário
        busy.append(ev.interval)
        if ev.recovery_minutes > 0 and e.type in ACTIVE_TYPES:
            busy.append(Interval(ev.end, ev.end + timedelta(minutes=ev.recovery_minutes)))

    plans = effective_plans(db, ctx, m)
    activities: list[Activity] = []
    cands: list[Candidate] = []
    built = Built(
        None,
        m,
        metric,
        goal.id if goal else None,
        baseline,
        gap,
        eff,
        haircut_bps=req.conservative_haircut_bps,
    )  # type: ignore[arg-type]
    keep = 10_000 - req.conservative_haircut_bps
    pess = lambda v: v * keep // 10_000  # noqa: E731
    blocks_info = {"generated": 0, "dropped_busy": 0, "dropped_closed_day": 0}

    for a in req.activities:
        key = f"{a.source_type}:{a.source_id}"
        if a.source_type == "WORK":
            w = db.scalar(
                select(WorkSource).where(
                    WorkSource.id == a.source_id,
                    WorkSource.organization_id == ctx.organization_id,
                    WorkSource.owner_user_id == uid,
                    WorkSource.deleted_at.is_(None),
                    WorkSource.status == "active",
                )
            )
            if w is None:
                raise HTTPException(422, "Trabalho inválido")
            if w.work_type == "CLINIC":
                raise HTTPException(
                    422, "A renda do consultório é otimizada por serviços, não como trabalho."
                )
            r = w.income_rule
            plan = plans.get(("WORK", w.id))
            tax = plan.tax_rate_bps if plan and plan.tax_rate_bps is not None else r.tax_rate_bps
            gross, var = r.gross_cents, r.variable_cost_cents
            net = gross - var - _ceil_div(gross * tax, 10_000)
            committed = max(r.commitment_minutes, r.duration_minutes)
            if committed <= 0:
                built.excluded.append(
                    {"key": key, "label": w.name, "reason": "Sem duração informada no cadastro."}
                )
                continue
            if net <= 0:
                built.excluded.append(
                    {
                        "key": key,
                        "label": w.name,
                        "reason": "Ganho líquido por unidade não é positivo.",
                    }
                )
                continue
            recovery = a.recovery_minutes
            if recovery is None:
                recovery = tc.min_rest_minutes if (tc and w.work_type == "SHIFT") else 0
            activities.append(Activity(
                key, "BLOCK", w.name, net, pess(net), active_minutes=r.duration_minutes,
                committed_minutes=committed, max_units=a.max_units,
            ))  # fmt: skip
            built.meta[key] = {"label": w.name, "kind": "BLOCK", "source_type": "WORK",
                               "source_id": str(w.id), "work_type": w.work_type, "gross": gross,
                               "variable": var, "tax_bps": tax, "acq": 0, "recovery": recovery,
                               "active": r.duration_minutes, "committed": committed}  # fmt: skip
            start_t = a.start_time or (time(7, 0) if w.work_type == "SHIFT" else time(8, 0))
            days = a.candidate_days or [
                m + timedelta(days=i) for i in range((win.end.date() - m).days)
            ]
            for d in sorted(set(days)):
                if d.year != m.year or d.month != m.month:
                    continue
                blocks_info["generated"] += 1
                start = datetime.combine(d, start_t, tzinfo=tz)
                end = start + timedelta(minutes=committed)
                occ = end + timedelta(minutes=recovery)
                cand_iv = Interval(start, occ)
                if any(b.start < cand_iv.end and b.end > cand_iv.start for b in busy):
                    blocks_info["dropped_busy"] += 1
                    continue
                active_iv = Interval(start, end)
                if any(c.start < active_iv.end and c.end > active_iv.start for c in closure_ivs):
                    blocks_info["dropped_closed_day"] += 1
                    continue
                night = is_night(Event("x", "SHIFT", start, end), tz)
                lost = total_minutes(clip(remaining, cand_iv))
                cid = f"{key}@{d.isoformat()}"
                cands.append(Candidate(cid, key, minutes_of(start), minutes_of(end), minutes_of(occ),
                                       d.isoformat(), night, lost))  # fmt: skip
                built.cand_meta[cid] = {"activity_key": key, "label": w.name, "date": d.isoformat(),
                                        "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                                        "night": night}  # fmt: skip
        else:
            s = db.scalar(
                select(ClinicService).where(
                    ClinicService.id == a.source_id,
                    ClinicService.organization_id == ctx.organization_id,
                    ClinicService.deleted_at.is_(None),
                    ClinicService.status == "active",
                )
            )
            if s is None:
                raise HTTPException(422, "Serviço inválido")
            econ, rp = _economics(s, _policy_of(db, ctx, s), s.price_cents)
            plan = plans.get(("SERVICE", s.id))
            tax = plan.tax_rate_bps if plan and plan.tax_rate_bps is not None else 0
            gross = econ.gross_revenue_cents
            var = econ.fee_cents + econ.variable_cost_cents
            acq = a.acquisition_cost_cents
            net = gross - var - _ceil_div(gross * tax, 10_000) - acq
            if net <= 0:
                built.excluded.append(
                    {
                        "key": key,
                        "label": s.name,
                        "reason": "Ganho líquido por paciente não é positivo.",
                    }
                )
                continue
            slots = count_slots(remaining, s.duration_minutes)
            demand = a.demand_limit
            activities.append(Activity(
                key, "CONSULT", s.name, net, pess(net), max_units=a.max_units,
                demand_limit=demand, demand_limit_pessimistic=None if demand is None else pess(demand),
                slots_limit=slots, consult_minutes_x10=econ.minutes_x10, acquisition_cost_cents=acq,
            ))  # fmt: skip
            built.meta[key] = {
                "label": s.name, "kind": "CONSULT", "source_type": "SERVICE", "source_id": str(s.id),
                "gross": gross, "variable": var, "tax_bps": tax, "acq": acq,
                "returns_bps": econ.expected_returns_bps, "return_delay_days": rp["delay_days"],
                "return_minutes": rp["return_minutes"], "minutes_x10": econ.minutes_x10,
                "demand_limit": demand,
            }  # fmt: skip

    consult_free = total_minutes(remaining)
    weights = req.weights or {"hours": 40, "change": 20, "risk": 20, "margin": 20}
    built.problem = Problem(
        gap_cents=gap,
        activities=tuple(activities),
        candidates=tuple(cands),
        max_active_minutes=min(caps_a) if caps_a else None,
        max_committed_minutes=min(caps_c) if caps_c else None,
        consult_free_minutes=consult_free,
        max_night_shifts=night_room,
        marketing_budget_cents=req.marketing_budget_cents,
        max_block_events=req.max_block_events,
        weights=weights,
    )
    built.info = {
        "candidates": blocks_info | {"kept": len(cands)},
        "consult_free_minutes": consult_free,
        "headroom": {
            "active_minutes": built.problem.max_active_minutes,
            "committed_minutes": built.problem.max_committed_minutes,
            "night_shifts": night_room,
        },  # fmt: skip
        "no_hours_limit": not caps_a and not caps_c,
        "can_read_schedule": ctx.can(P_SCHED_READ),
    }
    return built


def _exact_gain(b: Built, key: str, units: int) -> int:
    mt = b.meta[key]
    s = compute_stream(StreamInput(key, mt["source_type"], units, "PROBABLE", mt["gross"],
                                   mt["variable"], mt["tax_bps"], 0, 0))  # fmt: skip
    return s.net_cents - units * mt["acq"]


def render(b: Built, scenario, ranking: int, reserve_label: str | None = None) -> dict:
    sol = scenario.solution
    units_out, total = [], 0
    for a in b.problem.activities:
        n = sol.units.get(a.key, 0)
        if not n:
            continue
        g = _exact_gain(b, a.key, n)
        total += g
        mt = b.meta[a.key]
        units_out.append({
            "key": a.key, "label": a.label, "kind": mt["kind"], "source_type": mt["source_type"],
            "source_id": mt["source_id"], "units": n, "net_gain_unit_cents": a.net_gain_cents,
            "net_gain_cents": g,
            "committed_minutes": (a.committed_minutes * n if a.kind == "BLOCK"
                                  else math.ceil(a.consult_minutes_x10 * n / 10)),
        })  # fmt: skip
    placements = [{"id": cid, **b.cand_meta[cid]} for cid in sol.placements]
    placements.sort(key=lambda p: (p["starts_at"], p["id"]))

    risks: list[str] = []
    for u in units_out:
        mt = b.meta[u["key"]]
        if mt["kind"] == "CONSULT" and mt["demand_limit"] is None:
            risks.append(
                f"{u['units']} consulta(s) de {u['label']} dependem de captação: a demanda não foi informada."
            )
        if mt["kind"] == "BLOCK" and mt.get("work_type") == "ON_CALL":
            risks.append(
                f"Sobreaviso ({u['label']}): as horas ativas dependem de acionamentos incertos."
            )
        if mt["kind"] == "CONSULT" and mt["returns_bps"] and mt["return_delay_days"]:
            when = return_month(date(b.month.year, b.month.month, 15), mt["return_delay_days"])
            if when != b.month:
                n_ret = u["units"] * mt["returns_bps"] / 10_000
                mins = n_ret * mt["return_minutes"]
                risks.append(
                    f"≈{dec(n_ret)} retorno(s) de {u['label']} devem cair em {month_str(when)} e ocupar "
                    f"≈{int(mins)} min lá; confira a agenda desse mês."
                )
    if not scenario.pessimistic_ok:
        risks.append(
            f"Não fecha a meta se os ganhos e a demanda forem {b.haircut_bps // 100}% menores."
        )
    if b.info.get("no_hours_limit"):
        risks.append("Nenhum limite de horas foi informado: o cenário não tem teto de esforço.")

    after = b.baseline_cents + total
    out = {
        "ranking": ranking,
        "objective": scenario.objectives[0],
        "objectives": scenario.objectives,
        "labels": [LABELS[o] for o in scenario.objectives],
        "units": units_out,
        "placements": placements,
        "net_gain_cents": total,
        "net_gain_pessimistic_cents": sol.gain_pessimistic_cents,
        "active_minutes": sol.active_minutes,
        "committed_minutes": sol.committed_minutes,
        "new_days_used": sol.days_used,
        "block_events": sol.block_events,
        "consult_units": sol.consult_units,
        "baseline_cents": b.baseline_cents,
        "projected_after_cents": after,
        "effective_target_cents": b.effective_target_cents,
        "surplus_cents": after - b.effective_target_cents,
        "gap_closed": after >= b.effective_target_cents,
        "pessimistic_ok": scenario.pessimistic_ok,
        "net_per_committed_hour_cents": (total * 60 // sol.committed_minutes)
        if sol.committed_minutes
        else None,
        "binding_constraints": [{"code": c, "label": label_of(c)} for c in scenario.binding],
        "risks": risks,
        "optimal": sol.optimal,
    }
    return out


def explain(b: Built, outs: list[dict]) -> None:
    """Texto de 'Por que esse cenário?', gerado só a partir dos números (sem IA)."""
    lightest = min(o["committed_minutes"] for o in outs)
    w = b.problem.weights
    for o in outs:
        lines = []
        for obj in o["objectives"]:
            if obj == "LESS_HOURS":
                lines.append(
                    f"Fecha a meta com o menor tempo comprometido possível: {dec(o['committed_minutes'] / 60)} h."
                )
            elif obj == "MAX_MARGIN":
                lines.append(f"Maior ganho líquido dentro dos limites: {format_brl(o['net_gain_cents'])} "
                             f"(folga de {format_brl(o['surplus_cents'])} sobre a meta).")  # fmt: skip
            elif obj == "LEAST_CHANGE":
                lines.append(f"Menor mudança de rotina: {o['new_days_used']} dia(s) novo(s) e "
                             f"{o['block_events']} plantão(ões)/sobreaviso(s) novo(s).")  # fmt: skip
            elif obj == "BALANCED":
                lines.append(f"Equilíbrio entre horas ({w['hours']}%), mudança de rotina ({w['change']}%), "
                             f"risco ({w['risk']}%) e folga financeira ({w['margin']}%).")  # fmt: skip
            elif obj == "CONSERVATIVE":
                lines.append(f"Ainda fecha a meta com ganhos e demanda {b.haircut_bps // 100}% menores: "
                             f"{format_brl(o['net_gain_pessimistic_cents'])} no cenário pessimista.")  # fmt: skip
        extra = o["committed_minutes"] - lightest
        if extra > 0:
            lines.append(f"Compromete {dec(extra / 60)} h a mais que o cenário mais leve.")
        o["explanation"] = lines


def infeasible_out(b: Built, max_gain: int, blocking: list[dict]) -> dict:
    shortfall = max(b.gap_cents - max_gain, 0)
    items = []
    for bl in blocking:
        names = bl["constraints"]
        items.append({
            "constraints": names,
            "labels": [CONSTRAINT_LABELS[c] for c in names],
            "max_gain_if_relaxed_cents": bl["max_gain_if_relaxed_cents"],
            "increase_cents": bl["increase_cents"],
            "closes_gap": bl["closes_gap"],
            "message": " ".join(SUGGESTIONS[c] for c in names),
        })  # fmt: skip
    return {
        "max_achievable_gain_cents": max_gain,
        "shortfall_cents": shortfall,
        "max_projected_cents": b.baseline_cents + max_gain,
        "blocking_constraints": items,
        "message": (
            f"Com as restrições atuais o máximo alcançável é {format_brl(b.baseline_cents + max_gain)}; "
            f"faltam {format_brl(shortfall)} para a meta de {format_brl(b.effective_target_cents)}."
        ),
    }
