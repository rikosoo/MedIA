import uuid

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import AuditLog


def audit(
    db: Session,
    *,
    action: str,
    object_type: str,
    object_id: object | None = None,
    organization_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> None:
    """Registra evento. Nunca inclua senhas, tokens ou valores financeiros em `metadata`."""
    db.add(
        AuditLog(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=str(object_id) if object_id else None,
            timestamp=utcnow(),
            metadata_json=metadata or {},
        )
    )
