from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import AuthContext, get_auth
from app.core.security import hash_password, hash_token, new_token, verify_password
from app.db.base import utcnow
from app.db.models import Membership, Organization, Role, User, UserSession
from app.db.session import get_db

router = APIRouter(prefix="/api/v1", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


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
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "E-mail já cadastrado")
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
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


@router.post("/auth/login", response_model=SessionOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> SessionOut:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas")
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
        organizations=[{"id": str(o.id), "name": o.name, "role": str(r)} for o, r in rows],
    )
