from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import AuthContext, TenantContext, get_auth, get_tenant
from app.core.rbac import P_ORG_UPDATE
from app.db.models import Membership, Organization, Role
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    legal_type: str = Field(default="personal", max_length=50)
    location_uf: str | None = Field(default=None, min_length=2, max_length=2)
    location_city: str | None = Field(default=None, max_length=120)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location_uf: str | None = Field(default=None, min_length=2, max_length=2)
    location_city: str | None = Field(default=None, max_length=120)


class OrgOut(BaseModel):
    id: str
    name: str
    legal_type: str
    timezone: str
    currency: str
    location_uf: str | None
    location_city: str | None


def _out(o: Organization) -> OrgOut:
    return OrgOut(
        id=str(o.id),
        name=o.name,
        legal_type=o.legal_type,
        timezone=o.timezone,
        currency=o.currency,
        location_uf=o.location_uf,
        location_city=o.location_city,
    )


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
def create_org(
    body: OrgIn, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
) -> OrgOut:
    org = Organization(**body.model_dump())
    db.add(org)
    db.flush()
    db.add(Membership(user_id=auth.user.id, organization_id=org.id, role=Role.OWNER))
    audit(
        db,
        action="org.create",
        object_type="organization",
        object_id=org.id,
        organization_id=org.id,
        actor_user_id=auth.user.id,
    )
    db.commit()
    return _out(org)


@router.get("/current", response_model=OrgOut)
def current_org(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)) -> OrgOut:
    return _out(db.get(Organization, ctx.organization_id))


@router.patch("/current", response_model=OrgOut)
def update_org(
    body: OrgPatch, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
) -> OrgOut:
    ctx.require(P_ORG_UPDATE)
    org = db.get(Organization, ctx.organization_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(org, key, value)
    audit(
        db,
        action="org.update",
        object_type="organization",
        object_id=org.id,
        organization_id=org.id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _out(org)
