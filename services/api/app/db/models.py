import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin


class Role(enum.StrEnum):
    OWNER = "OWNER"
    PHYSICIAN = "PHYSICIAN"
    MANAGER = "MANAGER"
    ASSISTANT = "ASSISTANT"
    ACCOUNTANT = "ACCOUNTANT"
    PLATFORM_SUPPORT = "PLATFORM_SUPPORT"


class WorkType(enum.StrEnum):
    CLINIC = "CLINIC"
    SHIFT = "SHIFT"  # plantão
    ON_CALL = "ON_CALL"  # sobreaviso
    EMPLOYMENT = "EMPLOYMENT"  # CLT
    TELEMEDICINE = "TELEMEDICINE"
    PROCEDURE = "PROCEDURE"
    OTHER = "OTHER"


class Regime(enum.StrEnum):
    PF = "PF"
    PJ = "PJ"
    CLT = "CLT"
    OTHER = "OTHER"


class UnitType(enum.StrEnum):
    PER_EVENT = "PER_EVENT"
    PER_CONSULT = "PER_CONSULT"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class Organization(Base, IdMixin, TimestampMixin):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))
    legal_type: Mapped[str] = mapped_column(String(50), default="personal")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    location_uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    location_city: Mapped[str | None] = mapped_column(String(120), nullable=True)


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(String(30), default="password")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(Base, IdMixin, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_membership"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    permissions_json: Mapped[dict] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship()


class UserSession(Base, IdMixin):
    __tablename__ = "user_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkSource(Base, IdMixin, TimestampMixin):
    __tablename__ = "work_sources"
    __table_args__ = (Index("ix_work_org_owner", "organization_id", "owner_user_id"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    work_type: Mapped[str] = mapped_column(String(30))
    regime: Mapped[str] = mapped_column(String(10))
    payer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    income_rule: Mapped["IncomeRule"] = relationship(
        back_populates="work_source", uselist=False, cascade="all, delete-orphan"
    )


class IncomeRule(Base, IdMixin, TimestampMixin):
    __tablename__ = "income_rules"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    work_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_sources.id"), unique=True)
    gross_cents: Mapped[int] = mapped_column(BigInteger)
    unit_type: Mapped[str] = mapped_column(String(20))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0)  # hora ativa
    commitment_minutes: Mapped[int] = mapped_column(Integer, default=0)  # hora comprometida
    travel_minutes: Mapped[int] = mapped_column(Integer, default=0)
    variable_cost_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # Retenção/tributo estimado em pontos-base (1500 = 15,00%). Sempre estimativa (PRD 7.5).
    tax_rate_bps: Mapped[int] = mapped_column(Integer, default=0)

    work_source: Mapped[WorkSource] = relationship(back_populates="income_rule")


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), index=True, nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(60))
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
