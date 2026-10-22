import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_WORK_READ_ALL, P_WORK_READ_OWN, P_WORK_WRITE
from app.db.base import utcnow
from app.db.models import IncomeRule, Regime, UnitType, WorkSource, WorkType
from app.db.session import get_db
from finance_engine import per_hour_cents

router = APIRouter(prefix="/api/v1/work-sources", tags=["work-sources"])

MAX_MINUTES = 7 * 24 * 60


class IncomeRuleIn(BaseModel):
    gross_cents: int = Field(ge=0, le=10**12)
    unit_type: UnitType
    duration_minutes: int = Field(default=0, ge=0, le=MAX_MINUTES)
    commitment_minutes: int = Field(default=0, ge=0, le=MAX_MINUTES)
    travel_minutes: int = Field(default=0, ge=0, le=MAX_MINUTES)
    variable_cost_cents: int = Field(default=0, ge=0, le=10**12)
    tax_rate_bps: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def _commitment_covers_duration(self):
        # Hora comprometida inclui a ativa; se omitida, assume ativa + deslocamento.
        if self.commitment_minutes == 0:
            self.commitment_minutes = self.duration_minutes + self.travel_minutes
        elif self.commitment_minutes < self.duration_minutes:
            raise ValueError("commitment_minutes não pode ser menor que duration_minutes")
        return self


class WorkSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    work_type: WorkType
    regime: Regime
    payer_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    income: IncomeRuleIn


class WorkSourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    work_type: WorkType | None = None
    regime: Regime | None = None
    payer_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(active|archived)$")
    income: IncomeRuleIn | None = None


class WorkSourceOut(BaseModel):
    id: str
    owner_user_id: str
    name: str
    work_type: str
    regime: str
    payer_name: str | None
    notes: str | None
    status: str
    income: dict
    tax_is_estimate: bool = True  # PRD 7.5: nunca apresentar como valor definitivo


def _out(w: WorkSource) -> WorkSourceOut:
    r = w.income_rule
    net_cents = r.gross_cents - r.variable_cost_cents - (r.gross_cents * r.tax_rate_bps) // 10_000
    return WorkSourceOut(
        id=str(w.id),
        owner_user_id=str(w.owner_user_id),
        name=w.name,
        work_type=w.work_type,
        regime=w.regime,
        payer_name=w.payer_name,
        notes=w.notes,
        status=w.status,
        income={
            "gross_cents": r.gross_cents,
            "unit_type": r.unit_type,
            "duration_minutes": r.duration_minutes,
            "commitment_minutes": r.commitment_minutes,
            "travel_minutes": r.travel_minutes,
            "variable_cost_cents": r.variable_cost_cents,
            "tax_rate_bps": r.tax_rate_bps,
            "estimated_net_cents": net_cents,
            "net_per_active_hour_cents": per_hour_cents(net_cents, r.duration_minutes),
            "net_per_committed_hour_cents": per_hour_cents(net_cents, r.commitment_minutes),
        },
    )


def _visible(ctx: TenantContext):
    """Filtro de visibilidade: sempre por organização; sem READ_ALL, só os próprios."""
    q = select(WorkSource).where(
        WorkSource.organization_id == ctx.organization_id, WorkSource.deleted_at.is_(None)
    )
    if not ctx.can(P_WORK_READ_ALL):
        q = q.where(WorkSource.owner_user_id == ctx.user.id)
    return q


def _get(db: Session, ctx: TenantContext, work_id: uuid.UUID) -> WorkSource:
    if not (ctx.can(P_WORK_READ_OWN) or ctx.can(P_WORK_READ_ALL)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permissão insuficiente")
    w = db.scalar(_visible(ctx).where(WorkSource.id == work_id))
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trabalho não encontrado")
    return w


@router.get("", response_model=list[WorkSourceOut])
def list_work(
    include_archived: bool = False,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
) -> list[WorkSourceOut]:
    if not (ctx.can(P_WORK_READ_OWN) or ctx.can(P_WORK_READ_ALL)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permissão insuficiente")
    q = _visible(ctx).order_by(WorkSource.created_at, WorkSource.id)
    if not include_archived:
        q = q.where(WorkSource.status == "active")
    return [_out(w) for w in db.scalars(q).all()]


@router.post("", response_model=WorkSourceOut, status_code=status.HTTP_201_CREATED)
def create_work(
    body: WorkSourceIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
) -> WorkSourceOut:
    ctx.require(P_WORK_WRITE)
    w = WorkSource(
        organization_id=ctx.organization_id,
        owner_user_id=ctx.user.id,
        created_by=ctx.user.id,
        name=body.name,
        work_type=body.work_type,
        regime=body.regime,
        payer_name=body.payer_name,
        notes=body.notes,
        income_rule=IncomeRule(organization_id=ctx.organization_id, **body.income.model_dump()),
    )
    db.add(w)
    db.flush()
    audit(
        db,
        action="work.create",
        object_type="work_source",
        object_id=w.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _out(w)


@router.get("/{work_id}", response_model=WorkSourceOut)
def get_work(
    work_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
) -> WorkSourceOut:
    return _out(_get(db, ctx, work_id))


@router.patch("/{work_id}", response_model=WorkSourceOut)
def update_work(
    work_id: uuid.UUID,
    body: WorkSourcePatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
) -> WorkSourceOut:
    ctx.require(P_WORK_WRITE)
    w = _get(db, ctx, work_id)
    data = body.model_dump(exclude_unset=True, exclude={"income"})
    for key, value in data.items():
        setattr(w, key, value)
    if body.income is not None:
        for key, value in body.income.model_dump().items():
            setattr(w.income_rule, key, value)
    audit(
        db,
        action="work.update",
        object_type="work_source",
        object_id=w.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _out(w)


@router.delete("/{work_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_work(
    work_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
) -> Response:
    ctx.require(P_WORK_WRITE)
    w = _get(db, ctx, work_id)
    w.deleted_at = utcnow()  # soft-delete (PRD 11)
    audit(
        db,
        action="work.delete",
        object_type="work_source",
        object_id=w.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
