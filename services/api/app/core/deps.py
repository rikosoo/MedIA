import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rbac import permissions_for
from app.core.security import hash_token
from app.db.models import Membership, User, UserSession
from app.db.session import get_db

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class AuthContext:
    user: User
    session: UserSession


@dataclass
class TenantContext:
    user: User
    membership: Membership
    organization_id: uuid.UUID
    permissions: frozenset[str]

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Permissão insuficiente")

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def get_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token = request.cookies.get(get_settings().session_cookie_name)
    unauth = HTTPException(status.HTTP_401_UNAUTHORIZED, "Não autenticado")
    if not token:
        raise unauth
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if sess is None or _aware(sess.expires_at) <= datetime.now(UTC):
        raise unauth
    # CSRF: toda requisição que altera estado precisa do token da sessão no header.
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token")
        if sent != sess.csrf_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token inválido")
    user = db.get(User, sess.user_id)
    if user is None:
        raise unauth
    return AuthContext(user=user, session=sess)


def get_tenant(
    x_organization_id: str | None = Header(default=None),
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
) -> TenantContext:
    # 404 tanto para org inexistente quanto para org alheia: não revela existência.
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, "Organização não encontrada")
    if not x_organization_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Header X-Organization-Id obrigatório")
    try:
        org_id = uuid.UUID(x_organization_id)
    except ValueError:
        raise not_found from None
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == auth.user.id, Membership.organization_id == org_id
        )
    )
    if membership is None:
        raise not_found
    return TenantContext(
        user=auth.user,
        membership=membership,
        organization_id=org_id,
        permissions=permissions_for(membership.role, membership.permissions_json),
    )
