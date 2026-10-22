import time as _time
import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from optimizer_engine import ENGINE_VERSION, OBJECTIVES, optimize
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_GOAL_READ, P_GOAL_WRITE, P_SCHED_WRITE
from app.db.base import utcnow
from app.db.models import (
    IncomePlan,
    OptimizerRun,
    OptimizerScenario,
    SavedPlan,
    ScheduleEvent,
    WorkSource,
)
from app.db.session import get_db
from app.modules.billing.entitlements import check_limit
from app.modules.goals.projection import effective_plans, month_numbers, month_str
from app.modules.optimizer.builder import LABELS, build, explain, infeasible_out, render
from app.modules.schedule.router import _assess, _tz

router = APIRouter(prefix="/api/v1/optimizer", tags=["optimizer"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
EVENT_TYPE_BY_WORK = {
    "SHIFT": "SHIFT", "ON_CALL": "ON_CALL", "EMPLOYMENT": "EMPLOYMENT",
    "TELEMEDICINE": "TELEMEDICINE", "PROCEDURE": "PROCEDURE",
}  # fmt: skip
ASSUMPTIONS = [
    "Ganhos por unidade vêm dos cadastros, líquidos de tributo estimado e custos; tributos não "
    "são validados por contador.",
    "Plantões e sobreavisos só são propostos em dias livres, sem sobreposição e respeitando o "
    "descanso informado; sobreposições condicionais nunca são sugeridas.",
    "O número de oportunidades e a demanda de pacientes são informados por você; o app não os prevê.",
    "O cálculo é determinístico e cada cenário passa por uma segunda validação independente.",
    "Aplicar ao planejamento não registra trabalho como realizado, não lança receita e não "
    "agenda nada sem sua confirmação.",
]


class ActivityIn(BaseModel):
    source_type: str = Field(pattern="^(WORK|SERVICE)$")
    source_id: uuid.UUID
    max_units: int = Field(ge=0, le=1000)
    start_time: time | None = None  # trabalhos: horário de início dos plantões/sobreavisos
    candidate_days: list[date] | None = Field(
        default=None, max_length=31
    )  # dias realmente possíveis
    demand_limit: int | None = Field(default=None, ge=0, le=100_000)  # serviços
    acquisition_cost_cents: int = Field(default=0, ge=0, le=10**9)  # serviços, por paciente novo
    recovery_minutes: int | None = Field(default=None, ge=0, le=7 * 24 * 60)


class RunIn(BaseModel):
    month: str = Field(pattern=MONTH_RE)
    goal_id: uuid.UUID | None = None
    goal_type: str = Field(
        default="PERSONAL_NET", pattern="^(PERSONAL_NET|CLINIC_PROFIT|GROSS_REVENUE)$"
    )
    target_cents: int | None = Field(default=None, ge=0, le=10**12)
    baseline_cents: int | None = Field(default=None, ge=-(10**12), le=10**12)
    max_additional_active_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 31)
    max_additional_committed_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 31)
    marketing_budget_cents: int | None = Field(default=None, ge=0, le=10**12)
    max_block_events: int | None = Field(default=None, ge=0, le=62)
    conservative_haircut_bps: int = Field(default=2000, ge=0, le=5000)
    objectives: list[str] | None = None
    weights: dict[str, int] | None = None
    activities: list[ActivityIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _ok(self):
        if self.objectives is not None:
            bad = [o for o in self.objectives if o not in OBJECTIVES]
            if bad or not self.objectives:
                raise ValueError(f"objetivo inválido: {bad or 'lista vazia'}")
        if self.weights is not None:
            if set(self.weights) != {"hours", "change", "risk", "margin"}:
                raise ValueError("weights deve ter hours, change, risk e margin")
            if any(v < 0 for v in self.weights.values()) or sum(self.weights.values()) != 100:
                raise ValueError("os pesos devem ser não negativos e somar 100")
        keys = [(a.source_type, a.source_id) for a in self.activities]
        if len(set(keys)) != len(keys):
            raise ValueError("atividade repetida")
        if self.goal_id is None and self.target_cents is None:
            raise ValueError("informe goal_id ou target_cents")
        return self


def _own_run(db: Session, ctx: TenantContext, rid: uuid.UUID) -> OptimizerRun:
    r = db.scalar(
        select(OptimizerRun).where(
            OptimizerRun.id == rid,
            OptimizerRun.organization_id == ctx.organization_id,
            OptimizerRun.created_by == ctx.user.id,
        )
    )
    if r is None:
        raise HTTPException(404, "Execução não encontrada")
    return r


def _own_scenario(db: Session, ctx: TenantContext, sid: uuid.UUID):
    sc = db.scalar(
        select(OptimizerScenario).where(
            OptimizerScenario.id == sid, OptimizerScenario.organization_id == ctx.organization_id
        )
    )
    run = db.get(OptimizerRun, sc.run_id) if sc else None
    if sc is None or run is None or run.created_by != ctx.user.id:
        raise HTTPException(404, "Cenário não encontrado")
    return sc, run


def _run_out(run: OptimizerRun, scenarios: list[OptimizerScenario]) -> dict:
    return {
        "run_id": str(run.id),
        "month": month_str(run.month),
        "metric_type": run.metric_type,
        "engine_version": run.engine_version,
        "outcome": run.outcome,
        "feasible": run.outcome in ("FEASIBLE", "ALREADY_MET"),
        "baseline_cents": run.baseline_cents,
        "gap_cents": run.gap_cents,
        "runtime_ms": run.runtime_ms,
        "scenarios": [{"id": str(s.id), **s.output_json} for s in scenarios],
        **run.result_json,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.post("/runs", status_code=status.HTTP_201_CREATED)
def create_run(
    body: RunIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_WRITE)
    check_limit(db, ctx, "optimizer_runs_per_month")
    t0 = _time.perf_counter()
    b = build(db, ctx, body)
    objectives = tuple(o for o in OBJECTIVES if o in (body.objectives or OBJECTIVES))
    try:
        res = optimize(b.problem, objectives)
    except RuntimeError as exc:  # a validação independente rejeitou: é bug do motor, não do usuário
        raise HTTPException(500, "Falha interna na validação do cenário") from exc

    result: dict = {
        "assumptions": ASSUMPTIONS,
        "excluded_activities": b.excluded,
        "info": b.info,
        "effective_target_cents": b.effective_target_cents,
        "objectives_requested": list(objectives),
    }
    outs: list[dict] = []
    if res.already_met:
        outcome = "ALREADY_MET"
        result["message"] = (
            "A meta já é atingida pela projeção atual. Em vez de mais trabalho, priorize "
            "estabilidade e reserva."
        )
    elif res.feasible:
        outcome = "FEASIBLE"
        order = {o: i for i, o in enumerate(OBJECTIVES)}
        scs = sorted(res.scenarios, key=lambda s: min(order[o] for o in s.objectives))
        outs = [render(b, s, i + 1) for i, s in enumerate(scs)]
        explain(b, outs)
        result["conservative_feasible"] = res.conservative_feasible
        if res.conservative_feasible is False:
            result["conservative_max_gain_cents"] = res.conservative_max_gain_cents
            result["conservative_message"] = (
                "Nenhum plano fecha a meta com ganhos e demanda pessimistas; o plano conservador "
                "não foi gerado."
            )
    else:
        outcome = "INFEASIBLE"
        result.update(infeasible_out(b, res.max_gain_cents or 0, res.blocking))

    run = OptimizerRun(
        organization_id=ctx.organization_id, created_by=ctx.user.id, goal_id=b.goal_id, month=b.month,
        metric_type=b.metric, engine_version=ENGINE_VERSION, outcome=outcome,
        baseline_cents=b.baseline_cents, gap_cents=b.gap_cents,
        runtime_ms=int((_time.perf_counter() - t0) * 1000),
        params_json={"request": body.model_dump(mode="json"), "problem": {
            "gap_cents": b.problem.gap_cents,
            "max_active_minutes": b.problem.max_active_minutes,
            "max_committed_minutes": b.problem.max_committed_minutes,
            "consult_free_minutes": b.problem.consult_free_minutes,
            "max_night_shifts": b.problem.max_night_shifts,
            "activities": [{"key": a.key, "kind": a.kind, "net_gain_cents": a.net_gain_cents,
                            "max_units": a.max_units, "demand_limit": a.demand_limit}
                           for a in b.problem.activities],
            "candidates": len(b.problem.candidates)}},  # fmt: skip
        result_json=result,
    )  # fmt: skip
    db.add(run)
    db.flush()
    rows = []
    for o in outs:
        row = OptimizerScenario(
            organization_id=ctx.organization_id, run_id=run.id, ranking=o["ranking"],
            objective=o["objective"], output_json=o, feasibility_status="FEASIBLE",
            warnings_json=o["risks"],
        )  # fmt: skip
        db.add(row)
        rows.append(row)
    db.flush()
    audit(db, action="optimizer.run", object_type="optimizer_run", object_id=run.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"outcome": outcome, "scenarios": len(rows)})  # fmt: skip
    db.commit()
    return _run_out(run, rows)


@router.get("/runs")
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_GOAL_READ)
    rows = db.scalars(
        select(OptimizerRun)
        .where(
            OptimizerRun.organization_id == ctx.organization_id,
            OptimizerRun.created_by == ctx.user.id,
        )
        .order_by(OptimizerRun.created_at.desc(), OptimizerRun.id)
        .limit(limit)
    )
    return [
        {"run_id": str(r.id), "month": month_str(r.month), "outcome": r.outcome,
         "gap_cents": r.gap_cents, "created_at": r.created_at.isoformat()}
        for r in rows
    ]  # fmt: skip


@router.get("/runs/{run_id}")
def get_run(
    run_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_READ)
    run = _own_run(db, ctx, run_id)
    scs = list(db.scalars(select(OptimizerScenario).where(OptimizerScenario.run_id == run.id)
                          .order_by(OptimizerScenario.ranking)))  # fmt: skip
    return _run_out(run, scs)


class CompareIn(BaseModel):
    scenario_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)


@router.post("/compare")
def compare(
    body: CompareIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_READ)
    cols = []
    for sid in body.scenario_ids:
        sc, _ = _own_scenario(db, ctx, sid)
        o = sc.output_json
        cols.append({
            "id": str(sc.id), "label": " + ".join(o["labels"]), "net_gain_cents": o["net_gain_cents"],
            "projected_after_cents": o["projected_after_cents"], "surplus_cents": o["surplus_cents"],
            "active_minutes": o["active_minutes"], "committed_minutes": o["committed_minutes"],
            "new_days_used": o["new_days_used"], "risks": len(o["risks"]),
            "pessimistic_ok": o["pessimistic_ok"], "units": {u["label"]: u["units"] for u in o["units"]},
        })  # fmt: skip
    return {"scenarios": cols}


# ---------- salvar / aplicar / desfazer ----------


def _saved_out(sp: SavedPlan) -> dict:
    return {
        "id": str(sp.id), "scenario_id": str(sp.scenario_id), "month": month_str(sp.month),
        "status": sp.status, "applied_at": sp.applied_at.isoformat() if sp.applied_at else None,
        "snapshot": sp.snapshot_json, "applied": sp.applied_json,
    }  # fmt: skip


def _get_or_save(
    db: Session, ctx: TenantContext, sc: OptimizerScenario, run: OptimizerRun
) -> SavedPlan:
    sp = db.scalar(
        select(SavedPlan)
        .where(SavedPlan.scenario_id == sc.id, SavedPlan.user_id == ctx.user.id)
        .order_by(SavedPlan.created_at.desc())
    )
    if sp is not None and sp.status != "UNDONE":
        return sp
    sp = SavedPlan(
        organization_id=ctx.organization_id, user_id=ctx.user.id, scenario_id=sc.id, month=run.month,
        status="SAVED", snapshot_json=sc.output_json, applied_json={},
    )  # fmt: skip
    db.add(sp)
    db.flush()
    return sp


@router.post("/scenarios/{scenario_id}/save", status_code=status.HTTP_201_CREATED)
def save_scenario(
    scenario_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Congela o cenário (snapshot das entradas e do resultado); mudanças futuras não o alteram."""
    ctx.require(P_GOAL_WRITE)
    sc, run = _own_scenario(db, ctx, scenario_id)
    sp = _get_or_save(db, ctx, sc, run)
    audit(db, action="optimizer.save", object_type="saved_plan", object_id=sp.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return _saved_out(sp)


class ApplyIn(BaseModel):
    create_events: bool = False  # confirmação explícita para colocar plantões/sobreavisos na agenda


@router.post("/scenarios/{scenario_id}/apply")
def apply_scenario(
    scenario_id: uuid.UUID,
    body: ApplyIn | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Planeja o cenário: soma as unidades como adicional *provável* na composição do mês.

    Não registra trabalho como realizado, não lança receita e só cria eventos PLANEJADOS na agenda
    com `create_events=true`. Idempotente: aplicar de novo o mesmo cenário devolve 409.
    """
    ctx.require(P_GOAL_WRITE)
    body = body or ApplyIn()
    sc, run = _own_scenario(db, ctx, scenario_id)
    sp = _get_or_save(db, ctx, sc, run)
    if sp.status == "APPLIED":
        raise HTTPException(409, "Este cenário já foi aplicado. Desfaça antes de aplicar de novo.")
    if sc.output_json["units"] == []:
        raise HTTPException(422, "O cenário não tem unidades para aplicar")
    m = run.month
    tz = _tz(db, ctx)
    plans = effective_plans(db, ctx, m)

    # valida tudo antes de mudar qualquer coisa (aplicação atômica)
    for u in sc.output_json["units"]:
        sid = uuid.UUID(u["source_id"])
        if u["source_type"] == "WORK":
            w = db.scalar(select(WorkSource).where(
                WorkSource.id == sid, WorkSource.organization_id == ctx.organization_id,
                WorkSource.owner_user_id == ctx.user.id, WorkSource.deleted_at.is_(None)))  # fmt: skip
            if w is None:
                raise HTTPException(409, f"O trabalho '{u['label']}' não existe mais")
    events_to_make = []
    if body.create_events:
        ctx.require(P_SCHED_WRITE)
        from datetime import datetime

        from finance_engine.schedule import Event

        for p in sc.output_json["placements"]:
            key = p["activity_key"]
            work = next((u for u in sc.output_json["units"] if u["key"] == key), None)
            w = db.get(WorkSource, uuid.UUID(work["source_id"])) if work else None
            etype = EVENT_TYPE_BY_WORK.get(w.work_type) if w else None
            if etype is None:
                continue  # tipo sem evento equivalente na agenda
            s, e = datetime.fromisoformat(p["starts_at"]), datetime.fromisoformat(p["ends_at"])
            rec = None
            report = _assess(db, ctx, ctx.user.id, tz, Event("new", etype, s, e, False), rec)
            if any(c["kind"] == "HARD" for c in report["conflicts"]):
                raise HTTPException(
                    409,
                    f"A agenda mudou: {p['date']} agora tem conflito. Rode o otimizador de novo.",
                )
            events_to_make.append((etype, w, s, e))

    changes, created_events = [], []
    for u in sc.output_json["units"]:
        sid = uuid.UUID(u["source_id"])
        plan = db.scalar(select(IncomePlan).where(
            IncomePlan.organization_id == ctx.organization_id, IncomePlan.source_type == u["source_type"],
            IncomePlan.source_id == sid, IncomePlan.month == m))  # fmt: skip
        created_row = False
        if plan is None:
            base = plans.get((u["source_type"], sid))
            plan = IncomePlan(
                organization_id=ctx.organization_id, created_by=ctx.user.id, month=m,
                source_type=u["source_type"], source_id=sid, units=base.units if base else 0,
                certainty=base.certainty if base else "PROBABLE",
                tax_rate_bps=base.tax_rate_bps if base else None, extra_units=0,
            )  # fmt: skip
            db.add(plan)
            db.flush()
            created_row = True
        plan.extra_units += u["units"]
        changes.append({"plan_id": str(plan.id), "source_type": u["source_type"], "source_id": str(sid),
                        "label": u["label"], "extra_added": u["units"], "created_row": created_row,
                        "units_at_creation": plan.units})  # fmt: skip
    for etype, w, s, e in events_to_make:
        ev = ScheduleEvent(
            organization_id=ctx.organization_id, user_id=ctx.user.id, created_by=ctx.user.id, type=etype,
            title=f"Otimizador: {w.name}", starts_at=s, ends_at=e, status="PLANNED", work_source_id=w.id,
        )  # fmt: skip
        db.add(ev)
        db.flush()
        created_events.append(str(ev.id))
    sp.status, sp.applied_at = "APPLIED", utcnow()
    current = month_numbers(db, ctx, m, run.metric_type)["projection"].value_cents
    sp.applied_json = {"plan_changes": changes, "event_ids": created_events,
                       "baseline_at_run_cents": run.baseline_cents}  # fmt: skip
    audit(db, action="optimizer.apply", object_type="saved_plan", object_id=sp.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"events": len(created_events)})  # fmt: skip
    db.commit()
    return {**_saved_out(sp), "projected_after_cents": current,
            "events_created": len(created_events)}  # fmt: skip


@router.post("/saved-plans/{plan_id}/undo")
def undo(
    plan_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_WRITE)
    sp = db.scalar(select(SavedPlan).where(
        SavedPlan.id == plan_id, SavedPlan.organization_id == ctx.organization_id,
        SavedPlan.user_id == ctx.user.id))  # fmt: skip
    if sp is None:
        raise HTTPException(404, "Plano não encontrado")
    if sp.status != "APPLIED":
        raise HTTPException(409, "Só é possível desfazer um plano aplicado")
    for ch in sp.applied_json.get("plan_changes", []):
        plan = db.get(IncomePlan, uuid.UUID(ch["plan_id"]))
        if plan is None:
            continue
        plan.extra_units = max(plan.extra_units - ch["extra_added"], 0)
        if ch["created_row"] and plan.extra_units == 0 and plan.units == ch["units_at_creation"]:
            db.delete(plan)  # a linha só existia por causa da aplicação
    for eid in sp.applied_json.get("event_ids", []):
        ev = db.get(ScheduleEvent, uuid.UUID(eid))
        if ev is not None and ev.deleted_at is None and ev.status == "PLANNED":
            ev.deleted_at = utcnow()
    sp.status, sp.undone_at = "UNDONE", utcnow()
    audit(db, action="optimizer.undo", object_type="saved_plan", object_id=sp.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return _saved_out(sp)


@router.get("/saved-plans")
def list_saved(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_GOAL_READ)
    rows = db.scalars(select(SavedPlan).where(
        SavedPlan.organization_id == ctx.organization_id, SavedPlan.user_id == ctx.user.id
    ).order_by(SavedPlan.created_at.desc(), SavedPlan.id))  # fmt: skip
    return [_saved_out(r) for r in rows]


@router.get("/labels")
def labels():
    return LABELS
