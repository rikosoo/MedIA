import hashlib
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_FIN_READ, P_FIN_WRITE, P_WORK_READ_ALL
from app.db.base import utcnow
from app.db.models import Recurrence, Transaction, WorkSource
from app.db.session import get_db
from finance_engine import occurrences_in_month

router = APIRouter(prefix="/api/v1", tags=["finances"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
Money = Field(gt=0, le=10**12)

EXPENSE_CATEGORIES = {
    "aluguel": "Aluguel", "condominio": "Condomínio", "utilidades": "Água/luz/internet",
    "secretaria": "Secretaria e encargos", "papelaria": "Papelaria", "copa": "Café/água",
    "limpeza": "Limpeza", "material": "Material", "software": "Software",
    "equipamentos": "Equipamentos", "manutencao": "Manutenção", "marketing": "Marketing",
    "contabilidade": "Contabilidade", "taxas_cartao": "Taxas de cartão", "impostos": "Impostos",
    "deslocamento": "Deslocamento", "treinamento": "Treinamento", "outros": "Outros",
}  # fmt: skip
INCOME_CATEGORIES = {
    "consultas": "Consultas", "procedimentos": "Procedimentos", "plantoes": "Plantões",
    "outras_receitas": "Outras receitas",
}  # fmt: skip


def parse_month(value: str) -> date:
    y, m = value.split("-")
    return date(int(y), int(m), 1)


def month_str(d: date) -> str:
    return d.strftime("%Y-%m")


@router.get("/finance/categories")
def categories(ctx: TenantContext = Depends(get_tenant)):
    ctx.require(P_FIN_READ)
    return {"expense": EXPENSE_CATEGORIES, "income": INCOME_CATEGORIES}


# ---------- Lançamentos ----------


class TransactionIn(BaseModel):
    type: str = Field(pattern="^(INCOME|EXPENSE)$")
    status: str = Field(pattern="^(PLANNED|REALIZED)$")
    competence_month: str = Field(pattern=MONTH_RE)
    due_date: date | None = None
    paid_at: date | None = None
    amount_cents: int = Money
    is_reversal: bool = False
    category: str
    description: str = Field(min_length=1, max_length=200)
    is_fixed: bool = False
    work_source_id: uuid.UUID | None = None
    allow_duplicate: bool = False

    @model_validator(mode="after")
    def _coherent(self):
        cats = EXPENSE_CATEGORIES if self.type == "EXPENSE" else INCOME_CATEGORIES
        if self.category not in cats:
            raise ValueError("categoria inválida para o tipo do lançamento")
        if self.status == "REALIZED" and self.paid_at is None:
            raise ValueError("lançamento realizado exige paid_at (data de caixa)")
        if self.status == "PLANNED" and self.paid_at is not None:
            raise ValueError("lançamento planejado não pode ter paid_at")
        return self


class TransactionPatch(BaseModel):
    model_config = {"extra": "forbid"}
    status: str | None = None
    competence_month: str | None = None
    due_date: date | None = None
    paid_at: date | None = None
    amount_cents: int | None = None
    is_reversal: bool | None = None
    category: str | None = None
    description: str | None = None
    is_fixed: bool | None = None
    work_source_id: uuid.UUID | None = None


def _signed(t: Transaction) -> int:
    """Receita soma, despesa subtrai; estorno inverte o efeito."""
    sign = 1 if t.type == "INCOME" else -1
    return sign * (-1 if t.is_reversal else 1) * t.amount_cents


def _tx_out(t: Transaction) -> dict:
    return {
        "id": str(t.id),
        "type": t.type,
        "status": t.status,
        "competence_month": month_str(t.competence_month),
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "paid_at": t.paid_at.isoformat() if t.paid_at else None,
        "amount_cents": t.amount_cents,
        "is_reversal": t.is_reversal,
        "signed_cents": _signed(t),  # efeito no resultado: despesa negativa, estorno inverte
        "category": t.category,
        "description": t.description,
        "is_fixed": t.is_fixed,
        "work_source_id": str(t.work_source_id) if t.work_source_id else None,
        "recurrence_id": str(t.recurrence_id) if t.recurrence_id else None,
    }


def _dedupe_hash(b: TransactionIn) -> str:
    key = "|".join(
        [
            b.type,
            b.competence_month,
            str(b.paid_at or b.due_date or ""),
            str(b.amount_cents),
            str(b.is_reversal),
            b.category,
            " ".join(b.description.lower().split()),
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()


def _tx_q(ctx: TenantContext):
    return select(Transaction).where(
        Transaction.organization_id == ctx.organization_id, Transaction.deleted_at.is_(None)
    )


def _get_tx(db: Session, ctx: TenantContext, tid: uuid.UUID) -> Transaction:
    t = db.scalar(_tx_q(ctx).where(Transaction.id == tid))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lançamento não encontrado")
    return t


def _check_work_ref(db: Session, ctx: TenantContext, wid: uuid.UUID | None) -> None:
    if wid is None:
        return
    q = select(WorkSource.id).where(
        WorkSource.id == wid,
        WorkSource.organization_id == ctx.organization_id,
        WorkSource.deleted_at.is_(None),
    )
    if not ctx.can(P_WORK_READ_ALL):
        q = q.where(WorkSource.owner_user_id == ctx.user.id)
    if db.scalar(q) is None:
        raise HTTPException(422, "Trabalho inválido")


def _check_duplicate(
    db: Session, ctx: TenantContext, h: str, exclude: uuid.UUID | None = None
) -> None:
    q = _tx_q(ctx).where(Transaction.dedupe_hash == h)
    if exclude:
        q = q.where(Transaction.id != exclude)
    if db.scalar(q) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Lançamento possivelmente duplicado. Confirme com allow_duplicate=true.",
        )


@router.get("/transactions")
def list_transactions(
    month: str | None = Query(default=None, pattern=MONTH_RE),
    type: str | None = Query(default=None, pattern="^(INCOME|EXPENSE)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_FIN_READ)
    q = _tx_q(ctx)
    if month:
        q = q.where(Transaction.competence_month == parse_month(month))
    if type:
        q = q.where(Transaction.type == type)
    q = q.order_by(Transaction.competence_month.desc(), Transaction.created_at, Transaction.id)
    return [_tx_out(t) for t in db.scalars(q.limit(limit).offset(offset))]


@router.post("/transactions", status_code=status.HTTP_201_CREATED)
def create_transaction(
    body: TransactionIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_FIN_WRITE)
    _check_work_ref(db, ctx, body.work_source_id)
    h = _dedupe_hash(body)
    if not body.allow_duplicate:
        _check_duplicate(db, ctx, h)
    t = Transaction(
        organization_id=ctx.organization_id,
        created_by=ctx.user.id,
        dedupe_hash=h,
        **body.model_dump(exclude={"allow_duplicate", "competence_month"}),
        competence_month=parse_month(body.competence_month),
    )
    db.add(t)
    db.flush()
    audit(
        db,
        action="finance.tx_create",
        object_type="transaction",
        object_id=t.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
        metadata={"type": t.type, "status": t.status},
    )
    db.commit()
    return _tx_out(t)


@router.patch("/transactions/{tx_id}")
def update_transaction(
    tx_id: uuid.UUID,
    body: TransactionPatch,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_FIN_WRITE)
    t = _get_tx(db, ctx, tx_id)
    current = {
        "type": t.type, "status": t.status, "competence_month": month_str(t.competence_month),
        "due_date": t.due_date, "paid_at": t.paid_at, "amount_cents": t.amount_cents,
        "is_reversal": t.is_reversal, "category": t.category, "description": t.description,
        "is_fixed": t.is_fixed, "work_source_id": t.work_source_id,
    }  # fmt: skip
    try:
        valid = TransactionIn(**(current | body.model_dump(exclude_unset=True)))
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from None
    if "work_source_id" in body.model_fields_set:
        _check_work_ref(db, ctx, valid.work_source_id)
    h = _dedupe_hash(valid)
    _check_duplicate(db, ctx, h, exclude=t.id)
    for k, v in valid.model_dump(exclude={"allow_duplicate", "type"}).items():
        setattr(t, k, parse_month(v) if k == "competence_month" else v)
    t.dedupe_hash = h
    audit(
        db,
        action="finance.tx_update",
        object_type="transaction",
        object_id=t.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _tx_out(t)


@router.delete("/transactions/{tx_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    tx_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_FIN_WRITE)
    t = _get_tx(db, ctx, tx_id)
    t.deleted_at = utcnow()
    audit(
        db,
        action="finance.tx_delete",
        object_type="transaction",
        object_id=t.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Recorrências (despesas planejadas) ----------


class RecurrenceIn(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    category: str
    amount_cents: int = Money
    frequency: str = Field(pattern="^(MONTHLY|YEARLY)$")
    start_date: date
    stop_date: date | None = None

    @model_validator(mode="after")
    def _coherent(self):
        if self.category not in EXPENSE_CATEGORIES:
            raise ValueError("categoria de despesa inválida")
        if self.stop_date is not None and self.stop_date < self.start_date:
            raise ValueError("stop_date anterior a start_date")
        return self


def _rec_out(r: Recurrence) -> dict:
    return {
        "id": str(r.id),
        "description": r.description,
        "category": r.category,
        "amount_cents": r.amount_cents,
        "frequency": r.frequency,
        "start_date": r.start_date.isoformat(),
        "stop_date": r.stop_date.isoformat() if r.stop_date else None,
    }


def _rec_q(ctx: TenantContext):
    return select(Recurrence).where(
        Recurrence.organization_id == ctx.organization_id, Recurrence.deleted_at.is_(None)
    )


def _get_rec(db: Session, ctx: TenantContext, rid: uuid.UUID) -> Recurrence:
    r = db.scalar(_rec_q(ctx).where(Recurrence.id == rid))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recorrência não encontrada")
    return r


@router.get("/recurrences")
def list_recurrences(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_FIN_READ)
    return [_rec_out(r) for r in db.scalars(_rec_q(ctx).order_by(Recurrence.created_at))]


@router.post("/recurrences", status_code=status.HTTP_201_CREATED)
def create_recurrence(
    body: RecurrenceIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_FIN_WRITE)
    r = Recurrence(organization_id=ctx.organization_id, created_by=ctx.user.id, **body.model_dump())
    db.add(r)
    db.flush()
    audit(
        db,
        action="finance.rec_create",
        object_type="recurrence",
        object_id=r.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _rec_out(r)


@router.delete("/recurrences/{rec_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recurrence(
    rec_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_FIN_WRITE)
    r = _get_rec(db, ctx, rec_id)
    r.deleted_at = utcnow()
    audit(
        db,
        action="finance.rec_delete",
        object_type="recurrence",
        object_id=r.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _materialized(db: Session, ctx: TenantContext, rec_id: uuid.UUID, month: date) -> bool:
    return (
        db.scalar(
            _tx_q(ctx).where(
                Transaction.recurrence_id == rec_id, Transaction.competence_month == month
            )
        )
        is not None
    )


@router.post("/recurrences/{rec_id}/materialize", status_code=status.HTTP_201_CREATED)
def materialize(
    rec_id: uuid.UUID,
    month: str = Query(pattern=MONTH_RE),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Cria o lançamento PLANEJADO do mês. Continua não sendo despesa realizada."""
    ctx.require(P_FIN_WRITE)
    r = _get_rec(db, ctx, rec_id)
    m = parse_month(month)
    occ = occurrences_in_month(r.frequency, r.start_date, r.stop_date, m)
    if not occ:
        raise HTTPException(422, "Recorrência não incide no mês")
    if _materialized(db, ctx, r.id, m):
        raise HTTPException(status.HTTP_409_CONFLICT, "Mês já materializado")
    body = TransactionIn(
        type="EXPENSE", status="PLANNED", competence_month=month, due_date=occ[0],
        amount_cents=r.amount_cents, category=r.category, description=r.description,
        is_fixed=True,
    )  # fmt: skip
    t = Transaction(
        organization_id=ctx.organization_id,
        created_by=ctx.user.id,
        recurrence_id=r.id,
        dedupe_hash=_dedupe_hash(body),
        **body.model_dump(exclude={"allow_duplicate", "competence_month"}),
        competence_month=m,
    )
    db.add(t)
    db.flush()
    audit(
        db,
        action="finance.rec_materialize",
        object_type="transaction",
        object_id=t.id,
        organization_id=ctx.organization_id,
        actor_user_id=ctx.user.id,
    )
    db.commit()
    return _tx_out(t)


# ---------- Resumo mensal ----------


@router.get("/finances/summary")
def summary(
    month: str = Query(pattern=MONTH_RE),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Competência (realizado vs planejado) separada do caixa (data de pagamento)."""
    ctx.require(P_FIN_READ)
    m = parse_month(month)
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)

    by_comp = list(db.scalars(_tx_q(ctx).where(Transaction.competence_month == m)))
    realized = [t for t in by_comp if t.status == "REALIZED"]
    planned = [t for t in by_comp if t.status == "PLANNED"]

    projected_cents = 0  # recorrências ainda sem lançamento no mês
    projected_items = []
    for r in db.scalars(_rec_q(ctx)):
        if occurrences_in_month(r.frequency, r.start_date, r.stop_date, m) and not _materialized(
            db, ctx, r.id, m
        ):
            projected_cents += r.amount_cents
            projected_items.append({"recurrence_id": str(r.id), "amount_cents": r.amount_cents})

    cash = list(
        db.scalars(
            _tx_q(ctx).where(
                Transaction.status == "REALIZED",
                Transaction.paid_at >= m,
                Transaction.paid_at < nxt,
            )
        )
    )

    def total(items, kind):
        return sum(abs(_signed(t)) * (1 if _signed(t) > 0 else -1) for t in items if t.type == kind)

    by_cat: dict[str, int] = {}
    for t in realized:
        if t.type == "EXPENSE":
            by_cat[t.category] = by_cat.get(t.category, 0) + (-_signed(t))
    income_realized = total(realized, "INCOME")
    expense_realized = -total(realized, "EXPENSE")  # custo líquido de estornos, positivo

    return {
        "month": month,
        "competence": {
            "income_realized_cents": income_realized,
            "expense_realized_cents": expense_realized,
            "result_realized_cents": income_realized - expense_realized,
            "income_planned_cents": total(planned, "INCOME"),
            "expense_planned_cents": -total(planned, "EXPENSE") + projected_cents,
            "expense_projected_from_recurrences_cents": projected_cents,
            "expense_by_category_cents": dict(sorted(by_cat.items())),
        },
        "cash": {
            "income_received_cents": total(cash, "INCOME"),
            "expense_paid_cents": -total(cash, "EXPENSE"),
            "net_cents": total(cash, "INCOME") + total(cash, "EXPENSE"),
        },
        "projected_recurrences": projected_items,
        "notes": [
            "Realizado = lançamentos com data de caixa. Planejado e projetado não são dinheiro.",
            "Competência e caixa podem divergir (ex.: serviço de setembro recebido em outubro).",
        ],
    }
