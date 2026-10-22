import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import AuthContext, get_auth
from app.core.security import (
    decrypt_secret,
    hash_password,
    hash_token,
    new_token,
    verify_password,
    verify_totp,
)  # fmt: skip
from app.db.base import utcnow
from app.db.models import Membership, Organization, Role, User, UserSession
from app.db.session import get_db

router = APIRouter(prefix="/api/v1", tags=["auth"])


TERMS_VERSION = "2026-10-minuta"
_COMMON = {"1234567890", "0123456789", "qwertyuiop", "password12", "senha12345", "1q2w3e4r5t"}


def check_password(password: str, email: str = "", name: str = "") -> None:
    """Política simples e honesta: tamanho, não ser trivial e não conter o e-mail/nome."""
    low = password.lower()
    local = email.split("@")[0].lower()
    if (
        len(set(password)) < 5
        or low in _COMMON
        or re.fullmatch(r"\d+", password)
        or (len(local) >= 4 and local in low)
        or (len(name.strip()) >= 4 and name.strip().lower() in low)
    ):
        raise HTTPException(422, "Senha fraca: evite sequências, só números e seu nome ou e-mail.")


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)
    accept_terms: bool = False


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)
    otp: str | None = Field(default=None, max_length=20)  # código TOTP ou de recuperação


class SessionOut(BaseModel):
    user_id: str
    email: str
    name: str
    csrf_token: str


def _start_session(db: Session, user: User, response: Response) -> SessionOut:
    settings = get_settings()
    token, csrf = new_token(), new_token()
    now = utcnow()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            created_at=now,
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
        )
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return SessionOut(user_id=str(user.id), email=user.email, name=user.name, csrf_token=csrf)


@router.post("/auth/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)) -> SessionOut:
    email = body.email.lower()
    if not body.accept_terms:
        raise HTTPException(422, "É preciso aceitar os Termos de Uso e a Política de Privacidade.")
    check_password(body.password, email, body.name)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "E-mail já cadastrado")
    user = User(
        email=email, name=body.name, password_hash=hash_password(body.password),
        terms_accepted_at=utcnow(), terms_version=TERMS_VERSION,
    )  # fmt: skip
    org = Organization(name=body.organization_name)
    db.add_all([user, org])
    db.flush()
    db.add(Membership(user_id=user.id, organization_id=org.id, role=Role.OWNER))
    audit(
        db,
        action="auth.register",
        object_type="user",
        object_id=user.id,
        organization_id=org.id,
        actor_user_id=user.id,
    )
    out = _start_session(db, user, response)
    db.commit()
    return out


def _aware(dt):
    from datetime import UTC

    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def use_recovery_code(user: User, code: str) -> bool:
    h = hash_token(code.strip().lower())
    if h in (user.mfa_recovery_hashes or []):
        user.mfa_recovery_hashes = [x for x in user.mfa_recovery_hashes if x != h]  # uso único
        return True
    return False


def second_factor_ok(user: User, otp: str) -> bool:
    secret = decrypt_secret(user.mfa_secret_enc) if user.mfa_secret_enc else None
    return bool(secret and verify_totp(secret, otp)) or use_recovery_code(user, otp)


@router.post("/auth/login", response_model=SessionOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> SessionOut:
    cfg = get_settings()
    now = utcnow()
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is not None and user.deleted_at is not None:
        user = None
    if user is not None and user.locked_until and _aware(user.locked_until) > now:
        wait = int((_aware(user.locked_until) - now).total_seconds() // 60) + 1
        raise HTTPException(429, f"Muitas tentativas. Tente novamente em {wait} min.")
    ok = verify_password(user.password_hash if user else None, body.password) and user is not None
    if ok and user.mfa_enabled:
        if not body.otp:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA_REQUIRED")
        ok = second_factor_ok(user, body.otp)
    if not ok:
        if user is not None:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= cfg.login_max_failures:
                user.failed_logins = 0
                user.locked_until = now + timedelta(minutes=cfg.login_lock_minutes)
                audit(db, action="auth.lockout", object_type="user", object_id=user.id,
                      actor_user_id=user.id)  # fmt: skip
            audit(db, action="auth.login_failed", object_type="user", object_id=user.id,
                  actor_user_id=user.id)  # fmt: skip
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas")
    user.failed_logins, user.locked_until = 0, None
    out = _start_session(db, user, response)
    audit(db, action="auth.login", object_type="user", object_id=user.id, actor_user_id=user.id)
    db.commit()
    return out


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
) -> None:
    db.delete(auth.session)
    db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")


class MeOut(BaseModel):
    id: str
    email: str
    name: str
    csrf_token: str
    mfa_enabled: bool = False
    organizations: list[dict]


@router.get("/me", response_model=MeOut)
def me(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> MeOut:
    rows = db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == auth.user.id)
    ).all()
    return MeOut(
        id=str(auth.user.id),
        email=auth.user.email,
        name=auth.user.name,
        csrf_token=auth.session.csrf_token,
        mfa_enabled=auth.user.mfa_enabled,
        organizations=[{"id": str(o.id), "name": o.name, "role": str(r)} for o, r in rows],
    )
