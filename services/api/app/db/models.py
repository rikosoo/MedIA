import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
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


class ReturnPolicy(Base, IdMixin, TimestampMixin):
    __tablename__ = "return_policies"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    occurrence_rate_bps: Mapped[int] = mapped_column(Integer)  # % de pacientes que retornam
    delay_days: Mapped[int] = mapped_column(Integer)
    duration_mode: Mapped[str] = mapped_column(String(10))  # MINUTES | PERCENT
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_percent_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_mode: Mapped[str] = mapped_column(String(10))  # FREE | PARTIAL | FULL | CUSTOM
    price_percent_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    free_within_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    no_show_rate_bps: Mapped[int] = mapped_column(Integer, default=0)
    cost_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClinicService(Base, IdMixin, TimestampMixin):
    __tablename__ = "clinic_services"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    price_cents: Mapped[int] = mapped_column(BigInteger)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    cost_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    fee_bps: Mapped[int] = mapped_column(Integer, default=0)  # taxa de cartão/meio de pagamento
    no_show_rate_bps: Mapped[int] = mapped_column(Integer, default=0)
    return_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("return_policies.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Recurrence(Base, IdMixin, TimestampMixin):
    """Regra de despesa recorrente. Projeta valores (planejado); não é dinheiro realizado."""

    __tablename__ = "recurrences"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    description: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    frequency: Mapped[str] = mapped_column(String(10))  # MONTHLY | YEARLY
    start_date: Mapped[date] = mapped_column(Date)
    stop_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Transaction(Base, IdMixin, TimestampMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_tx_org_competence", "organization_id", "competence_month"),
        Index("ix_tx_org_dedupe", "organization_id", "dedupe_hash"),
        Index("ix_tx_org_rowhash", "organization_id", "import_row_hash"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(10))  # INCOME | EXPENSE
    status: Mapped[str] = mapped_column(String(10))  # PLANNED | REALIZED
    competence_month: Mapped[date] = mapped_column(Date)  # sempre dia 1
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[date | None] = mapped_column(Date, nullable=True)  # data de caixa
    amount_cents: Mapped[int] = mapped_column(BigInteger)  # sempre positivo
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False)  # estorno inverte o sinal
    category: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(200))
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    work_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("work_sources.id"), nullable=True
    )
    recurrence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recurrences.id"), nullable=True
    )
    dedupe_hash: Mapped[str] = mapped_column(String(64))
    # Receita realizada de uma fonte: quantas unidades (plantões/pacientes) ela representa.
    units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clinic_service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic_services.id"), nullable=True
    )
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_jobs.id"), nullable=True
    )
    # Impressão digital da linha de CSV que originou/conciliou este lançamento: extratos com
    # períodos sobrepostos não reimportam a mesma linha, nem depois de conciliada.
    import_row_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AvailabilitySlot(Base, IdMixin, TimestampMixin):
    """Janela em que o médico pode atender (UTC)."""

    __tablename__ = "availability_slots"
    __table_args__ = (Index("ix_avail_org_user_start", "organization_id", "user_id", "starts_at"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    availability_type: Mapped[str] = mapped_column(String(20), default="CLINIC")


class ScheduleEvent(Base, IdMixin, TimestampMixin):
    __tablename__ = "schedule_events"
    __table_args__ = (Index("ix_event_org_user_start", "organization_id", "user_id", "starts_at"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200), default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="PLANNED")  # PLANNED|DONE|CANCELLED
    allow_overlap: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    work_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("work_sources.id"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Holiday(Base, IdMixin, TimestampMixin):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("organization_id", "date", "name", name="uq_holiday"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(12))  # NATIONAL|STATE|MUNICIPAL|CUSTOM
    # Sugestões automáticas nascem não confirmadas; só feriado confirmado fecha a agenda.
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class Leave(Base, IdMixin, TimestampMixin):
    """Férias/licença em dias inteiros. `PHYSICIAN` tira capacidade; `SECRETARY` afeta captação."""

    __tablename__ = "leaves"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    staff_role: Mapped[str] = mapped_column(String(12))  # PHYSICIAN | SECRETARY
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)  # inclusivo
    coverage_factor_bps: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimeConstraint(Base, IdMixin, TimestampMixin):
    """Limites de jornada. `month` nulo = padrão; mês específico sobrescreve o padrão."""

    __tablename__ = "time_constraints"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "month", name="uq_tc"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    month: Mapped[date | None] = mapped_column(Date, nullable=True)
    max_active_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_committed_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_rest_minutes: Mapped[int] = mapped_column(Integer, default=0)
    max_night_shifts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_off: Mapped[list] = mapped_column(JSON, default=list)  # ["2026-10-12", ...]


class MonthlyGoal(Base, IdMixin, TimestampMixin):
    """Meta pessoal do mês. Dados de renda pessoal: visíveis só para o próprio usuário."""

    __tablename__ = "monthly_goals"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "month", "metric_type", name="uq_goal"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    month: Mapped[date] = mapped_column(Date)
    metric_type: Mapped[str] = mapped_column(
        String(20)
    )  # PERSONAL_NET | CLINIC_PROFIT | GROSS_REVENUE
    target_cents: Mapped[int] = mapped_column(BigInteger)
    priority: Mapped[str] = mapped_column(String(20), default="BALANCED")
    use_reserves: Mapped[bool] = mapped_column(Boolean, default=False)
    reserve_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IncomePlan(Base, IdMixin, TimestampMixin):
    """Quantas unidades de uma fonte de renda se espera no mês (`month` nulo = padrão mensal)."""

    __tablename__ = "income_plans"
    __table_args__ = (
        UniqueConstraint("organization_id", "source_type", "source_id", "month", name="uq_plan"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    month: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_type: Mapped[str] = mapped_column(String(10))  # WORK | SERVICE
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    units: Mapped[int] = mapped_column(Integer)
    certainty: Mapped[str] = mapped_column(String(12))  # GUARANTEED | PROBABLE
    tax_rate_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # estimativa
    # Unidades adicionais aplicadas por um cenário do otimizador: sempre "provável".
    extra_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class OptimizerRun(Base, IdMixin, TimestampMixin):
    """Cada execução fica registrada com os parâmetros usados (auditoria e reprodutibilidade)."""

    __tablename__ = "optimizer_runs"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("monthly_goals.id"), nullable=True)
    month: Mapped[date] = mapped_column(Date)
    metric_type: Mapped[str] = mapped_column(String(20))
    engine_version: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(15))  # FEASIBLE | INFEASIBLE | ALREADY_MET
    baseline_cents: Mapped[int] = mapped_column(BigInteger)
    gap_cents: Mapped[int] = mapped_column(BigInteger)
    runtime_ms: Mapped[int] = mapped_column(Integer)
    params_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict] = mapped_column(JSON)


class OptimizerScenario(Base, IdMixin, TimestampMixin):
    __tablename__ = "optimizer_scenarios"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("optimizer_runs.id"), index=True)
    ranking: Mapped[int] = mapped_column(Integer)
    objective: Mapped[str] = mapped_column(String(20))  # objetivo principal do cenário
    output_json: Mapped[dict] = mapped_column(JSON)
    feasibility_status: Mapped[str] = mapped_column(String(15), default="FEASIBLE")
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)


class SavedPlan(Base, IdMixin, TimestampMixin):
    """Cenário salvo (snapshot imutável) e, se aplicado, o que mudou no planejamento."""

    __tablename__ = "saved_plans"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("optimizer_scenarios.id"), index=True)
    month: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="SAVED")  # SAVED|APPLIED|UNDONE
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    applied_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ImportJob(Base, IdMixin, TimestampMixin):
    """Importação de CSV concluída. O hash do conteúdo garante que o mesmo arquivo não entre 2x."""

    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_org_hash", "organization_id", "file_hash"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(20), default="TRANSACTIONS")
    status: Mapped[str] = mapped_column(String(10), default="COMPLETED")  # COMPLETED | UNDONE
    file_hash: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    reconciled_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[list] = mapped_column(JSON, default=list)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportJob(Base, IdMixin, TimestampMixin):
    """Relatório gerado. O arquivo fica guardado até `expires_at` (retenção limitada)."""

    __tablename__ = "report_jobs"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    template: Mapped[str] = mapped_column(String(20))
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM ou YYYY
    format: Mapped[str] = mapped_column(String(4))  # PDF | CSV
    state: Mapped[str] = mapped_column(String(10), default="DONE")
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportShare(Base, IdMixin, TimestampMixin):
    """Link temporário (ex.: para o contador). Só o hash do token fica no banco."""

    __tablename__ = "report_shares"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("report_jobs.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_access_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationSettings(Base, IdMixin, TimestampMixin):
    """Alertas são opt-in: desligados até o usuário ligar e escolher os tipos."""

    __tablename__ = "notification_settings"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_notif_settings"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    kinds: Mapped[list] = mapped_column(JSON, default=list)
    quiet_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Notification(Base, IdMixin, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notif_user_kind", "organization_id", "user_id", "kind"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    ref: Mapped[str] = mapped_column(String(7))  # competência a que se refere
    channel: Mapped[str] = mapped_column(String(10), default="IN_APP")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(300))  # genérico: sem valores financeiros
    route: Mapped[str] = mapped_column(String(60))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AcquisitionChannel(Base, IdMixin, TimestampMixin):
    """Canal de captação (Google, indicação...). Só dados agregados, nunca de pacientes."""

    __tablename__ = "acquisition_channels"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_channel_name"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    channel_type: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AcquisitionMetric(Base, IdMixin, TimestampMixin):
    __tablename__ = "acquisition_metrics"
    __table_args__ = (
        UniqueConstraint("organization_id", "month", "channel_id", name="uq_acq_month_channel"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    month: Mapped[date] = mapped_column(Date)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acquisition_channels.id"), index=True)
    leads_count: Mapped[int] = mapped_column(Integer, default=0)
    responded_in_time_count: Mapped[int] = mapped_column(Integer, default=0)
    bookings_count: Mapped[int] = mapped_column(Integer, default=0)
    attended_count: Mapped[int] = mapped_column(Integer, default=0)
    spend_cents: Mapped[int] = mapped_column(BigInteger, default=0)


class Forecast(Base, IdMixin, TimestampMixin):
    """Previsão salva: premissas e resultado congelados (uma hipótese, não uma promessa)."""

    __tablename__ = "forecasts"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    version: Mapped[str] = mapped_column(String(10))
    assumptions_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict] = mapped_column(JSON)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiConversation(Base, IdMixin, TimestampMixin):
    """Conversa do consultor: pessoal (só o dono lê), nunca altera dados."""

    __tablename__ = "ai_conversations"
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    summary: Mapped[str] = mapped_column(String(200))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiMessage(Base, IdMixin, TimestampMixin):
    __tablename__ = "ai_messages"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))  # USER | ASSISTANT
    content_redacted: Mapped[str] = mapped_column(String(2000))
    answer_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_calls_json: Mapped[list] = mapped_column(JSON, default=list)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)


class Subscription(Base, IdMixin, TimestampMixin):
    """Assinatura SaaS da organização. Sem provedor real: `provider` = simulated (ver docs)."""

    __tablename__ = "subscriptions"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), unique=True, index=True
    )
    plan: Mapped[str] = mapped_column(String(10), default="FREE")  # FREE|PRO|PRO_AI|CLINIC
    state: Mapped[str] = mapped_column(String(10), default="ACTIVE")  # ACTIVE | PAST_DUE
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_plan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), default="simulated")
    provider_customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payment_token: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payment_brand: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)


class Invoice(Base, IdMixin, TimestampMixin):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_invoice_idem"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    number: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(200))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10))  # PAID | FAILED
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)


class BillingEvent(Base, IdMixin, TimestampMixin):
    """Eventos do provedor já processados (webhook idempotente)."""

    __tablename__ = "billing_events"
    event_id: Mapped[str] = mapped_column(String(80), unique=True)
    event_type: Mapped[str] = mapped_column(String(60))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
