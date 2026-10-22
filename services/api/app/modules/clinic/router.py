import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_CLINIC_READ, P_CLINIC_WRITE
from app.db.base import utcnow
from app.db.models import ClinicService, ReturnPolicy
from app.db.session import get_db
from finance_engine import (
    ConsultEconomics,
    capacity_new_patients,
    consult_economics,
    resolve_return_minutes,
    resolve_return_price_cents,
    return_load,
    returns_by_month,
)

router = APIRouter(prefix="/api/v1", tags=["clinic"])

MAX_MIN = 24 * 60


# ---------- Políticas de retorno ----------


class ReturnPolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    occurrence_rate_bps: int = Field(ge=0, le=10_000)
    delay_days: int = Field(ge=0, le=365)
    duration_mode: str = Field(pattern="^(MINUTES|PERCENT)$")
    duration_minutes: int | None = Field(default=None, ge=0, le=MAX_MIN)
    duration_percent_bps: int | None = Field(default=None, ge=0, le=20_000)
    price_mode: str = Field(pattern="^(FREE|PARTIAL|FULL|CUSTOM)$")
    price_percent_bps: int | None = Field(default=None, ge=0, le=10_000)
    price_cents: int | None = Field(default=None, ge=0, le=10**12)
    free_within_days: int | None = Field(default=None, ge=0, le=365)
    no_show_rate_bps: int = Field(default=0, ge=0, le=10_000)
    cost_cents: int = Field(default=0, ge=0, le=10**12)

    @model_validator(mode="after")
    def _coherent(self):
        if self.duration_mode == "MINUTES" and self.duration_minutes is None:
            raise ValueError("duration_minutes é obrigatório no modo MINUTES")
        if self.duration_mode == "PERCENT" and self.duration_percent_bps is None:
            raise ValueError("duration_percent_bps é obrigatório no modo PERCENT")
        if self.price_mode == "PARTIAL" and self.price_percent_bps is None:
            raise ValueError("price_percent_bps é obrigatório no modo PARTIAL")
        if self.price_mode == "CUSTOM" and self.price_cents is None:
            raise ValueError("price_cents é obrigatório no modo CUSTOM")
        return self


class ReturnPolicyPatch(BaseModel):
    """Qualquer campo de ReturnPolicyIn; o resultado mesclado é revalidado por inteiro."""

    model_config = {"extra": "forbid"}
    name: str | None = None
    occurrence_rate_bps: int | None = None
    delay_days: int | None = None
    duration_mode: str | None = None
    duration_minutes: int | None = None
    duration_percent_bps: int | None = None
    price_mode: str | None = None
    price_percent_bps: int | None = None
    price_cents: int | None = None
    free_within_days: int | None = None
    no_show_rate_bps: int | None = None
    cost_cents: int | None = None


POLICY_FIELDS = list(ReturnPolicyIn.model_fields)


def _policy_out(p: ReturnPolicy) -> dict:
    return {"id": str(p.id), **{f: getattr(p, f) for f in POLICY_FIELDS}}


def _policy_q(ctx: TenantContext):
    return select(ReturnPolicy).where(
        ReturnPolicy.organization_id == ctx.organization_id, ReturnPolicy.deleted_at.is_(None)
    )


def _get_policy(db: Session, ctx: TenantContext, pid: uuid.UUID) -> ReturnPolicy:
    p = db.scalar(_policy_q(ctx).where(ReturnPolicy.id == pid))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Política de retorno não encontrada")
    return p


@router.get("/return-policies")
def list_policies(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_CLINIC_READ)
    return [_policy_out(p) for p in db.scalars(_policy_q(ctx).order_by(ReturnPolicy.created_at))]


@router.post("/return-policies", status_code=status.HTTP_201_CREATED)
def create_policy(
    body: ReturnPolicyIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_CLINIC_WRITE)
    p = ReturnPolicy(
        organization_id=ctx.organization_id, created_by=ctx.user.id, **body.model_dump()
    )
    db.add(p)
    db.flush()
    audit(
        db,
        action="clinic.policy_create",
        object_type="return_policy",
        object_id=p.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _policy_out(p)


@router.patch("/return-policies/{policy_id}")
def update_policy(
    policy_id: uuid.UUID,
    body: ReturnPolicyPatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_CLINIC_WRITE)
    p = _get_policy(db, ctx, policy_id)
    merged = {f: getattr(p, f) for f in POLICY_FIELDS} | body.model_dump(exclude_unset=True)
    try:
        valid = ReturnPolicyIn(**merged)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from None
    for k, v in valid.model_dump().items():
        setattr(p, k, v)
    audit(
        db,
        action="clinic.policy_update",
        object_type="return_policy",
        object_id=p.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _policy_out(p)


@router.delete("/return-policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_policy(
    policy_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_CLINIC_WRITE)
    p = _get_policy(db, ctx, policy_id)
    in_use = db.scalar(
        select(ClinicService.id).where(
            ClinicService.return_policy_id == p.id, ClinicService.deleted_at.is_(None)
        )
    )
    if in_use:
        raise HTTPException(status.HTTP_409_CONFLICT, "Política em uso por um serviço")
    p.deleted_at = utcnow()
    audit(
        db,
        action="clinic.policy_delete",
        object_type="return_policy",
        object_id=p.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Serviços ----------


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0, le=10**12)
    duration_minutes: int = Field(gt=0, le=MAX_MIN)
    cost_cents: int = Field(default=0, ge=0, le=10**12)
    fee_bps: int = Field(default=0, ge=0, le=10_000)
    no_show_rate_bps: int = Field(default=0, ge=0, le=10_000)
    return_policy_id: uuid.UUID | None = None


class ServicePatch(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_cents: int | None = Field(default=None, ge=0, le=10**12)
    duration_minutes: int | None = Field(default=None, gt=0, le=MAX_MIN)
    cost_cents: int | None = Field(default=None, ge=0, le=10**12)
    fee_bps: int | None = Field(default=None, ge=0, le=10_000)
    no_show_rate_bps: int | None = Field(default=None, ge=0, le=10_000)
    return_policy_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern="^(active|archived)$")


def _service_out(s: ClinicService) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "price_cents": s.price_cents,
        "duration_minutes": s.duration_minutes,
        "cost_cents": s.cost_cents,
        "fee_bps": s.fee_bps,
        "no_show_rate_bps": s.no_show_rate_bps,
        "return_policy_id": str(s.return_policy_id) if s.return_policy_id else None,
        "status": s.status,
    }


def _service_q(ctx: TenantContext):
    return select(ClinicService).where(
        ClinicService.organization_id == ctx.organization_id, ClinicService.deleted_at.is_(None)
    )


def _get_service(db: Session, ctx: TenantContext, sid: uuid.UUID) -> ClinicService:
    s = db.scalar(_service_q(ctx).where(ClinicService.id == sid))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Serviço não encontrado")
    return s


def _check_policy_ref(db: Session, ctx: TenantContext, pid: uuid.UUID | None) -> None:
    # Mesma mensagem para inexistente e de outra organização: não vaza existência.
    if pid is not None and db.scalar(_policy_q(ctx).where(ReturnPolicy.id == pid)) is None:
        raise HTTPException(422, "Política de retorno inválida")


@router.get("/clinic-services")
def list_services(
    include_archived: bool = False,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_CLINIC_READ)
    q = _service_q(ctx).order_by(ClinicService.created_at)
    if not include_archived:
        q = q.where(ClinicService.status == "active")
    return [_service_out(s) for s in db.scalars(q)]


@router.post("/clinic-services", status_code=status.HTTP_201_CREATED)
def create_service(
    body: ServiceIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_CLINIC_WRITE)
    _check_policy_ref(db, ctx, body.return_policy_id)
    s = ClinicService(
        organization_id=ctx.organization_id, created_by=ctx.user.id, **body.model_dump()
    )
    db.add(s)
    db.flush()
    audit(
        db,
        action="clinic.service_create",
        object_type="clinic_service",
        object_id=s.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _service_out(s)


@router.get("/clinic-services/{service_id}")
def get_service(
    service_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_CLINIC_READ)
    return _service_out(_get_service(db, ctx, service_id))


@router.patch("/clinic-services/{service_id}")
def update_service(
    service_id: uuid.UUID,
    body: ServicePatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_CLINIC_WRITE)
    s = _get_service(db, ctx, service_id)
    data = body.model_dump(exclude_unset=True)
    if "return_policy_id" in data:
        _check_policy_ref(db, ctx, data["return_policy_id"])
    for k, v in data.items():
        setattr(s, k, v)
    audit(
        db,
        action="clinic.service_update",
        object_type="clinic_service",
        object_id=s.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _service_out(s)


@router.delete("/clinic-services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_CLINIC_WRITE)
    s = _get_service(db, ctx, service_id)
    s.deleted_at = utcnow()
    audit(
        db,
        action="clinic.service_delete",
        object_type="clinic_service",
        object_id=s.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Economia, capacidade e agenda de retornos ----------


def _fmt_x10(x10: int) -> str:
    return f"{x10 // 10}.{x10 % 10}"


def _policy_of(db: Session, ctx: TenantContext, s: ClinicService) -> ReturnPolicy | None:
    if s.return_policy_id is None:
        return None
    return db.scalar(_policy_q(ctx).where(ReturnPolicy.id == s.return_policy_id))


def _return_params(s: ClinicService, p: ReturnPolicy | None, price_cents: int) -> dict:
    if p is None:
        return dict(
            return_rate_bps=0, return_minutes=0, return_price_cents=0, return_no_show_bps=0,
            return_cost_cents=0, delay_days=0,
        )  # fmt: skip
    return dict(
        return_rate_bps=p.occurrence_rate_bps,
        return_minutes=resolve_return_minutes(
            s.duration_minutes, p.duration_mode, p.duration_minutes, p.duration_percent_bps
        ),
        return_price_cents=resolve_return_price_cents(
            price_cents,
            p.price_mode,
            p.delay_days,
            p.price_percent_bps,
            p.price_cents,
            p.free_within_days,
        ),
        return_no_show_bps=p.no_show_rate_bps,
        return_cost_cents=p.cost_cents,
        delay_days=p.delay_days,
    )


def _economics(
    s: ClinicService, p: ReturnPolicy | None, price_cents: int
) -> tuple[ConsultEconomics, dict]:
    rp = _return_params(s, p, price_cents)
    e = consult_economics(
        price_cents=price_cents,
        consult_minutes=s.duration_minutes,
        return_rate_bps=rp["return_rate_bps"],
        return_minutes=rp["return_minutes"],
        return_price_cents=rp["return_price_cents"],
        new_no_show_bps=s.no_show_rate_bps,
        return_no_show_bps=rp["return_no_show_bps"],
        fee_bps=s.fee_bps,
        cost_cents=s.cost_cents,
        return_cost_cents=rp["return_cost_cents"],
    )
    return e, rp


def _econ_out(e: ConsultEconomics, rp: dict, price_cents: int) -> dict:
    return {
        "price_cents": price_cents,
        "return_price_cents": rp["return_price_cents"],
        "return_minutes": rp["return_minutes"],
        "expected_returns_bps": e.expected_returns_bps,
        "expected_minutes": _fmt_x10(e.minutes_x10),
        "expected_minutes_x10": e.minutes_x10,
        "revenue_new_cents": e.revenue_new_cents,
        "revenue_returns_cents": e.revenue_returns_cents,
        "gross_revenue_cents": e.gross_revenue_cents,
        "fee_cents": e.fee_cents,
        "variable_cost_cents": e.variable_cost_cents,
        "margin_cents": e.margin_cents,
        "margin_per_hour_cents": e.margin_per_hour_cents,
    }


@router.get("/clinic-services/{service_id}/economics")
def service_economics(
    service_id: uuid.UUID,
    price_cents: int | None = Query(default=None, ge=0, le=10**12),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Por consulta nova agendada. Com `price_cents`, compara o preço atual com o simulado."""
    ctx.require(P_CLINIC_READ)
    s = _get_service(db, ctx, service_id)
    p = _policy_of(db, ctx, s)
    cur, rp = _economics(s, p, s.price_cents)
    out = {
        "service_id": str(s.id),
        "current": _econ_out(cur, rp, s.price_cents),
        "simulated": None,
        "delta_margin_cents": None,
        "assumptions": [
            "Por consulta nova agendada, com retornos ocorrendo no horizonte analisado.",
            "Margem antes de tributos e de despesas fixas.",
            "Simulação de preço assume demanda constante (elasticidade não modelada).",
        ],
    }
    if price_cents is not None:
        sim, rp2 = _economics(s, p, price_cents)
        out["simulated"] = _econ_out(sim, rp2, price_cents)
        out["delta_margin_cents"] = sim.margin_cents - cur.margin_cents
    return out


class CapacityIn(BaseModel):
    service_id: uuid.UUID
    available_minutes: int = Field(ge=0, le=60 * 24 * 31)


@router.post("/clinic/capacity")
def capacity(
    body: CapacityIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_CLINIC_READ)
    s = _get_service(db, ctx, body.service_id)
    e, _ = _economics(s, _policy_of(db, ctx, s), s.price_cents)
    return {
        "capacity_new_patients": capacity_new_patients(body.available_minutes, e.minutes_x10),
        "expected_minutes_per_new_patient": _fmt_x10(e.minutes_x10),
        "is_approximation": True,  # o motor de calendário (Sprint 3) conta slots reais
    }


class ConsultDay(BaseModel):
    date: date
    count: int = Field(ge=0, le=1000)


class ReturnScheduleIn(BaseModel):
    service_id: uuid.UUID
    consults: list[ConsultDay] = Field(max_length=500)


@router.post("/clinic/return-schedule")
def return_schedule(
    body: ReturnScheduleIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Em que mês cada retorno esperado ocupa a agenda (e quanto rende nesse mês)."""
    ctx.require(P_CLINIC_READ)
    s = _get_service(db, ctx, body.service_id)
    p = _policy_of(db, ctx, s)
    e, rp = _economics(s, p, s.price_cents)
    by_month = returns_by_month(
        [(c.date, c.count) for c in body.consults], e.expected_returns_bps, rp["delay_days"]
    )
    months = []
    for m, bps in by_month.items():
        minutes_x10, revenue = return_load(
            bps, rp["return_minutes"], rp["return_price_cents"], rp["return_no_show_bps"]
        )
        months.append(
            {
                "month": m.strftime("%Y-%m"),
                "expected_returns": f"{bps // 10_000}.{(bps % 10_000) // 100:02d}",
                "minutes": _fmt_x10(minutes_x10),
                "revenue_cents": revenue,
            }
        )
    return {
        "months": months,
        "note": "A receita da consulta original permanece no mês em que ela ocorre; "
        "aqui aparece só o que o retorno ocupa e rende no mês em que acontece.",
    }
