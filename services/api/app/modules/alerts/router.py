import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import AuthContext, TenantContext, get_auth, get_tenant
from app.db.base import utcnow
from app.db.models import Notification
from app.db.session import get_db
from app.modules.alerts.engine import KINDS, evaluate, generate, settings_for
from app.modules.finances.router import parse_month
from app.modules.schedule.router import _tz

router = APIRouter(prefix="/api/v1", tags=["alerts"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"


def _today(db: Session, ctx: TenantContext, v: date | None) -> date:
    return v or datetime.now(UTC).astimezone(_tz(db, ctx)).date()


@router.get("/alerts")
def alerts(
    month: str | None = Query(default=None, pattern=MONTH_RE),
    as_of: date | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Alertas de agora, com detalhes. Só aparecem dados que o papel do usuário pode ver."""
    today = _today(db, ctx, as_of)
    m = parse_month(month) if month else today.replace(day=1)
    return {"month": m.strftime("%Y-%m"), "alerts": [a.out() for a in evaluate(db, ctx, m, today)]}


@router.get("/notification-kinds")
def kinds(_auth: AuthContext = Depends(get_auth)):
    return [
        {"kind": k, "title": t, "route": r, "min_interval_days": d}
        for k, (t, r, d) in KINDS.items()
    ]


class SettingsIn(BaseModel):
    enabled: bool
    kinds: list[str] = Field(default_factory=list, max_length=len(KINDS))
    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)

    @model_validator(mode="after")
    def _ok(self):
        bad = [k for k in self.kinds if k not in KINDS]
        if bad:
            raise ValueError(f"tipos de alerta inválidos: {bad}")
        if (self.quiet_start_hour is None) != (self.quiet_end_hour is None):
            raise ValueError("informe o início e o fim do horário silencioso, ou nenhum")
        self.kinds = sorted(set(self.kinds))
        return self


def _settings_out(s) -> dict:
    return {"enabled": s.enabled, "kinds": list(s.kinds or []), "quiet_start_hour": s.quiet_start_hour,
            "quiet_end_hour": s.quiet_end_hour, "channel": "IN_APP"}  # fmt: skip


@router.get("/notification-settings")
def get_settings(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    s = settings_for(db, ctx)
    db.commit()
    return _settings_out(s)


@router.put("/notification-settings")
def put_settings(
    body: SettingsIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    s = settings_for(db, ctx)
    s.enabled, s.kinds = body.enabled, body.kinds
    s.quiet_start_hour, s.quiet_end_hour = body.quiet_start_hour, body.quiet_end_hour
    audit(db, action="notifications.settings", object_type="notification_settings", object_id=s.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"enabled": body.enabled, "kinds": body.kinds})  # fmt: skip
    db.commit()
    return _settings_out(s)


def _n_out(n: Notification) -> dict:
    return {"id": str(n.id), "kind": n.kind, "ref": n.ref, "title": n.title, "body": n.body,
            "route": n.route, "read": n.read_at is not None,
            "created_at": n.created_at.isoformat() if n.created_at else None}  # fmt: skip


def _mine(ctx: TenantContext):
    return select(Notification).where(Notification.organization_id == ctx.organization_id,
                                      Notification.user_id == ctx.user.id)  # fmt: skip


@router.get("/notifications")
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    q = _mine(ctx)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    unread = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.organization_id == ctx.organization_id, Notification.user_id == ctx.user.id,
        Notification.read_at.is_(None)))  # fmt: skip
    rows = db.scalars(q.order_by(Notification.created_at.desc(), Notification.id).limit(limit))
    return {"unread_count": unread, "items": [_n_out(n) for n in rows]}


class GenerateIn(BaseModel):
    month: str | None = Field(default=None, pattern=MONTH_RE)
    now: datetime | None = None  # só para reprodutibilidade; padrão: agora


@router.post("/notifications/generate")
def generate_notifications(body: GenerateIn | None = None, ctx: TenantContext = Depends(get_tenant),
                           db: Session = Depends(get_db)):  # fmt: skip
    """Cria notificações dos alertas ligados. Idempotente: respeita frequência mínima, horário
    silencioso e o limite diário, então chamar de novo não gera spam."""
    body = body or GenerateIn()
    now = body.now or datetime.now(UTC)
    if now.tzinfo is None:
        raise HTTPException(422, "now precisa ter fuso horário")
    m = (
        parse_month(body.month)
        if body.month
        else now.astimezone(_tz(db, ctx)).date().replace(day=1)
    )
    res = generate(db, ctx, m, now)
    db.commit()
    return res


@router.post("/notifications/read-all")
def read_all(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    n = 0
    for x in db.scalars(_mine(ctx).where(Notification.read_at.is_(None))):
        x.read_at = utcnow()
        n += 1
    db.commit()
    return {"marked": n}


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read_one(
    notification_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    n = db.scalar(_mine(ctx).where(Notification.id == notification_id))
    if n is None:
        raise HTTPException(404, "Notificação não encontrada")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
