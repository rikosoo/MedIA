"""Assinatura, direitos por plano e renovação. Nada aqui cobra de verdade (provedor simulado).

A renovação é *preguiçosa*: `get_sub` sincroniza a assinatura ao ser lida (virada de período,
cancelamento agendado, downgrade agendado, falha de pagamento e fim da carência). Num ambiente com
provedor real, a mesma `sync` seria chamada por um agendador e pelos webhooks.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.db.base import utcnow
from app.db.models import (
    AiConversation,
    AiMessage,
    Invoice,
    MonthlyGoal,
    OptimizerRun,
    Organization,
    Subscription,
    WorkSource,
)  # fmt: skip
from app.modules.billing.plans import (
    FEATURE_LABEL,
    FEATURE_MIN_PLAN,
    GRACE_DAYS,
    LIMIT_LABEL,
    PERIOD_DAYS,
    PLANS,
    RANK,
    TRIAL_DAYS,
)  # fmt: skip
from finance_engine import format_brl

TOKENS = {  # token do provedor simulado -> (bandeira, final)
    "tok_visa": ("Visa", "4242"),
    "tok_mastercard": ("Mastercard", "4444"),
    "tok_fail": ("Visa", "0002"),  # recusado em toda cobrança
    "tok_fail_renewal": ("Visa", "0341"),  # aceito na adesão, recusado na renovação
}


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def provider_charge(token: str | None, amount_cents: int, renewal: bool) -> bool:
    if amount_cents <= 0:
        return True
    if not token or token == "tok_fail":
        return False
    return not (renewal and token == "tok_fail_renewal")


def add_invoice(
    db, org_id, desc, amount, status, start: datetime | None, end: datetime | None, key=None
):
    n = (
        db.scalar(select(func.count(Invoice.id)).where(Invoice.organization_id == org_id)) or 0
    ) + 1
    inv = Invoice(
        organization_id=org_id, number=f"FM-{n:06d}", description=desc, amount_cents=amount,
        status=status, period_start=start.date() if start else None,
        period_end=end.date() if end else None, idempotency_key=key,
    )  # fmt: skip
    db.add(inv)
    return inv


def to_free(sub: Subscription) -> None:
    sub.plan, sub.state = "FREE", "ACTIVE"
    sub.cancel_at_period_end, sub.pending_plan = False, None
    sub.current_period_end, sub.past_due_since = None, None


def sync(db: Session, sub: Subscription, now: datetime) -> bool:
    changed = False
    if (
        sub.state == "PAST_DUE"
        and sub.past_due_since
        and now >= aware(sub.past_due_since) + timedelta(days=GRACE_DAYS)
    ):
        to_free(sub)
        changed = True
    if (
        sub.plan != "FREE"
        and sub.state == "ACTIVE"
        and sub.current_period_end
        and aware(sub.current_period_end) <= now
    ):
        end = aware(sub.current_period_end)
        if sub.cancel_at_period_end or sub.pending_plan == "FREE":
            to_free(sub)
        else:
            target = sub.pending_plan or sub.plan
            price = PLANS[target]["price_cents"]
            desc = f"Renovação do plano {PLANS[target]['name']}"
            if provider_charge(sub.payment_token, price, renewal=True):
                add_invoice(
                    db,
                    sub.organization_id,
                    desc,
                    price,
                    "PAID",
                    end,
                    now + timedelta(days=PERIOD_DAYS),
                )
                sub.plan, sub.pending_plan = target, None
                sub.current_period_end = now + timedelta(days=PERIOD_DAYS)
            else:
                add_invoice(db, sub.organization_id, desc, price, "FAILED", end, None)
                sub.state, sub.past_due_since = "PAST_DUE", now
        changed = True
    return changed


def get_sub(db: Session, org_id: uuid.UUID, now: datetime | None = None) -> Subscription:
    now = now or utcnow()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    if sub is None:
        org = db.get(Organization, org_id)
        started = aware(org.created_at) if org and org.created_at else now
        sub = Subscription(
            organization_id=org_id, trial_ends_at=started + timedelta(days=TRIAL_DAYS)
        )
        db.add(sub)
        db.flush()
    if sync(db, sub, now):
        db.flush()
    return sub


def in_trial(sub: Subscription, now: datetime) -> bool:
    return sub.plan == "FREE" and aware(sub.trial_ends_at) > now


def effective_plan(sub: Subscription, now: datetime) -> str:
    """Durante o teste grátis todos os recursos ficam liberados (como no plano Clínica)."""
    return "CLINIC" if in_trial(sub, now) else sub.plan


def status_of(sub: Subscription, now: datetime) -> str:
    if sub.state == "PAST_DUE":
        return "PAST_DUE"
    if in_trial(sub, now):
        return "TRIALING"
    if sub.plan == "FREE":
        return "FREE"
    return "CANCELING" if sub.cancel_at_period_end or sub.pending_plan == "FREE" else "ACTIVE"


def _need(plan_name: str, what: str) -> HTTPException:
    return HTTPException(
        402, f"{what} não está incluído no plano {plan_name}. Veja os planos em Planos & Cobrança."
    )


def require_feature(db: Session, ctx: TenantContext, feature: str) -> None:
    now = utcnow()
    plan = effective_plan(get_sub(db, ctx.organization_id, now), now)
    if feature not in PLANS[plan]["features"]:
        need = PLANS[FEATURE_MIN_PLAN[feature]]["name"]
        raise HTTPException(
            402,
            f"{FEATURE_LABEL[feature]} exige o plano {need} ou superior (seu plano: "
            f"{PLANS[plan]['name']}). Veja os planos em Planos & Cobrança.",
        )


def month_start(d: date) -> datetime:
    return datetime(d.year, d.month, 1, tzinfo=UTC)


def usage(
    db: Session, org_id: uuid.UUID, user_id: uuid.UUID | None = None, now: datetime | None = None
) -> dict:
    now = now or utcnow()
    works = db.scalar(select(func.count(WorkSource.id)).where(
        WorkSource.organization_id == org_id, WorkSource.deleted_at.is_(None),
        WorkSource.status == "active")) or 0  # fmt: skip
    m = date(now.year, now.month, 1)
    goals = db.scalar(select(func.count(MonthlyGoal.id)).where(
        MonthlyGoal.organization_id == org_id, MonthlyGoal.deleted_at.is_(None),
        MonthlyGoal.month == m)) or 0  # fmt: skip
    runs = db.scalar(select(func.count(OptimizerRun.id)).where(
        OptimizerRun.organization_id == org_id, OptimizerRun.created_at >= month_start(m))) or 0  # fmt: skip
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q = select(func.count(AiMessage.id)).join(
        AiConversation, AiConversation.id == AiMessage.conversation_id).where(
        AiConversation.organization_id == org_id, AiMessage.role == "USER", AiMessage.created_at >= day)  # fmt: skip
    asks = db.scalar(q.where(AiConversation.owner_user_id == user_id) if user_id else q) or 0
    return {"works": works, "goals_per_month": goals, "optimizer_runs_per_month": runs,
            "assistant_per_day": asks}  # fmt: skip


def check_limit(db: Session, ctx: TenantContext, key: str, extra: int = 1) -> None:
    """402 se criar `extra` itens novos ultrapassaria o limite do plano. Dados existentes não somem."""
    now = utcnow()
    plan = effective_plan(get_sub(db, ctx.organization_id, now), now)
    limit = PLANS[plan]["limits"].get(key)
    if limit is None:
        return
    used = usage(db, ctx.organization_id, ctx.user.id, now)[key]
    if used + extra > limit:
        raise HTTPException(
            402,
            f"Limite do plano {PLANS[plan]['name']} atingido: {limit} {LIMIT_LABEL[key]}. "
            "Seus dados continuam disponíveis; para criar mais, veja os planos em Planos & Cobrança.",
        )


def check_goal_limit(db: Session, ctx: TenantContext, month: date) -> None:
    """Limite de metas por mês conta as metas do próprio mês da nova meta."""
    now = utcnow()
    plan = effective_plan(get_sub(db, ctx.organization_id, now), now)
    limit = PLANS[plan]["limits"].get("goals_per_month")
    if limit is None:
        return
    used = db.scalar(select(func.count(MonthlyGoal.id)).where(
        MonthlyGoal.organization_id == ctx.organization_id, MonthlyGoal.user_id == ctx.user.id,
        MonthlyGoal.deleted_at.is_(None), MonthlyGoal.month == month)) or 0  # fmt: skip
    if used + 1 > limit:
        raise HTTPException(
            402,
            f"Limite do plano {PLANS[plan]['name']} atingido: {limit} {LIMIT_LABEL['goals_per_month']}. "
            "Seus dados continuam disponíveis; para criar mais, veja os planos em Planos & Cobrança.",
        )


def professionals(db: Session, org_id: uuid.UUID) -> int:
    from app.db.models import Membership

    return db.scalar(select(func.count(Membership.id)).where(
        Membership.organization_id == org_id,
        Membership.role.in_(["OWNER", "PHYSICIAN"]))) or 0  # fmt: skip


def check_team(db: Session, ctx: TenantContext, new_role: str) -> None:
    require_feature(db, ctx, "team")
    now = utcnow()
    plan = effective_plan(get_sub(db, ctx.organization_id, now), now)
    cap = PLANS[plan]["limits"].get("professionals")
    if (
        new_role == "PHYSICIAN"
        and cap is not None
        and professionals(db, ctx.organization_id) + 1 > cap
    ):
        raise HTTPException(402, f"O plano {PLANS[plan]['name']} permite até {cap} profissionais.")


# ---------- mudança de plano ----------


def plan_change(sub: Subscription, target: str, now: datetime) -> dict:
    """Calcula o efeito (sem executar). Valores sempre em centavos inteiros."""
    cur = sub.plan
    if target not in PLANS:
        raise HTTPException(422, "Plano inválido")
    new_price, old_price = PLANS[target]["price_cents"], PLANS[cur]["price_cents"]
    end = aware(sub.current_period_end) if sub.current_period_end else None
    out = {"from": cur, "to": target, "charge_today_cents": 0, "effective_at": None}
    if target == cur:
        if sub.pending_plan or sub.cancel_at_period_end:
            out.update(
                action="KEEP",
                message=f"A mudança agendada será desfeita: você continua no plano {PLANS[cur]['name']}.",
            )
            return out
        raise HTTPException(409, "Você já está neste plano")
    if target == "FREE":
        out.update(action="CANCEL_AT_PERIOD_END", effective_at=end.isoformat() if end else None,
                   message="O plano Gratuito começa no fim do período já pago; até lá você mantém tudo. Sem reembolso do período corrente. Seus dados e a exportação continuam disponíveis.")  # fmt: skip
        return out
    if cur == "FREE":
        out.update(action="START", charge_today_cents=new_price, effective_at=now.isoformat(),
                   message=f"Cobrança de {format_brl(new_price)} hoje e a cada {PERIOD_DAYS} dias. Se estiver no teste grátis, ele termina agora.")  # fmt: skip
        return out
    if RANK[target] > RANK[cur]:
        remaining = max((end - now).days, 0) if end else 0
        charge = (new_price - old_price) * remaining // PERIOD_DAYS
        out.update(action="UPGRADE_NOW", charge_today_cents=charge, effective_at=now.isoformat(),
                   message=f"Vale agora. Cobrança proporcional de {format_brl(charge)} pelos {remaining} dias restantes do período; depois {format_brl(new_price)} a cada {PERIOD_DAYS} dias.")  # fmt: skip
        return out
    out.update(action="DOWNGRADE_AT_PERIOD_END", effective_at=end.isoformat() if end else None,
               message=f"Você mantém o plano atual até o fim do período pago; depois passa a {PLANS[target]['name']} ({format_brl(new_price)}). Sem reembolso do período corrente.")  # fmt: skip
    return out
