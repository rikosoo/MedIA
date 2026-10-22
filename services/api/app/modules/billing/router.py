import hashlib
import hmac
import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal  # noqa: F401  (serialização defensiva)

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_BILLING
from app.db.base import utcnow
from app.db.models import (
    BillingEvent,
    ClinicService,
    IncomePlan,
    Invoice,
    MonthlyGoal,
    Organization,
    ReturnPolicy,
    Subscription,
    Transaction,
    WorkSource,
)  # fmt: skip
from app.db.session import get_db
from app.modules.billing.entitlements import (
    TOKENS,
    add_invoice,
    aware,
    effective_plan,
    get_sub,
    in_trial,
    plan_change,
    provider_charge,
    status_of,
    to_free,
    usage,
)  # fmt: skip
from app.modules.billing.plans import GRACE_DAYS, PERIOD_DAYS, PLANS
from finance_engine import format_brl

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
webhook_router = APIRouter(prefix="/api/v1/billing", tags=["billing-webhook"])


def _iso(d: datetime | None) -> str | None:
    return aware(d).isoformat() if d else None


def sub_out(db: Session, sub: Subscription, ctx: TenantContext, now: datetime) -> dict:
    eff = effective_plan(sub, now)
    grace = (
        _iso(aware(sub.past_due_since) + timedelta(days=GRACE_DAYS))
        if sub.state == "PAST_DUE" and sub.past_due_since else None
    )  # fmt: skip
    return {
        "plan": sub.plan, "plan_name": PLANS[sub.plan]["name"], "effective_plan": eff,
        "status": status_of(sub, now), "in_trial": in_trial(sub, now),
        "trial_ends_at": _iso(sub.trial_ends_at), "current_period_end": _iso(sub.current_period_end),
        "next_billing_at": _iso(sub.current_period_end)
        if sub.plan != "FREE" and not sub.cancel_at_period_end and sub.pending_plan != "FREE" else None,
        "cancel_at_period_end": sub.cancel_at_period_end or sub.pending_plan == "FREE",
        "pending_plan": sub.pending_plan if sub.pending_plan != "FREE" else None,
        "grace_ends_at": grace,
        "payment_method": {"brand": sub.payment_brand, "last4": sub.payment_last4}
        if sub.payment_last4 else None,
        "limits": PLANS[eff]["limits"], "features": PLANS[eff]["features"],
        "usage": usage(db, ctx.organization_id, ctx.user.id, now),
        "price_cents": PLANS[sub.plan]["price_cents"],
        "provider": sub.provider,
        "provider_note": "Cobrança simulada: nenhum pagamento real é feito neste ambiente.",
    }  # fmt: skip


def _ctx(ctx: TenantContext) -> TenantContext:
    ctx.require(P_BILLING)
    return ctx


@router.get("/plans")
def plans(ctx: TenantContext = Depends(get_tenant)):
    ctx.require(P_BILLING)
    return [{"plan": k, **{x: v[x] for x in ("name", "price_cents", "summary", "limits", "features")}}
            for k, v in PLANS.items()]  # fmt: skip


@router.get("/subscription")
def subscription(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    _ctx(ctx)
    now = utcnow()
    sub = get_sub(db, ctx.organization_id, now)
    db.commit()
    return sub_out(db, sub, ctx, now)


class ChangeIn(BaseModel):
    plan: str = Field(pattern=r"^(FREE|PRO|PRO_AI|CLINIC)$")


@router.post("/subscription/preview")
def preview_change(
    body: ChangeIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    _ctx(ctx)
    now = utcnow()
    out = plan_change(get_sub(db, ctx.organization_id, now), body.plan, now)
    db.commit()
    return out


@router.patch("/subscription")
def change_plan(
    body: ChangeIn,
    idempotency_key: str | None = Header(default=None, max_length=80),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Upgrade vale agora (cobrança proporcional); downgrade e cancelamento só no fim do período."""
    _ctx(ctx)
    now = utcnow()
    org = ctx.organization_id
    if idempotency_key and db.scalar(select(Invoice).where(
        Invoice.organization_id == org, Invoice.idempotency_key == idempotency_key)):  # fmt: skip
        sub = get_sub(db, org, now)
        return {**sub_out(db, sub, ctx, now), "replayed": True}
    sub = get_sub(db, org, now)
    eff = plan_change(sub, body.plan, now)
    action, charge = eff["action"], eff["charge_today_cents"]
    if sub.state == "PAST_DUE" and action in ("START", "UPGRADE_NOW"):
        raise HTTPException(409, "Regularize o pagamento pendente antes de mudar de plano.")
    if action in ("START", "UPGRADE_NOW"):
        if not sub.payment_token:
            raise HTTPException(409, "Cadastre uma forma de pagamento antes de assinar.")
        name = PLANS[body.plan]["name"]
        if not provider_charge(sub.payment_token, charge, renewal=False):
            add_invoice(db, org, f"Adesão ao plano {name}", charge, "FAILED", now, None)
            audit(db, action="billing.charge_failed", object_type="subscription", object_id=sub.id,
                  organization_id=org, actor_user_id=ctx.user.id, metadata={"plan": body.plan})  # fmt: skip
            db.commit()
            raise HTTPException(
                402, "Pagamento recusado. Nada foi alterado; confira a forma de pagamento."
            )
        add_invoice(db, org, f"{'Adesão' if action == 'START' else 'Upgrade'} ao plano {name}", charge, "PAID",
                    now, now + timedelta(days=PERIOD_DAYS) if action == "START" else sub.current_period_end,
                    idempotency_key)  # fmt: skip
        sub.plan, sub.pending_plan, sub.cancel_at_period_end = body.plan, None, False
        if action == "START":
            sub.trial_ends_at = min(aware(sub.trial_ends_at), now)
            sub.current_period_end = now + timedelta(days=PERIOD_DAYS)
    elif action == "DOWNGRADE_AT_PERIOD_END":
        sub.pending_plan, sub.cancel_at_period_end = body.plan, False
    elif action == "CANCEL_AT_PERIOD_END":
        sub.pending_plan, sub.cancel_at_period_end = "FREE", True
    elif action == "KEEP":
        sub.pending_plan, sub.cancel_at_period_end = None, False
    audit(db, action=f"billing.{action.lower()}", object_type="subscription", object_id=sub.id,
          organization_id=org, actor_user_id=ctx.user.id, metadata={"from": eff["from"], "to": body.plan})  # fmt: skip
    db.commit()
    return {**sub_out(db, sub, ctx, now), "change": eff}


class PaymentIn(BaseModel):
    token: str = Field(min_length=3, max_length=80)


_PAN = re.compile(r"\d[\d \-]{11,}\d")


@router.put("/payment-method")
def set_payment_method(
    body: PaymentIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Recebe só um token do provedor (nunca o número do cartão). Em atraso, tenta cobrar de novo."""
    _ctx(ctx)
    if _PAN.search(body.token):
        raise HTTPException(
            422, "Nunca envie o número do cartão: use o token do provedor de pagamento."
        )
    if body.token not in TOKENS:
        raise HTTPException(422, "Token de pagamento inválido")
    now = utcnow()
    sub = get_sub(db, ctx.organization_id, now)
    sub.payment_token = body.token
    sub.payment_brand, sub.payment_last4 = TOKENS[body.token]
    retried = None
    if sub.state == "PAST_DUE":
        price = PLANS[sub.pending_plan if sub.pending_plan not in (None, "FREE") else sub.plan][
            "price_cents"
        ]
        retried = provider_charge(body.token, price, renewal=False)
        if retried:
            add_invoice(db, ctx.organization_id, f"Pagamento regularizado ({PLANS[sub.plan]['name']})", price,
                        "PAID", now, now + timedelta(days=PERIOD_DAYS))  # fmt: skip
            if sub.pending_plan not in (None, "FREE"):
                sub.plan = sub.pending_plan
            sub.pending_plan, sub.state, sub.past_due_since = None, "ACTIVE", None
            sub.current_period_end = now + timedelta(days=PERIOD_DAYS)
    audit(db, action="billing.payment_method", object_type="subscription", object_id=sub.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return {**sub_out(db, sub, ctx, now), "retried_charge": retried}


class CancelIn(BaseModel):
    confirm: bool


@router.post("/cancel")
def cancel(body: CancelIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    """Cancelamento em um passo, com confirmação explícita: acesso até o fim do período pago."""
    _ctx(ctx)
    if not body.confirm:
        raise HTTPException(422, "Confirme o cancelamento para continuar.")
    now = utcnow()
    sub = get_sub(db, ctx.organization_id, now)
    if sub.plan == "FREE":
        raise HTTPException(409, "Não há assinatura paga para cancelar.")
    if sub.cancel_at_period_end or sub.pending_plan == "FREE":
        raise HTTPException(409, "O cancelamento já está agendado.")
    sub.pending_plan, sub.cancel_at_period_end = "FREE", True
    audit(db, action="billing.cancel", object_type="subscription", object_id=sub.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    end = aware(sub.current_period_end).strftime("%d/%m/%Y") if sub.current_period_end else "hoje"
    return {**sub_out(db, sub, ctx, now),
            "message": f"Cancelamento agendado. Você mantém o acesso até {end}; não haverá nova cobrança e "
                       "não há reembolso do período corrente. Seus dados e a exportação continuam disponíveis."}  # fmt: skip


@router.post("/resume")
def resume(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    _ctx(ctx)
    now = utcnow()
    sub = get_sub(db, ctx.organization_id, now)
    if not (sub.cancel_at_period_end or sub.pending_plan):
        raise HTTPException(409, "Não há cancelamento ou mudança agendada.")
    sub.pending_plan, sub.cancel_at_period_end = None, False
    audit(db, action="billing.resume", object_type="subscription", object_id=sub.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return sub_out(db, sub, ctx, now)


def _inv(i: Invoice) -> dict:
    return {"id": str(i.id), "number": i.number, "description": i.description,
            "amount_cents": i.amount_cents, "formatted": format_brl(i.amount_cents), "status": i.status,
            "period_start": i.period_start.isoformat() if i.period_start else None,
            "period_end": i.period_end.isoformat() if i.period_end else None,
            "created_at": aware(i.created_at).isoformat()}  # fmt: skip


@router.get("/invoices")
def invoices(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    _ctx(ctx)
    rows = db.scalars(select(Invoice).where(Invoice.organization_id == ctx.organization_id)
                      .order_by(Invoice.created_at.desc(), Invoice.number.desc()))  # fmt: skip
    return [_inv(i) for i in rows]


def _row(obj) -> dict:
    out = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.key)
        if isinstance(v, (bytes, bytearray)):
            continue
        out[c.key] = str(v) if isinstance(v, (uuid.UUID, Decimal)) else (
            v.isoformat() if isinstance(v, (date, datetime)) else v)  # fmt: skip
    return out


@router.get("/export")
def export_data(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    """Exportação dos dados da organização em JSON. Disponível em todos os planos, inclusive vencido."""
    _ctx(ctx)
    org = ctx.organization_id
    now = utcnow()
    sub = get_sub(db, org, now)
    works = list(db.scalars(select(WorkSource).where(WorkSource.organization_id == org,
                WorkSource.owner_user_id == ctx.user.id)))  # fmt: skip
    wids = {w.id for w in works}
    data = {
        "exported_at": now.isoformat(),
        "organization": _row(db.get(Organization, org)),
        "subscription": {k: v for k, v in sub_out(db, sub, ctx, now).items() if k != "usage"},
        "invoices": [_inv(i) for i in db.scalars(select(Invoice).where(Invoice.organization_id == org))],
        "work_sources": [{**_row(w), "income_rule": _row(w.income_rule)} for w in works],
        "clinic_services": [_row(s) for s in db.scalars(select(ClinicService).where(ClinicService.organization_id == org))],
        "return_policies": [_row(p) for p in db.scalars(select(ReturnPolicy).where(ReturnPolicy.organization_id == org))],
        "income_plans": [_row(p) for p in db.scalars(select(IncomePlan).where(IncomePlan.organization_id == org))
                         if p.source_type == "SERVICE" or p.source_id in wids],
        "goals": [_row(g) for g in db.scalars(select(MonthlyGoal).where(
            MonthlyGoal.organization_id == org, MonthlyGoal.user_id == ctx.user.id))],
        "transactions": [_row(t) for t in db.scalars(select(Transaction).where(Transaction.organization_id == org))],
    }  # fmt: skip
    audit(db, action="billing.export", object_type="organization", object_id=org,
          organization_id=org, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(
        json.dumps(data, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="financemed-dados.json"'},
    )


# ---------- webhook do provedor (sem sessão: autenticado por assinatura HMAC) ----------


@webhook_router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    secret = get_settings().billing_webhook_secret
    if not secret:
        raise HTTPException(503, "Webhook não configurado")
    raw = await request.body()
    sent = request.headers.get("x-signature", "")
    good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sent, good):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Assinatura inválida")
    try:
        ev = json.loads(raw)
        event_id, etype, org = (
            str(ev["event_id"])[:80],
            str(ev["type"])[:60],
            uuid.UUID(ev["organization_id"]),
        )
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Evento inválido") from None
    if db.scalar(select(BillingEvent).where(BillingEvent.event_id == event_id)):
        return {"status": "duplicate"}
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == org))
    if sub is None:
        raise HTTPException(404, "Assinatura não encontrada")
    now = utcnow()
    if etype == "invoice.payment_failed" and sub.plan != "FREE":
        sub.state = "PAST_DUE"
        sub.past_due_since = sub.past_due_since or now
    elif etype == "invoice.payment_succeeded":
        sub.state, sub.past_due_since = "ACTIVE", None
    elif etype == "subscription.deleted":
        to_free(sub)
    db.add(BillingEvent(event_id=event_id, event_type=etype, organization_id=org))
    audit(
        db,
        action=f"billing.webhook.{etype}"[:80],
        object_type="subscription",
        object_id=sub.id,
        organization_id=org,
    )
    db.commit()
    return {"status": "processed"}
