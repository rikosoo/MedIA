import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_ACQ_READ, P_ACQ_WRITE, P_CLINIC_READ, P_SCHED_READ
from app.db.models import AcquisitionChannel, AcquisitionMetric, ClinicService, Leave
from app.db.session import get_db
from app.modules.goals.projection import month_str, shift_month
from finance_engine.funnel import (
    RECOMMENDATIONS,
    Funnel,
    bottleneck,
    combine,
    conversion_drop,
    funnel,
    required_leads,
)

router = APIRouter(prefix="/api/v1/acquisition", tags=["acquisition"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
CHANNEL_TYPES = "^(GOOGLE|REFERRAL|INSTAGRAM|SITE|SPONTANEOUS_RETURN|OTHER)$"
STAGE_LABELS = {"RESPONSE_TIME": "Resposta no prazo", "BOOKING": "Contato → agendamento",
                "ATTENDANCE": "Agendamento → comparecimento"}  # fmt: skip


def _month(v: str) -> date:
    return date(int(v[:4]), int(v[5:7]), 1)


# ---------- canais ----------


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    channel_type: str = Field(pattern=CHANNEL_TYPES)


class ChannelPatch(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=100)
    channel_type: str | None = Field(default=None, pattern=CHANNEL_TYPES)
    active: bool | None = None


def _ch_out(c: AcquisitionChannel) -> dict:
    return {"id": str(c.id), "name": c.name, "channel_type": c.channel_type, "active": c.active}


def _channel(db: Session, ctx: TenantContext, cid: uuid.UUID) -> AcquisitionChannel:
    c = db.scalar(select(AcquisitionChannel).where(AcquisitionChannel.id == cid,
                                                   AcquisitionChannel.organization_id == ctx.organization_id))  # fmt: skip
    if c is None:
        raise HTTPException(404, "Canal não encontrado")
    return c


@router.get("/channels")
def list_channels(include_inactive: bool = False, ctx: TenantContext = Depends(get_tenant),
                  db: Session = Depends(get_db)):  # fmt: skip
    ctx.require(P_ACQ_READ)
    q = select(AcquisitionChannel).where(AcquisitionChannel.organization_id == ctx.organization_id)
    if not include_inactive:
        q = q.where(AcquisitionChannel.active.is_(True))
    return [_ch_out(c) for c in db.scalars(q.order_by(AcquisitionChannel.name))]


@router.post("/channels", status_code=status.HTTP_201_CREATED)
def create_channel(
    body: ChannelIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_ACQ_WRITE)
    if db.scalar(select(AcquisitionChannel.id).where(AcquisitionChannel.organization_id == ctx.organization_id,
                                                     AcquisitionChannel.name == body.name)):  # fmt: skip
        raise HTTPException(409, "Já existe um canal com esse nome")
    c = AcquisitionChannel(
        organization_id=ctx.organization_id, created_by=ctx.user.id, **body.model_dump()
    )
    db.add(c)
    db.flush()
    audit(db, action="acq.channel_create", object_type="acquisition_channel", object_id=c.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return _ch_out(c)


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: uuid.UUID, body: ChannelPatch, ctx: TenantContext = Depends(get_tenant),
                   db: Session = Depends(get_db)):  # fmt: skip
    """Canais com histórico não são excluídos: são desativados (as métricas passadas continuam)."""
    ctx.require(P_ACQ_WRITE)
    c = _channel(db, ctx, channel_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != c.name and db.scalar(
        select(AcquisitionChannel.id).where(AcquisitionChannel.organization_id == ctx.organization_id,
                                            AcquisitionChannel.name == data["name"])):  # fmt: skip
        raise HTTPException(409, "Já existe um canal com esse nome")
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    return _ch_out(c)


# ---------- métricas mensais ----------


class MetricIn(BaseModel):
    month: str = Field(pattern=MONTH_RE)
    channel_id: uuid.UUID
    leads_count: int = Field(ge=0, le=10**7)
    responded_in_time_count: int = Field(default=0, ge=0, le=10**7)
    bookings_count: int = Field(default=0, ge=0, le=10**7)
    attended_count: int = Field(default=0, ge=0, le=10**7)
    spend_cents: int = Field(default=0, ge=0, le=10**12)


def _fun(m: AcquisitionMetric) -> Funnel:
    return funnel(
        m.leads_count, m.responded_in_time_count, m.bookings_count, m.attended_count, m.spend_cents
    )


def _f_out(f: Funnel) -> dict:
    return {
        "leads": f.leads, "responded_in_time": f.responded_in_time, "bookings": f.bookings,
        "attended": f.attended, "spend_cents": f.spend_cents, "response_rate_bps": f.response_rate_bps,
        "lead_to_booking_bps": f.lead_to_booking_bps, "booking_to_attendance_bps": f.booking_to_attendance_bps,
        "lead_to_attendance_bps": f.lead_to_attendance_bps, "cost_per_booking_cents": f.cost_per_booking_cents,
        "cost_per_attended_cents": f.cost_per_attended_cents,
    }  # fmt: skip


@router.put("/metrics")
def put_metric(
    body: MetricIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_ACQ_WRITE)
    ch = _channel(db, ctx, body.channel_id)
    try:
        f = funnel(body.leads_count, body.responded_in_time_count, body.bookings_count,
                   body.attended_count, body.spend_cents)  # fmt: skip
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    m = db.scalar(select(AcquisitionMetric).where(
        AcquisitionMetric.organization_id == ctx.organization_id, AcquisitionMetric.month == _month(body.month),
        AcquisitionMetric.channel_id == ch.id))  # fmt: skip
    if m is None:
        m = AcquisitionMetric(organization_id=ctx.organization_id, created_by=ctx.user.id,
                              month=_month(body.month), channel_id=ch.id)  # fmt: skip
        db.add(m)
    m.leads_count, m.responded_in_time_count = body.leads_count, body.responded_in_time_count
    m.bookings_count, m.attended_count, m.spend_cents = (
        body.bookings_count,
        body.attended_count,
        body.spend_cents,
    )
    db.flush()
    audit(db, action="acq.metric_put", object_type="acquisition_metric", object_id=m.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return {"id": str(m.id), "channel_id": str(ch.id), "month": body.month, **_f_out(f)}


def _metrics(db: Session, ctx: TenantContext, m: date) -> list[AcquisitionMetric]:
    return list(db.scalars(select(AcquisitionMetric).where(
        AcquisitionMetric.organization_id == ctx.organization_id, AcquisitionMetric.month == m)))  # fmt: skip


@router.get("/metrics")
def list_metrics(month: str = Query(pattern=MONTH_RE), ctx: TenantContext = Depends(get_tenant),
                 db: Session = Depends(get_db)):  # fmt: skip
    ctx.require(P_ACQ_READ)
    rows = {m.channel_id: m for m in _metrics(db, ctx, _month(month))}
    out = []
    for c in db.scalars(select(AcquisitionChannel).where(AcquisitionChannel.organization_id == ctx.organization_id)
                        .order_by(AcquisitionChannel.name)):  # fmt: skip
        m = rows.get(c.id)
        if m is None and not c.active:
            continue
        out.append({"channel": _ch_out(c), "has_data": m is not None,
                    **_f_out(_fun(m) if m else funnel(0, 0, 0, 0, 0))})  # fmt: skip
    return out


# ---------- cobertura da secretária (PRD §9.1) ----------


def secretary_coverage_bps(db: Session, ctx: TenantContext, m: date) -> tuple[int, list[dict]]:
    """Fração (bps) do atendimento de contatos que continua coberta no mês. Cada dia de férias da
    secretária perde (1 - cobertura) desse dia; sem férias, 10000."""
    nxt = shift_month(m, 1)
    last = nxt - timedelta(days=1)
    days_in_month = (nxt - m).days
    lost, details = 0, []
    for lv in db.scalars(select(Leave).where(
        Leave.organization_id == ctx.organization_id, Leave.staff_role == "SECRETARY",
        Leave.deleted_at.is_(None), Leave.start_date <= last, Leave.end_date >= m)):  # fmt: skip
        start, end = max(lv.start_date, m), min(lv.end_date, last)
        n = (end - start).days + 1
        lost += n * (10_000 - lv.coverage_factor_bps)
        details.append({"start": start.isoformat(), "end": end.isoformat(), "days": n,
                        "coverage_bps": lv.coverage_factor_bps})  # fmt: skip
    return max(10_000 - lost // days_in_month, 0), details


# ---------- funil, gargalo e planejamento ----------


def _service_revenue(db: Session, ctx: TenantContext, service_id: uuid.UUID | None):
    """Receita bruta esperada por paciente novo (com retornos), ou None sem permissão/serviço."""
    if not ctx.can(P_CLINIC_READ):
        return None, None
    from app.modules.clinic.router import _economics, _policy_of

    q = select(ClinicService).where(ClinicService.organization_id == ctx.organization_id,
                                    ClinicService.deleted_at.is_(None), ClinicService.status == "active")  # fmt: skip
    if service_id is not None:
        q = q.where(ClinicService.id == service_id)
    svcs = list(db.scalars(q.order_by(ClinicService.created_at)))
    if service_id is not None and not svcs:
        raise HTTPException(404, "Serviço não encontrado")
    if not svcs:
        return None, None
    vals = [
        _economics(s, _policy_of(db, ctx, s), s.price_cents)[0].gross_revenue_cents for s in svcs
    ]
    label = svcs[0].name if len(svcs) == 1 else f"média de {len(svcs)} serviços ativos"
    return sum(vals) // len(vals), label


@router.get("/funnel")
def funnel_view(
    month: str = Query(pattern=MONTH_RE),
    service_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_ACQ_READ)
    m = _month(month)
    rows = _metrics(db, ctx, m)
    names = {c.id: c for c in db.scalars(select(AcquisitionChannel).where(
        AcquisitionChannel.organization_id == ctx.organization_id))}  # fmt: skip
    total = combine([_fun(r) for r in rows]) if rows else funnel(0, 0, 0, 0, 0)
    by_channel = sorted(
        ({"channel": _ch_out(names[r.channel_id]), **_f_out(_fun(r))} for r in rows),
        key=lambda x: x["channel"]["name"],
    )

    history: list[Funnel] = []
    for i in (1, 2, 3):
        hm = _metrics(db, ctx, shift_month(m, -i))
        if hm:
            history.append(combine([_fun(r) for r in hm]))
    drop = conversion_drop(total, history) if rows else None
    stage = bottleneck(total)

    revenue, rev_label = _service_revenue(db, ctx, service_id)
    attribution = None
    if revenue is not None:
        for c in by_channel:
            attributed = c["attended"] * revenue
            c["attributed_revenue_cents"] = attributed
            c["roi_bps"] = (
                ((attributed - c["spend_cents"]) * 10_000 // c["spend_cents"])
                if c["spend_cents"]
                else None
            )
        attributed_total = total.attended * revenue
        attribution = {
            "revenue_per_attended_cents": revenue, "basis": rev_label,
            "attributed_revenue_cents": attributed_total, "spend_cents": total.spend_cents,
            "roi_bps": ((attributed_total - total.spend_cents) * 10_000 // total.spend_cents) if total.spend_cents else None,
            "note": "Receita atribuída = comparecimentos informados x receita bruta esperada por paciente "
                    "novo (inclui retornos). A origem de cada paciente é a que você informou.",
        }  # fmt: skip

    cov, cov_items = secretary_coverage_bps(db, ctx, m)
    messages = []
    if total.leads and total.leads < 30:
        messages.append("Poucos contatos no mês: as taxas variam muito e servem só como indício.")
    if cov < 10_000:
        messages.append(f"Férias da secretária reduzem a cobertura a {round(cov / 100)}% do mês; "
                        "cadastre a cobertura em Agenda & Capacidade.")  # fmt: skip
    if drop:
        messages.append(
            f"A conversão contato → agendamento caiu {round(drop / 100)}% em relação aos meses anteriores."
        )
    return {
        "month": month_str(m),
        "total": _f_out(total),
        "by_channel": by_channel,
        "bottleneck": None
        if stage is None
        else {
            "stage": stage,
            "label": STAGE_LABELS[stage],
            "recommendations": RECOMMENDATIONS[stage],
            "note": "É a etapa de menor taxa entre as três; não há referência de mercado.",
        },  # fmt: skip
        "conversion_drop_bps": drop,
        "history_months": len(history),
        "attribution": attribution,
        "secretary_coverage_bps": cov,
        "secretary_leaves": cov_items,
        "messages": messages,
        "has_data": bool(rows),
    }


@router.get("/plan")
def plan(
    month: str = Query(pattern=MONTH_RE),
    target_new_patients: int = Query(ge=0, le=100_000),
    service_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Quantos contatos são necessários para `target_new_patients` comparecimentos, com as taxas dos
    últimos 3 meses + o atual, e se a agenda comporta esses pacientes antes de recomendar captação."""
    ctx.require(P_ACQ_READ)
    m = _month(month)
    fs = []
    for i in (0, 1, 2, 3):
        rows = _metrics(db, ctx, shift_month(m, -i))
        if rows:
            fs.append(combine([_fun(r) for r in rows]))
    if not fs:
        return {"enough_data": False, "message": "Registre ao menos um mês de métricas para estimar.",
                "target_new_patients": target_new_patients}  # fmt: skip
    base = combine(fs)
    need = required_leads(target_new_patients, base)
    out = {
        "target_new_patients": target_new_patients,
        "months_used": len(fs),
        "bookings_needed": need.bookings,
        "leads_needed": need.leads,
        "enough_data": need.enough_data,
        "rates": {
            "lead_to_booking_bps": base.lead_to_booking_bps,
            "booking_to_attendance_bps": base.booking_to_attendance_bps,
        },  # fmt: skip
        "capacity": None,
        "messages": [],
    }
    if need.leads is None:
        out["messages"].append(
            "Sem agendamentos ou comparecimentos no histórico: não dá para estimar."
        )
    elif not need.enough_data:
        out["messages"].append("Histórico com menos de 30 contatos: estimativa frágil.")
    if ctx.can(P_SCHED_READ) and ctx.can(P_CLINIC_READ):
        from app.modules.schedule.router import capacity

        sid = service_id
        if sid is None:
            first = db.scalar(select(ClinicService.id).where(
                ClinicService.organization_id == ctx.organization_id, ClinicService.deleted_at.is_(None),
                ClinicService.status == "active").order_by(ClinicService.created_at))  # fmt: skip
            sid = first
        if sid is not None:
            cap = capacity(
                month=month_str(m), user_id=None, slot_minutes=None, service_id=sid, ctx=ctx, db=db
            )
            room = cap["service"]["new_patients_remaining"]
            out["capacity"] = {
                "new_patients_remaining": room,
                "remaining_minutes": cap["remaining_minutes"],
            }
            if room <= 0:
                out["messages"].append(
                    "A agenda do mês está cheia: captar mais contatos não resolve agora."
                )
            elif target_new_patients > room:
                out["messages"].append(f"A agenda comporta só {room} paciente(s) novo(s) no mês; a meta de "
                                       f"{target_new_patients} exige abrir horários.")  # fmt: skip
    return out
