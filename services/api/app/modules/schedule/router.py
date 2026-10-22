import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import (
    P_SCHED_ALL,
    P_SCHED_READ,
    P_SCHED_WRITE,
    P_WORK_READ_ALL,
    P_WORK_READ_OWN,
)
from app.db.base import utcnow
from app.db.models import (
    AvailabilitySlot,
    Holiday,
    Leave,
    Membership,
    Organization,
    Role,
    ScheduleEvent,
    TimeConstraint,
    WorkSource,
)
from app.db.session import get_db
from app.modules.clinic.router import _economics, _get_service, _policy_of
from finance_engine import national_holidays
from finance_engine.schedule import (
    ACTIVE_TYPES,
    COMMITTED_TYPES,
    CONSULT_TYPES,
    EVENT_TYPES,
    Event,
    Interval,
    clip,
    count_slots,
    find_conflicts,
    is_night,
    local_day,
    local_month,
    merge,
    rest_violations,
    subtract,
    total_minutes,
)

router = APIRouter(prefix="/api/v1", tags=["schedule"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
MAX_EVENT_MINUTES = 7 * 24 * 60
MAX_RANGE_DAYS = 62


# ---------- helpers ----------


def _month(value: str) -> date:
    y, m = value.split("-")
    return date(int(y), int(m), 1)


def _tz(db: Session, ctx: TenantContext) -> ZoneInfo:
    return ZoneInfo(db.get(Organization, ctx.organization_id).timezone)


def _target_user(db: Session, ctx: TenantContext, user_id: uuid.UUID | None) -> uuid.UUID:
    """Agenda de outra pessoa só com schedule:all; e a pessoa precisa ser membro da org."""
    if user_id is None or user_id == ctx.user.id:
        return ctx.user.id
    ctx.require(P_SCHED_ALL)
    member = db.scalar(
        select(Membership.id).where(
            Membership.user_id == user_id, Membership.organization_id == ctx.organization_id
        )
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pessoa não encontrada")
    return user_id


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(ZoneInfo("UTC"))


def _aware(dt: datetime) -> datetime:
    """SQLite devolve naive; o banco guarda UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))


def _constraint(db: Session, ctx: TenantContext, user_id: uuid.UUID, month: date):
    rows = list(
        db.scalars(
            select(TimeConstraint).where(
                TimeConstraint.organization_id == ctx.organization_id,
                TimeConstraint.user_id == user_id,
                (TimeConstraint.month == month) | (TimeConstraint.month.is_(None)),
            )
        )
    )
    specific = next((r for r in rows if r.month == month), None)
    return specific or next((r for r in rows if r.month is None), None)


def _events_q(ctx: TenantContext, user_id: uuid.UUID):
    return select(ScheduleEvent).where(
        ScheduleEvent.organization_id == ctx.organization_id,
        ScheduleEvent.user_id == user_id,
        ScheduleEvent.deleted_at.is_(None),
        ScheduleEvent.status != "CANCELLED",
    )


def _effective_recovery(e: ScheduleEvent, tc: TimeConstraint | None) -> int:
    if e.recovery_minutes is not None:
        return e.recovery_minutes
    return tc.min_rest_minutes if (tc and e.type == "SHIFT") else 0


def _engine_event(e: ScheduleEvent, tc: TimeConstraint | None) -> Event:
    return Event(
        str(e.id), e.type, _aware(e.starts_at), _aware(e.ends_at), e.allow_overlap,
        _effective_recovery(e, tc),
    )  # fmt: skip


def _closures(
    db: Session, ctx: TenantContext, user_id: uuid.UUID, window: Interval, tz: ZoneInfo,
    tc: TimeConstraint | None,
) -> tuple[list[tuple[date, str]], list[Interval]]:  # fmt: skip
    """Dias fechados (data, motivo): feriado confirmado, férias do médico e folgas."""
    first, last = (
        window.start.astimezone(tz).date(),
        (window.end - timedelta(seconds=1)).astimezone(tz).date(),
    )
    days: dict[date, str] = {}
    for h in db.scalars(
        select(Holiday).where(
            Holiday.organization_id == ctx.organization_id,
            Holiday.confirmed.is_(True),
            Holiday.date >= first,
            Holiday.date <= last,
        )
    ):
        days[h.date] = f"Feriado: {h.name}"
    for lv in db.scalars(
        select(Leave).where(
            Leave.organization_id == ctx.organization_id,
            Leave.staff_role == "PHYSICIAN",
            Leave.user_id == user_id,
            Leave.deleted_at.is_(None),
            Leave.start_date <= last,
            Leave.end_date >= first,
        )
    ):
        d = max(lv.start_date, first)
        while d <= min(lv.end_date, last):
            days[d] = "Férias/licença"
            d += timedelta(days=1)
    for raw in (tc.days_off if tc else []) or []:
        d = date.fromisoformat(raw)
        if first <= d <= last:
            days.setdefault(d, "Folga")
    ordered = sorted(days.items())
    return ordered, [local_day(d, tz) for d, _ in ordered]


# ---------- pessoas ----------


@router.get("/schedule/people")
def people(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    """Quem tem agenda: médicos e donos. Visível para quem opera a agenda (ex.: secretária)."""
    ctx.require(P_SCHED_READ)
    rows = db.execute(
        select(Membership).where(
            Membership.organization_id == ctx.organization_id,
            Membership.role.in_([Role.OWNER, Role.PHYSICIAN]),
        )
    ).scalars()
    people_ = [{"user_id": str(m.user_id), "name": m.user.name, "role": m.role} for m in rows]
    if not ctx.can(P_SCHED_ALL):
        people_ = [p for p in people_ if p["user_id"] == str(ctx.user.id)]
    return people_


# ---------- disponibilidade ----------


class SlotIn(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _ok(self):
        if not (timedelta(0) < self.ends_at - self.starts_at <= timedelta(days=1)):
            raise ValueError("a janela deve ter entre 1 minuto e 24 horas")
        return self


class WeeklyIn(BaseModel):
    weekdays: list[int] = Field(min_length=1, max_length=7)  # 0 = segunda
    start_time: time
    end_time: time
    from_date: date
    to_date: date
    user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _ok(self):
        if any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("dia da semana deve estar entre 0 (segunda) e 6 (domingo)")
        if self.end_time <= self.start_time:
            raise ValueError("horário final deve ser maior que o inicial")
        if self.to_date < self.from_date or (self.to_date - self.from_date).days > 366:
            raise ValueError("período inválido (máximo de 366 dias)")
        return self


def _slot_out(s: AvailabilitySlot, tz: ZoneInfo) -> dict:
    return {
        "id": str(s.id),
        "user_id": str(s.user_id),
        "starts_at": _aware(s.starts_at).astimezone(tz).isoformat(),
        "ends_at": _aware(s.ends_at).astimezone(tz).isoformat(),
        "minutes": int((_aware(s.ends_at) - _aware(s.starts_at)).total_seconds() // 60),
    }


def _range(from_: date, to: date, tz: ZoneInfo) -> Interval:
    if to < from_ or (to - from_).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"Período inválido (máximo de {MAX_RANGE_DAYS} dias)")
    return Interval(local_day(from_, tz).start, local_day(to, tz).end)


@router.get("/availability")
def list_availability(
    from_: date = Query(alias="from"),
    to: date = Query(),
    user_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_SCHED_READ)
    uid, tz = _target_user(db, ctx, user_id), _tz(db, ctx)
    w = _range(from_, to, tz)
    rows = db.scalars(
        select(AvailabilitySlot)
        .where(
            AvailabilitySlot.organization_id == ctx.organization_id,
            AvailabilitySlot.user_id == uid,
            AvailabilitySlot.starts_at < _utc(w.end),
            AvailabilitySlot.ends_at > _utc(w.start),
        )
        .order_by(AvailabilitySlot.starts_at)
    )
    return [_slot_out(s, tz) for s in rows]


@router.post("/availability", status_code=status.HTTP_201_CREATED)
def create_availability(
    body: SlotIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    uid = _target_user(db, ctx, body.user_id)
    s = AvailabilitySlot(
        organization_id=ctx.organization_id, user_id=uid, created_by=ctx.user.id,
        starts_at=_utc(body.starts_at), ends_at=_utc(body.ends_at),
    )  # fmt: skip
    db.add(s)
    db.flush()
    audit(db, action="schedule.slot_create", object_type="availability_slot", object_id=s.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return _slot_out(s, _tz(db, ctx))


@router.post("/availability/weekly", status_code=status.HTTP_201_CREATED)
def create_weekly(
    body: WeeklyIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Repete um turno semanal (horário local da organização). Pula janelas já cadastradas."""
    ctx.require(P_SCHED_WRITE)
    uid, tz = _target_user(db, ctx, body.user_id), _tz(db, ctx)
    existing = {
        (_aware(s.starts_at), _aware(s.ends_at))
        for s in db.scalars(
            select(AvailabilitySlot).where(
                AvailabilitySlot.organization_id == ctx.organization_id,
                AvailabilitySlot.user_id == uid,
                AvailabilitySlot.starts_at >= _utc(local_day(body.from_date, tz).start),
                AvailabilitySlot.starts_at < _utc(local_day(body.to_date, tz).end),
            )
        )
    }
    created = skipped = 0
    d = body.from_date
    while d <= body.to_date:
        if d.weekday() in body.weekdays:
            a = datetime.combine(d, body.start_time, tzinfo=tz)
            b = datetime.combine(d, body.end_time, tzinfo=tz)
            key = (_utc(a), _utc(b))
            if key in existing:
                skipped += 1
            else:
                db.add(
                    AvailabilitySlot(
                        organization_id=ctx.organization_id,
                        user_id=uid,
                        created_by=ctx.user.id,
                        starts_at=key[0],
                        ends_at=key[1],
                    )
                )
                created += 1
        d += timedelta(days=1)
    audit(db, action="schedule.weekly_create", object_type="availability_slot",
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"created": created})  # fmt: skip
    db.commit()
    return {"created": created, "skipped_existing": skipped}


@router.delete("/availability/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_availability(
    slot_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    s = db.scalar(
        select(AvailabilitySlot).where(
            AvailabilitySlot.id == slot_id, AvailabilitySlot.organization_id == ctx.organization_id
        )
    )
    if s is None or (s.user_id != ctx.user.id and not ctx.can(P_SCHED_ALL)):
        raise HTTPException(404, "Janela não encontrada")
    db.delete(s)
    audit(db, action="schedule.slot_delete", object_type="availability_slot", object_id=s.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- eventos ----------


class EventIn(BaseModel):
    type: str
    title: str = Field(default="", max_length=200)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: str = Field(default="PLANNED", pattern="^(PLANNED|DONE)$")
    allow_overlap: bool = False
    recovery_minutes: int | None = Field(default=None, ge=0, le=MAX_EVENT_MINUTES)
    work_source_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    allow_conflict: bool = False

    @model_validator(mode="after")
    def _ok(self):
        if self.type not in EVENT_TYPES:
            raise ValueError("tipo de evento inválido")
        if not (
            timedelta(0) < self.ends_at - self.starts_at <= timedelta(minutes=MAX_EVENT_MINUTES)
        ):
            raise ValueError("duração deve ser positiva e de até 7 dias")
        if self.allow_overlap and self.type != "ON_CALL":
            raise ValueError("allow_overlap só vale para sobreaviso")
        return self


class EventPatch(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, max_length=200)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    status: str | None = Field(default=None, pattern="^(PLANNED|DONE|CANCELLED|NO_SHOW)$")
    allow_overlap: bool | None = None
    recovery_minutes: int | None = Field(default=None, ge=0, le=MAX_EVENT_MINUTES)
    allow_conflict: bool = False


def _event_out(e: ScheduleEvent, tz: ZoneInfo, show_work: bool) -> dict:
    return {
        "id": str(e.id),
        "user_id": str(e.user_id),
        "type": e.type,
        "title": e.title,
        "starts_at": _aware(e.starts_at).astimezone(tz).isoformat(),
        "ends_at": _aware(e.ends_at).astimezone(tz).isoformat(),
        "status": e.status,
        "allow_overlap": e.allow_overlap,
        "recovery_minutes": e.recovery_minutes,
        # Vínculo com um trabalho revela renda: só para quem pode ver rendas.
        "work_source_id": str(e.work_source_id) if (show_work and e.work_source_id) else None,
    }


def _can_see_work(ctx: TenantContext) -> bool:
    return ctx.can(P_WORK_READ_OWN) or ctx.can(P_WORK_READ_ALL)


def _check_work_ref(db: Session, ctx: TenantContext, wid: uuid.UUID | None) -> None:
    if wid is None:
        return
    q = select(WorkSource.id).where(
        WorkSource.id == wid,
        WorkSource.organization_id == ctx.organization_id,
        WorkSource.deleted_at.is_(None),
    )
    if not ctx.can(P_WORK_READ_ALL):
        q = q.where(WorkSource.owner_user_id == ctx.user.id)
    if not _can_see_work(ctx) or db.scalar(q) is None:
        raise HTTPException(422, "Trabalho inválido")


def _assess(
    db: Session, ctx: TenantContext, uid: uuid.UUID, tz: ZoneInfo, cand: Event,
    recovery: int | None, exclude: uuid.UUID | None = None,
) -> dict:  # fmt: skip
    """Conflitos, descanso e dias fechados envolvendo o evento candidato."""
    pad = timedelta(days=8)
    q = _events_q(ctx, uid).where(
        ScheduleEvent.starts_at < _utc(cand.end + pad),
        ScheduleEvent.ends_at > _utc(cand.start - pad),
    )
    if exclude:
        q = q.where(ScheduleEvent.id != exclude)
    month = cand.start.astimezone(tz).date().replace(day=1)
    tc = _constraint(db, ctx, uid, month)
    others = [_engine_event(e, tc) for e in db.scalars(q)]
    default_rest = tc.min_rest_minutes if tc and cand.type == "SHIFT" else 0
    cand = Event(
        cand.id, cand.type, cand.start, cand.end, cand.allow_overlap,
        recovery if recovery is not None else default_rest,
    )  # fmt: skip
    conflicts = [
        {"with_id": c.b_id if c.a_id == cand.id else c.a_id, "kind": c.kind,
         "overlap_minutes": c.overlap_minutes}
        for c in find_conflicts([*others, cand]) if cand.id in (c.a_id, c.b_id)
    ]  # fmt: skip
    rest = [
        {"after_id": r.after_id, "next_id": r.next_id, "required_minutes": r.required_minutes,
         "gap_minutes": r.gap_minutes}
        for r in rest_violations([*others, cand]) if cand.id in (r.after_id, r.next_id)
    ]  # fmt: skip
    win = Interval(cand.start, cand.end)
    closed, _ = _closures(db, ctx, uid, win, tz, tc)
    return {"conflicts": conflicts, "rest_violations": rest,
            "closed_days": [{"date": d.isoformat(), "reason": r} for d, r in closed]}  # fmt: skip


@router.get("/schedule/events")
def list_events(
    from_: date = Query(alias="from"),
    to: date = Query(),
    user_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_SCHED_READ)
    uid, tz = _target_user(db, ctx, user_id), _tz(db, ctx)
    w = _range(from_, to, tz)
    rows = db.scalars(
        _events_q(ctx, uid)
        .where(ScheduleEvent.starts_at < _utc(w.end), ScheduleEvent.ends_at > _utc(w.start))
        .order_by(ScheduleEvent.starts_at, ScheduleEvent.id)
    )
    show = _can_see_work(ctx)
    return [_event_out(e, tz, show) for e in rows]


@router.post("/schedule/events", status_code=status.HTTP_201_CREATED)
def create_event(
    body: EventIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    uid, tz = _target_user(db, ctx, body.user_id), _tz(db, ctx)
    _check_work_ref(db, ctx, body.work_source_id)
    cand = Event("new", body.type, body.starts_at, body.ends_at, body.allow_overlap)
    report = _assess(db, ctx, uid, tz, cand, body.recovery_minutes)
    hard = [c for c in report["conflicts"] if c["kind"] == "HARD"]
    if hard and not body.allow_conflict:
        raise HTTPException(409, detail={"message": "Conflito de horário", **report})
    e = ScheduleEvent(
        organization_id=ctx.organization_id, user_id=uid, created_by=ctx.user.id,
        type=body.type, title=body.title, starts_at=_utc(body.starts_at),
        ends_at=_utc(body.ends_at), status=body.status, allow_overlap=body.allow_overlap,
        recovery_minutes=body.recovery_minutes, work_source_id=body.work_source_id,
    )  # fmt: skip
    db.add(e)
    db.flush()
    audit(db, action="schedule.event_create", object_type="schedule_event", object_id=e.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"type": e.type})  # fmt: skip
    db.commit()
    return {**_event_out(e, tz, _can_see_work(ctx)), "warnings": report}


def _get_event(db: Session, ctx: TenantContext, eid: uuid.UUID) -> ScheduleEvent:
    e = db.scalar(
        select(ScheduleEvent).where(
            ScheduleEvent.id == eid,
            ScheduleEvent.organization_id == ctx.organization_id,
            ScheduleEvent.deleted_at.is_(None),
        )
    )
    if e is None or (e.user_id != ctx.user.id and not ctx.can(P_SCHED_ALL)):
        raise HTTPException(404, "Evento não encontrado")
    return e


@router.patch("/schedule/events/{event_id}")
def update_event(
    event_id: uuid.UUID,
    body: EventPatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_SCHED_WRITE)
    e, tz = _get_event(db, ctx, event_id), _tz(db, ctx)
    data = body.model_dump(exclude_unset=True, exclude={"allow_conflict"})
    starts = _utc(data.get("starts_at", e.starts_at))
    ends = _utc(data.get("ends_at", e.ends_at))
    if not (timedelta(0) < _aware(ends) - _aware(starts) <= timedelta(minutes=MAX_EVENT_MINUTES)):
        raise HTTPException(422, "Duração deve ser positiva e de até 7 dias")
    allow_overlap = data.get("allow_overlap", e.allow_overlap)
    if allow_overlap and e.type != "ON_CALL":
        raise HTTPException(422, "allow_overlap só vale para sobreaviso")
    report = {"conflicts": [], "rest_violations": [], "closed_days": []}
    if data.get("status") != "CANCELLED":
        cand = Event(str(e.id), e.type, _aware(starts), _aware(ends), allow_overlap)
        rec = data.get("recovery_minutes", e.recovery_minutes)
        report = _assess(db, ctx, e.user_id, tz, cand, rec, exclude=e.id)
        if any(c["kind"] == "HARD" for c in report["conflicts"]) and not body.allow_conflict:
            raise HTTPException(409, detail={"message": "Conflito de horário", **report})
    for k, v in data.items():
        setattr(e, k, _utc(v) if k in ("starts_at", "ends_at") else v)
    audit(db, action="schedule.event_update", object_type="schedule_event", object_id=e.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return {**_event_out(e, tz, _can_see_work(ctx)), "warnings": report}


@router.delete("/schedule/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    e = _get_event(db, ctx, event_id)
    e.deleted_at = utcnow()
    audit(db, action="schedule.event_delete", object_type="schedule_event", object_id=e.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- feriados ----------


class HolidayIn(BaseModel):
    date: date
    name: str = Field(min_length=1, max_length=120)
    scope: str = Field(default="CUSTOM", pattern="^(NATIONAL|STATE|MUNICIPAL|CUSTOM)$")
    confirmed: bool = True


class HolidayPatch(BaseModel):
    confirmed: bool


def _hol_out(h: Holiday) -> dict:
    return {"id": str(h.id), "date": h.date.isoformat(), "name": h.name, "scope": h.scope,
            "confirmed": h.confirmed}  # fmt: skip


def _get_holiday(db: Session, ctx: TenantContext, hid: uuid.UUID) -> Holiday:
    h = db.scalar(
        select(Holiday).where(Holiday.id == hid, Holiday.organization_id == ctx.organization_id)
    )
    if h is None:
        raise HTTPException(404, "Feriado não encontrado")
    return h


@router.get("/holidays")
def list_holidays(
    year: int = Query(ge=2000, le=2100),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_SCHED_READ)
    rows = db.scalars(
        select(Holiday)
        .where(
            Holiday.organization_id == ctx.organization_id,
            Holiday.date >= date(year, 1, 1),
            Holiday.date <= date(year, 12, 31),
        )
        .order_by(Holiday.date)
    )
    return [_hol_out(h) for h in rows]


@router.post("/holidays", status_code=status.HTTP_201_CREATED)
def create_holiday(
    body: HolidayIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    dup = db.scalar(
        select(Holiday.id).where(
            Holiday.organization_id == ctx.organization_id,
            Holiday.date == body.date,
            Holiday.name == body.name,
        )
    )
    if dup:
        raise HTTPException(409, "Feriado já cadastrado")
    h = Holiday(organization_id=ctx.organization_id, **body.model_dump())
    db.add(h)
    db.commit()
    return _hol_out(h)


@router.post("/holidays/suggest")
def suggest_holidays(
    year: int = Query(ge=2000, le=2100),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Sugere feriados nacionais por lei, *não confirmados*: o usuário confirma um a um.
    Estaduais, municipais e pontos facultativos não são sugeridos."""
    ctx.require(P_SCHED_WRITE)
    created = 0
    for d, name in national_holidays(year):
        exists = db.scalar(
            select(Holiday.id).where(
                Holiday.organization_id == ctx.organization_id,
                Holiday.date == d,
                Holiday.name == name,
            )
        )
        if not exists:
            db.add(Holiday(organization_id=ctx.organization_id, date=d, name=name,
                           scope="NATIONAL", confirmed=False))  # fmt: skip
            created += 1
    db.commit()
    return {"created": created}


@router.patch("/holidays/{holiday_id}")
def patch_holiday(
    holiday_id: uuid.UUID,
    body: HolidayPatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_SCHED_WRITE)
    h = _get_holiday(db, ctx, holiday_id)
    h.confirmed = body.confirmed
    db.commit()
    return _hol_out(h)


@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holiday(
    holiday_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    db.delete(_get_holiday(db, ctx, holiday_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- férias / licenças ----------


class LeaveIn(BaseModel):
    staff_role: str = Field(pattern="^(PHYSICIAN|SECRETARY)$")
    start_date: date
    end_date: date
    coverage_factor_bps: int = Field(default=0, ge=0, le=10_000)
    note: str | None = Field(default=None, max_length=200)
    user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _ok(self):
        if self.end_date < self.start_date or (self.end_date - self.start_date).days > 366:
            raise ValueError("período inválido")
        return self


def _leave_out(lv: Leave) -> dict:
    return {
        "id": str(lv.id),
        "staff_role": lv.staff_role,
        "user_id": str(lv.user_id) if lv.user_id else None,
        "start_date": lv.start_date.isoformat(),
        "end_date": lv.end_date.isoformat(),
        "coverage_factor_bps": lv.coverage_factor_bps,
        "note": lv.note,
    }


@router.get("/leaves")
def list_leaves(
    year: int = Query(ge=2000, le=2100),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_SCHED_READ)
    q = select(Leave).where(
        Leave.organization_id == ctx.organization_id,
        Leave.deleted_at.is_(None),
        Leave.start_date <= date(year, 12, 31),
        Leave.end_date >= date(year, 1, 1),
    )
    if not ctx.can(P_SCHED_ALL):
        q = q.where(Leave.user_id == ctx.user.id)
    return [_leave_out(lv) for lv in db.scalars(q.order_by(Leave.start_date))]


@router.post("/leaves", status_code=status.HTTP_201_CREATED)
def create_leave(
    body: LeaveIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    if body.staff_role == "PHYSICIAN":
        uid = _target_user(db, ctx, body.user_id)
    else:  # férias da secretária: registro da organização, quem opera a agenda pode lançar
        ctx.require(P_SCHED_ALL)
        uid = _target_user(db, ctx, body.user_id) if body.user_id else None
    lv = Leave(organization_id=ctx.organization_id, created_by=ctx.user.id, user_id=uid,
               **body.model_dump(exclude={"user_id"}))  # fmt: skip
    db.add(lv)
    db.flush()
    audit(db, action="schedule.leave_create", object_type="leave", object_id=lv.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return _leave_out(lv)


@router.delete("/leaves/{leave_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_leave(
    leave_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    lv = db.scalar(
        select(Leave).where(
            Leave.id == leave_id, Leave.organization_id == ctx.organization_id,
            Leave.deleted_at.is_(None),
        )
    )  # fmt: skip
    if lv is None or (lv.user_id != ctx.user.id and not ctx.can(P_SCHED_ALL)):
        raise HTTPException(404, "Registro não encontrado")
    lv.deleted_at = utcnow()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- limites de jornada ----------


class ConstraintIn(BaseModel):
    month: str | None = Field(default=None, pattern=MONTH_RE)
    max_active_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 31)
    max_committed_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 31)
    min_rest_minutes: int = Field(default=0, ge=0, le=MAX_EVENT_MINUTES)
    max_night_shifts: int | None = Field(default=None, ge=0, le=31)
    days_off: list[date] = Field(default_factory=list, max_length=62)
    user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _ok(self):
        a, c = self.max_active_minutes, self.max_committed_minutes
        if a is not None and c is not None and c < a:
            raise ValueError("limite de horas comprometidas não pode ser menor que o de ativas")
        return self


def _tc_out(tc: TimeConstraint | None) -> dict | None:
    if tc is None:
        return None
    return {
        "month": tc.month.strftime("%Y-%m") if tc.month else None,
        "max_active_minutes": tc.max_active_minutes,
        "max_committed_minutes": tc.max_committed_minutes,
        "min_rest_minutes": tc.min_rest_minutes,
        "max_night_shifts": tc.max_night_shifts,
        "days_off": list(tc.days_off or []),
    }


@router.get("/time-constraints")
def get_constraints(
    month: str = Query(pattern=MONTH_RE),
    user_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_SCHED_READ)
    uid = _target_user(db, ctx, user_id)
    return {"effective": _tc_out(_constraint(db, ctx, uid, _month(month)))}


@router.put("/time-constraints")
def put_constraints(
    body: ConstraintIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_SCHED_WRITE)
    uid = _target_user(db, ctx, body.user_id)
    m = _month(body.month) if body.month else None
    tc = db.scalar(
        select(TimeConstraint).where(
            TimeConstraint.organization_id == ctx.organization_id,
            TimeConstraint.user_id == uid,
            TimeConstraint.month.is_(m) if m is None else TimeConstraint.month == m,
        )
    )
    if tc is None:
        tc = TimeConstraint(organization_id=ctx.organization_id, user_id=uid, month=m)
        db.add(tc)
    tc.max_active_minutes = body.max_active_minutes
    tc.max_committed_minutes = body.max_committed_minutes
    tc.min_rest_minutes = body.min_rest_minutes
    tc.max_night_shifts = body.max_night_shifts
    tc.days_off = sorted({d.isoformat() for d in body.days_off})
    audit(db, action="schedule.constraints_put", object_type="time_constraint",
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return {"effective": _tc_out(tc)}


# ---------- visão do mês e capacidade ----------


@router.get("/schedule/overview")
def overview(
    month: str = Query(pattern=MONTH_RE),
    user_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Horas ativas e comprometidas (sem dupla contagem), conflitos, descanso e limites."""
    ctx.require(P_SCHED_READ)
    uid, tz = _target_user(db, ctx, user_id), _tz(db, ctx)
    m = _month(month)
    win = local_month(m, tz)
    tc = _constraint(db, ctx, uid, m)
    rows = list(
        db.scalars(
            _events_q(ctx, uid).where(
                ScheduleEvent.starts_at < _utc(win.end + timedelta(days=8)),
                ScheduleEvent.ends_at > _utc(win.start - timedelta(days=8)),
            )
        )
    )
    events = [_engine_event(e, tc) for e in rows]
    in_month = [e for e in events if e.start < win.end and e.end > win.start]
    month_ids = {e.id for e in in_month}

    active = total_minutes(clip([e.interval for e in in_month if e.type in ACTIVE_TYPES], win))
    committed = total_minutes(
        clip([e.interval for e in in_month if e.type in COMMITTED_TYPES], win)
    )
    conflicts = [c for c in find_conflicts(in_month)]
    rest = [r for r in rest_violations(events) if r.after_id in month_ids or r.next_id in month_ids]
    night = sum(1 for e in in_month if e.type == "SHIFT" and is_night(e, tz))
    closed, _ = _closures(db, ctx, uid, win, tz, tc)
    closed_map = dict(closed)

    def day_hits(e: Event) -> list[str]:
        d, last = (
            e.start.astimezone(tz).date(),
            (e.end - timedelta(seconds=1)).astimezone(tz).date(),
        )
        hits = []
        while d <= last:
            if d in closed_map and e.type in ACTIVE_TYPES:
                hits.append(d.isoformat())
            d += timedelta(days=1)
        return hits

    on_closed = [{"event_id": e.id, "dates": h, "reason": closed_map[date.fromisoformat(h[0])]}
                 for e in in_month if (h := day_hits(e))]  # fmt: skip
    limits = _tc_out(tc) or {}
    over: dict[str, int] = {}
    if limits.get("max_active_minutes") is not None and active > limits["max_active_minutes"]:
        over["active_excess_minutes"] = active - limits["max_active_minutes"]
    if (
        limits.get("max_committed_minutes") is not None
        and committed > limits["max_committed_minutes"]
    ):
        over["committed_excess_minutes"] = committed - limits["max_committed_minutes"]
    if limits.get("max_night_shifts") is not None and night > limits["max_night_shifts"]:
        over["night_shifts_excess"] = night - limits["max_night_shifts"]
    hard = [c for c in conflicts if c.kind == "HARD"]
    return {
        "month": month,
        "user_id": str(uid),
        "active_minutes": active,
        "committed_minutes": committed,
        "night_shifts": night,
        "limits": limits or None,
        "over_limit": over,
        "conflicts": [
            {"a_id": c.a_id, "b_id": c.b_id, "kind": c.kind, "overlap_minutes": c.overlap_minutes}
            for c in conflicts
        ],
        "rest_violations": [
            {
                "after_id": r.after_id,
                "next_id": r.next_id,
                "required_minutes": r.required_minutes,
                "gap_minutes": r.gap_minutes,
            }
            for r in rest
        ],  # fmt: skip
        "events_on_closed_days": on_closed,
        "feasible": not (hard or rest or over or on_closed),
        "notes": ["Tempo comprometido = ativo + sobreaviso + deslocamento, sem dupla contagem."],
    }


@router.get("/schedule/capacity")
def capacity(
    month: str = Query(pattern=MONTH_RE),
    user_id: uuid.UUID | None = None,
    slot_minutes: int | None = Query(default=None, ge=5, le=480),
    service_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Capacidade real do mês: disponibilidade menos fechamentos, compromissos e consultas já
    marcadas. Slots são contados por janela (um horário não atravessa lacunas)."""
    ctx.require(P_SCHED_READ)
    uid, tz = _target_user(db, ctx, user_id), _tz(db, ctx)
    m = _month(month)
    win = local_month(m, tz)
    tc = _constraint(db, ctx, uid, m)

    avail = merge(
        clip(
            [
                Interval(_aware(s.starts_at), _aware(s.ends_at))
                for s in db.scalars(
                    select(AvailabilitySlot).where(
                        AvailabilitySlot.organization_id == ctx.organization_id,
                        AvailabilitySlot.user_id == uid,
                        AvailabilitySlot.starts_at < _utc(win.end),
                        AvailabilitySlot.ends_at > _utc(win.start),
                    )
                )
            ],
            win,
        )
    )
    closed, closure_ivs = _closures(db, ctx, uid, win, tz, tc)
    after_closure = subtract(avail, closure_ivs)

    rows = list(
        db.scalars(
            _events_q(ctx, uid).where(
                ScheduleEvent.starts_at < _utc(win.end + timedelta(days=8)),
                ScheduleEvent.ends_at > _utc(win.start - timedelta(days=8)),
            )
        )
    )
    blockers: list[Interval] = []
    booked: list[Interval] = []
    conditional: list[Interval] = []
    for e in rows:
        ev = _engine_event(e, tc)
        if e.type in CONSULT_TYPES:
            booked.append(ev.interval)
            continue
        if e.type == "ON_CALL" and e.allow_overlap:
            conditional.append(ev.interval)
            continue
        blockers.append(ev.interval)
        if ev.recovery_minutes > 0 and e.type in ACTIVE_TYPES:
            blockers.append(Interval(ev.end, ev.end + timedelta(minutes=ev.recovery_minutes)))
    gross = subtract(after_closure, blockers)
    remaining = subtract(gross, booked)

    out = {
        "month": month,
        "user_id": str(uid),
        "availability_minutes": total_minutes(avail),
        "closed_minutes": total_minutes(avail) - total_minutes(after_closure),
        "blocked_minutes": total_minutes(after_closure) - total_minutes(gross),
        "gross_free_minutes": total_minutes(gross),
        "booked_consult_minutes": total_minutes(gross) - total_minutes(remaining),
        "remaining_minutes": total_minutes(remaining),
        "conditional_on_call_minutes": total_minutes(
            [i for c in conditional for i in clip(remaining, c)]
        ),
        "closed_days": [{"date": d.isoformat(), "reason": r} for d, r in closed],
        "warnings": [] if avail else ["Nenhuma disponibilidade cadastrada para este mês."],
    }

    slot = slot_minutes
    if service_id is not None:
        svc = _get_service(db, ctx, service_id)  # exige clinic:read via 404 se não visível
        econ, _ = _economics(svc, _policy_of(db, ctx, svc), svc.price_cents)
        slot = slot or svc.duration_minutes
        out["service"] = {
            "service_id": str(svc.id),
            "expected_minutes_per_new_patient": f"{econ.minutes_x10 // 10}.{econ.minutes_x10 % 10}",
            "new_patients_gross": total_minutes(gross) * 10 // econ.minutes_x10,
            "new_patients_remaining": total_minutes(remaining) * 10 // econ.minutes_x10,
            "note": "Inclui o tempo esperado de retornos no mesmo horizonte (PRD 7.2).",
        }
    slot = slot or 30
    out["slot_minutes"] = slot
    out["slots_gross"] = count_slots(gross, slot)
    out["slots_remaining"] = count_slots(remaining, slot)

    days = []
    closed_map = dict(closed)
    d = m
    while d < win.end.astimezone(tz).date():
        day = local_day(d, tz)
        rem = clip(remaining, day)
        days.append({
            "date": d.isoformat(),
            "closed_reason": closed_map.get(d),
            "available_minutes": total_minutes(clip(avail, day)),
            "remaining_minutes": total_minutes(rem),
            "slots_remaining": count_slots(rem, slot),
        })  # fmt: skip
        d += timedelta(days=1)
    out["days"] = days
    return out
