"""Conta do usuário: perfil, senha, 2FA, sessões, exportação, exclusão (LGPD) e primeiros passos."""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import AuthContext, TenantContext, get_auth, get_tenant
from app.core.rbac import (
    P_CLINIC_READ,
    P_CLINIC_WRITE,
    P_GOAL_WRITE,
    P_SCHED_WRITE,
    P_WORK_WRITE,
)  # fmt: skip
from app.core.security import (
    encrypt_secret,
    hash_password,
    hash_token,
    new_totp_secret,
    verify_password,
)  # fmt: skip
from app.db.base import Base, utcnow
from app.db.models import (
    AiConversation,
    AiMessage,
    AvailabilitySlot,
    ClinicService,
    IncomePlan,
    Membership,
    MonthlyGoal,
    Notification,
    NotificationSettings,
    OptimizerRun,
    Organization,
    Subscription,
    Transaction,
    User,
    UserSession,
    WorkSource,
)  # fmt: skip
from app.db.session import get_db
from app.modules.auth.router import check_password, second_factor_ok

router = APIRouter(prefix="/api/v1", tags=["account"])

# Tabelas cujo conteúdo precisa ser guardado após a exclusão (obrigação legal/fiscal e segurança).
RETAINED = {"invoices", "audit_logs", "billing_events", "organizations", "subscriptions"}


def _reauth(user: User, password: str, otp: str | None = None) -> None:
    """Ações sensíveis pedem a senha de novo (e o segundo fator, quando ativo)."""
    if not verify_password(user.password_hash, password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Senha incorreta")
    if user.mfa_enabled and not (otp and second_factor_ok(user, otp)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Código de verificação inválido")


# ---------- perfil e senha ----------


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.patch("/me")
def update_profile(
    body: ProfileIn, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
):
    auth.user.name = body.name.strip()
    audit(
        db,
        action="account.profile",
        object_type="user",
        object_id=auth.user.id,
        actor_user_id=auth.user.id,
    )
    db.commit()
    return {"name": auth.user.name}


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordIn, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
):
    u = auth.user
    if not verify_password(u.password_hash, body.current_password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Senha atual incorreta")
    check_password(body.new_password, u.email, u.name)
    u.password_hash = hash_password(body.new_password)
    db.execute(
        delete(UserSession).where(UserSession.user_id == u.id, UserSession.id != auth.session.id)
    )
    audit(
        db,
        action="account.password_changed",
        object_type="user",
        object_id=u.id,
        actor_user_id=u.id,
    )
    db.commit()
    return Response(status_code=204)


# ---------- 2FA (TOTP) ----------


@router.post("/auth/mfa/setup")
def mfa_setup(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    u = auth.user
    if u.mfa_enabled:
        raise HTTPException(409, "O 2FA já está ativo.")
    secret = new_totp_secret()
    u.mfa_secret_enc = encrypt_secret(secret)
    db.commit()
    uri = f"otpauth://totp/FinanceMed%20AI:{u.email}?secret={secret}&issuer=FinanceMed%20AI"
    return {"secret": secret, "otpauth_uri": uri}


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


@router.post("/auth/mfa/enable")
def mfa_enable(body: CodeIn, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    from app.core.security import decrypt_secret, verify_totp

    u = auth.user
    if u.mfa_enabled:
        raise HTTPException(409, "O 2FA já está ativo.")
    if not u.mfa_secret_enc or not verify_totp(decrypt_secret(u.mfa_secret_enc), body.code):
        raise HTTPException(
            422, "Código inválido. Confira o app autenticador e o horário do celular."
        )
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(8)]
    u.mfa_enabled = True
    u.mfa_recovery_hashes = [hash_token(c) for c in codes]
    audit(db, action="account.mfa_enabled", object_type="user", object_id=u.id, actor_user_id=u.id)
    db.commit()
    return {
        "recovery_codes": codes,
        "note": "Guarde estes códigos: cada um vale uma vez e não serão mostrados de novo.",
    }


class DisableIn(BaseModel):
    password: str = Field(max_length=128)
    code: str = Field(min_length=6, max_length=20)


@router.post("/auth/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
def mfa_disable(
    body: DisableIn, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
):
    u = auth.user
    if not u.mfa_enabled:
        raise HTTPException(409, "O 2FA não está ativo.")
    _reauth(u, body.password, body.code)
    u.mfa_enabled, u.mfa_secret_enc, u.mfa_recovery_hashes = False, None, []
    audit(db, action="account.mfa_disabled", object_type="user", object_id=u.id, actor_user_id=u.id)
    db.commit()
    return Response(status_code=204)


# ---------- sessões ----------


@router.get("/auth/sessions")
def sessions(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == auth.user.id)
        .order_by(UserSession.created_at.desc())
    )
    return [{"id": str(s.id), "created_at": s.created_at.isoformat(), "expires_at": s.expires_at.isoformat(),
             "current": s.id == auth.session.id} for s in rows]  # fmt: skip


@router.delete("/auth/sessions/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    sid: uuid.UUID, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
):
    s = db.scalar(
        select(UserSession).where(UserSession.id == sid, UserSession.user_id == auth.user.id)
    )
    if s is None:
        raise HTTPException(404, "Sessão não encontrada")
    db.delete(s)
    db.commit()
    return Response(status_code=204)


@router.post("/auth/sessions/revoke-others", status_code=status.HTTP_204_NO_CONTENT)
def revoke_others(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == auth.user.id, UserSession.id != auth.session.id
        )
    )
    audit(
        db,
        action="account.sessions_revoked",
        object_type="user",
        object_id=auth.user.id,
        actor_user_id=auth.user.id,
    )
    db.commit()
    return Response(status_code=204)


# ---------- exportação pessoal ----------


def _row(obj) -> dict:
    from app.modules.billing.router import _row as row

    return row(obj)


@router.get("/me/export")
def export_me(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)):
    """Dados pessoais do titular (LGPD): perfil, vínculos, preferências e o que é só dele."""
    import json

    u = auth.user
    data = {
        "exported_at": utcnow().isoformat(),
        "profile": {"id": str(u.id), "email": u.email, "name": u.name, "mfa_enabled": u.mfa_enabled,
                    "terms_accepted_at": u.terms_accepted_at.isoformat() if u.terms_accepted_at else None,
                    "terms_version": u.terms_version},  # fmt: skip
        "memberships": [{"organization_id": str(m.organization_id), "role": m.role}
                        for m in db.scalars(select(Membership).where(Membership.user_id == u.id))],  # fmt: skip
        "notification_settings": [_row(n) for n in db.scalars(select(NotificationSettings).where(NotificationSettings.user_id == u.id))],
        "goals": [_row(g) for g in db.scalars(select(MonthlyGoal).where(MonthlyGoal.user_id == u.id))],
        "work_sources": [_row(w) for w in db.scalars(select(WorkSource).where(WorkSource.owner_user_id == u.id))],
        "ai_conversations": [
            {**_row(c), "messages": [{"role": m.role, "content": m.content_redacted} for m in
                                     db.scalars(select(AiMessage).where(AiMessage.conversation_id == c.id))]}
            for c in db.scalars(select(AiConversation).where(AiConversation.owner_user_id == u.id))],
    }  # fmt: skip
    audit(db, action="account.export", object_type="user", object_id=u.id, actor_user_id=u.id)
    db.commit()
    return Response(json.dumps(data, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="meus-dados.json"'})  # fmt: skip


# ---------- exclusão de conta ----------


class DeleteIn(BaseModel):
    password: str = Field(max_length=128)
    email: str = Field(max_length=320)  # digitar o próprio e-mail confirma a intenção
    otp: str | None = Field(default=None, max_length=20)


def purge_organization(db: Session, org_id: uuid.UUID) -> None:
    """Apaga todo o conteúdo da organização, exceto o que precisa ser retido (faturas, auditoria)."""
    conv_ids = select(AiConversation.id).where(AiConversation.organization_id == org_id)
    db.execute(delete(AiMessage).where(AiMessage.conversation_id.in_(conv_ids)))
    db.execute(
        update(OptimizerRun).where(OptimizerRun.organization_id == org_id).values(goal_id=None)
    )
    for t in reversed(Base.metadata.sorted_tables):
        if t.name in RETAINED or "organization_id" not in t.c:
            continue
        db.execute(delete(t).where(t.c.organization_id == org_id))
    org = db.get(Organization, org_id)
    org.name, org.location_uf, org.location_city = "Organização removida", None, None
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    if sub is not None:
        sub.plan, sub.state, sub.pending_plan, sub.cancel_at_period_end = (
            "FREE",
            "ACTIVE",
            None,
            False,
        )
        sub.current_period_end = sub.past_due_since = None
        sub.payment_token = sub.payment_brand = sub.payment_last4 = sub.provider_customer_id = None


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    body: DeleteIn,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    """Exclui e anonimiza a conta. Faturas e auditoria são retidas sem dados pessoais."""
    u = auth.user
    if body.email.strip().lower() != u.email:
        raise HTTPException(422, "Digite seu e-mail exatamente como cadastrado para confirmar.")
    _reauth(u, body.password, body.otp)
    mine = list(db.scalars(select(Membership).where(Membership.user_id == u.id)))
    for m in mine:
        others = db.scalar(select(func.count(Membership.id)).where(
            Membership.organization_id == m.organization_id, Membership.user_id != u.id)) or 0  # fmt: skip
        if m.role == "OWNER" and others:
            raise HTTPException(409, "Você é proprietário de uma organização com outros membros. "
                                     "Remova a equipe (ou transfira a propriedade) antes de excluir a conta.")  # fmt: skip
    for m in mine:
        others = db.scalar(select(func.count(Membership.id)).where(
            Membership.organization_id == m.organization_id, Membership.user_id != u.id)) or 0  # fmt: skip
        org_id = m.organization_id
        if m.role == "OWNER" and not others:
            purge_organization(db, org_id)
        else:  # sai de uma organização alheia: some o que é só dele, o resto é registro do negócio
            db.execute(
                delete(MonthlyGoal).where(
                    MonthlyGoal.organization_id == org_id, MonthlyGoal.user_id == u.id
                )
            )
            db.execute(
                delete(NotificationSettings).where(
                    NotificationSettings.organization_id == org_id,
                    NotificationSettings.user_id == u.id,
                )
            )
            db.execute(
                delete(Notification).where(
                    Notification.organization_id == org_id, Notification.user_id == u.id
                )
            )
            convs = select(AiConversation.id).where(
                AiConversation.organization_id == org_id, AiConversation.owner_user_id == u.id
            )
            db.execute(delete(AiMessage).where(AiMessage.conversation_id.in_(convs)))
            db.execute(
                delete(AiConversation).where(
                    AiConversation.organization_id == org_id, AiConversation.owner_user_id == u.id
                )
            )
            db.execute(update(WorkSource).where(WorkSource.organization_id == org_id, WorkSource.owner_user_id == u.id)
                       .values(deleted_at=utcnow()))  # fmt: skip
            db.execute(delete(Membership).where(Membership.id == m.id))
        audit(db, action="account.deleted", object_type="organization", object_id=org_id,
              organization_id=org_id, actor_user_id=None)  # fmt: skip
    db.execute(delete(Membership).where(Membership.user_id == u.id))
    db.execute(delete(UserSession).where(UserSession.user_id == u.id))
    u.email = f"removido-{u.id}@invalid.local"
    u.name, u.password_hash = "Usuário removido", hash_password(secrets.token_urlsafe(32))
    u.mfa_enabled, u.mfa_secret_enc, u.mfa_recovery_hashes = False, None, []
    u.deleted_at = utcnow()
    db.commit()
    from app.core.config import get_settings

    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return Response(status_code=204)


# ---------- primeiros passos (onboarding) ----------


@router.get("/onboarding")
def onboarding(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    """Checklist derivado dos dados reais; mostra só os passos que o papel do usuário pode fazer."""
    org = ctx.organization_id

    def n(model, *where):
        return (db.scalar(select(func.count()).select_from(model).where(*where)) or 0) > 0

    steps = []

    def add(key, label, route, done, perm=None):
        if perm is None or ctx.can(perm):
            steps.append({"key": key, "label": label, "route": route, "done": bool(done)})

    add("work", "Cadastrar um trabalho ou renda (plantão, sobreaviso, CLT…)", "/app/work",
        n(WorkSource, WorkSource.organization_id == org, WorkSource.owner_user_id == ctx.user.id,
          WorkSource.deleted_at.is_(None)), P_WORK_WRITE)  # fmt: skip
    add("service", "Cadastrar um serviço do consultório com política de retorno", "/app/clinic",
        n(ClinicService, ClinicService.organization_id == org, ClinicService.deleted_at.is_(None)), P_CLINIC_WRITE)  # fmt: skip
    add("availability", "Informar sua disponibilidade na agenda", "/app/schedule",
        n(AvailabilitySlot, AvailabilitySlot.organization_id == org), P_SCHED_WRITE)  # fmt: skip
    add("plan", "Planejar o mês (quantas unidades de cada renda)", "/app/goals",
        n(IncomePlan, IncomePlan.organization_id == org), P_GOAL_WRITE)  # fmt: skip
    add("goal", "Definir a meta do mês", "/app/goals",
        n(MonthlyGoal, MonthlyGoal.organization_id == org, MonthlyGoal.user_id == ctx.user.id,
          MonthlyGoal.deleted_at.is_(None)), P_GOAL_WRITE)  # fmt: skip
    add("simulation", "Fazer a primeira simulação no Otimizador", "/app/optimizer",
        n(OptimizerRun, OptimizerRun.organization_id == org, OptimizerRun.created_by == ctx.user.id), P_GOAL_WRITE)  # fmt: skip
    add("realized", "Lançar o primeiro valor realizado", "/app/finances",
        n(Transaction, Transaction.organization_id == org, Transaction.status == "REALIZED",
          Transaction.deleted_at.is_(None)), P_CLINIC_READ)  # fmt: skip
    return {"steps": steps, "done": sum(s["done"] for s in steps), "total": len(steps),
            "complete": all(s["done"] for s in steps) if steps else True}  # fmt: skip
