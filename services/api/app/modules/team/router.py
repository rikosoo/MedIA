import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_TEAM_MANAGE, P_TEAM_READ
from app.db.models import Membership, Role, User
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/team/members", tags=["team"])


class MemberIn(BaseModel):
    email: EmailStr
    role: Role


class RolePatch(BaseModel):
    role: Role


class MemberOut(BaseModel):
    id: str
    user_id: str
    email: str
    name: str
    role: str


def _out(m: Membership) -> MemberOut:
    return MemberOut(
        id=str(m.id), user_id=str(m.user_id), email=m.user.email, name=m.user.name, role=m.role
    )


def _get_member(db: Session, ctx: TenantContext, member_id: uuid.UUID) -> Membership:
    m = db.scalar(
        select(Membership).where(
            Membership.id == member_id, Membership.organization_id == ctx.organization_id
        )
    )
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membro não encontrado")
    return m


def _owner_count(db: Session, org_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).where(
            Membership.organization_id == org_id, Membership.role == Role.OWNER
        )
    )


@router.get("", response_model=list[MemberOut])
def list_members(
    ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
) -> list[MemberOut]:
    ctx.require(P_TEAM_READ)
    rows = db.scalars(
        select(Membership).where(Membership.organization_id == ctx.organization_id)
    ).all()
    return [_out(m) for m in rows]


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    body: MemberIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
) -> MemberOut:
    """Vincula usuário já cadastrado. Convite por e-mail fica para sprint posterior."""
    ctx.require(P_TEAM_MANAGE)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    if db.scalar(
        select(Membership).where(
            Membership.user_id == user.id, Membership.organization_id == ctx.organization_id
        )
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Usuário já é membro")
    m = Membership(user_id=user.id, organization_id=ctx.organization_id, role=body.role)
    db.add(m)
    db.flush()
    audit(
        db,
        action="team.add",
        object_type="membership",
        object_id=m.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
        metadata={"role": body.role.value},
    )
    db.commit()
    return _out(m)


@router.patch("/{member_id}", response_model=MemberOut)
def change_role(
    member_id: uuid.UUID,
    body: RolePatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
) -> MemberOut:
    ctx.require(P_TEAM_MANAGE)
    m = _get_member(db, ctx, member_id)
    if (
        m.role == Role.OWNER
        and body.role != Role.OWNER
        and _owner_count(db, ctx.organization_id) <= 1
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "A organização precisa de ao menos um OWNER")
    m.role = body.role
    audit(
        db,
        action="team.role_change",
        object_type="membership",
        object_id=m.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
        metadata={"role": body.role.value},
    )
    db.commit()
    return _out(m)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_member(
    member_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
) -> Response:
    ctx.require(P_TEAM_MANAGE)
    m = _get_member(db, ctx, member_id)
    if m.role == Role.OWNER and _owner_count(db, ctx.organization_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A organização precisa de ao menos um OWNER")
    audit(
        db,
        action="team.revoke",
        object_type="membership",
        object_id=m.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.delete(m)
    db.commit()  # acesso revogado de imediato: get_tenant consulta o vínculo a cada request
    return Response(status_code=status.HTTP_204_NO_CONTENT)
