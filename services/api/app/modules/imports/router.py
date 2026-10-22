import hashlib
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_FIN_READ, P_FIN_WRITE
from app.db.base import utcnow
from app.db.models import ImportJob, Transaction
from app.db.session import get_db
from app.modules.finances.router import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    TransactionIn,
    _as_input,
    _dedupe_hash,
    month_str,
    parse_month,
)
from finance_engine.csvio import (
    clean_text,
    norm_key,
    parse_amount,
    parse_bool,
    parse_competence,
    parse_date,
    read_table,
    unsanitize_cell,
)

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

MAX_ROWS = 5000
PREVIEW_ROWS = 200

ALIASES = {
    "competence": {"competencia", "mes", "competence_month", "mes_competencia"},
    "paid_at": {
        "pago_em",
        "data",
        "data_pagamento",
        "data_pgto",
        "paid_at",
        "date",
        "data_lancamento",
    },
    "type": {"tipo", "type"},
    "status": {"situacao", "status"},
    "category": {"categoria", "category"},
    "description": {"descricao", "description", "historico", "memo", "lancamento"},
    "amount": {"valor", "amount", "value", "valor_r"},
    "reversal": {"estorno", "reversal"},
    "units": {"unidades", "units", "quantidade"},
}
INCOME_WORDS = {"receita", "entrada", "credito", "income", "r"}
EXPENSE_WORDS = {"despesa", "saida", "debito", "expense", "d"}
REALIZED_WORDS = {"realizado", "pago", "recebido", "realized", "efetivado"}
PLANNED_WORDS = {"planejado", "previsto", "planned", "pendente"}


def _category_lookup() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for typ, cats in (("EXPENSE", EXPENSE_CATEGORIES), ("INCOME", INCOME_CATEGORIES)):
        for key, label in cats.items():
            out[(typ, norm_key(key))] = key
            out[(typ, norm_key(label))] = key
    return out


CATEGORIES = _category_lookup()
FALLBACK = {"EXPENSE": "outros", "INCOME": "outras_receitas"}


class ImportIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=2_000_000)
    dry_run: bool = True
    skip_invalid: bool = False  # importa as linhas válidas mesmo havendo erros
    allow_reimport: bool = False  # permite reenviar um arquivo idêntico a um já importado
    allow_in_file_duplicates: bool = False  # mantém linhas idênticas dentro do mesmo arquivo


@dataclass
class Row:
    line: int
    state: str = "NEW"  # NEW | DUPLICATE_EXISTING | DUPLICATE_IN_FILE | RECONCILE | ERROR
    body: TransactionIn | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    hash: str = ""
    reconcile_id: uuid.UUID | None = None
    reconcile_prev_hash: str = ""
    row_hash: str = ""  # impressão digital da linha do arquivo (data, descrição, valor, sentido)
    category_given: bool = False  # extrato bancário não traz categoria: concilia só por tipo/valor


def _pick(row: dict, key: str) -> str | None:
    for alias in ALIASES[key]:
        if alias in row and row[alias] != "":
            return row[alias]
    return None


def _parse_row(line: int, raw: dict) -> Row:
    r = Row(line)
    try:
        amount_txt = _pick(raw, "amount")
        if amount_txt is None:
            raise ValueError("coluna 'valor' ausente ou vazia")
        signed = parse_amount(amount_txt)
        if signed == 0:
            raise ValueError("valor zero")
        type_txt = _pick(raw, "type")
        if type_txt is not None:
            t = norm_key(type_txt)
            if t in INCOME_WORDS:
                typ = "INCOME"
            elif t in EXPENSE_WORDS:
                typ = "EXPENSE"
            else:
                raise ValueError(f"tipo inválido: {type_txt!r} (use receita ou despesa)")
            if signed < 0:
                r.warnings.append("valor negativo lido como positivo (o tipo já indica o sentido)")
        else:
            typ = "EXPENSE" if signed < 0 else "INCOME"  # extrato bancário: débito negativo
        amount = abs(signed)
        rev_txt = _pick(raw, "reversal")
        reversal = parse_bool(rev_txt) if rev_txt is not None else False

        paid_txt, comp_txt = _pick(raw, "paid_at"), _pick(raw, "competence")
        paid = parse_date(paid_txt) if paid_txt else None
        if comp_txt:
            competence = parse_competence(comp_txt)
        elif paid:
            competence = paid.replace(day=1)
        else:
            raise ValueError("informe a competência ou a data de pagamento")

        st_txt = _pick(raw, "status")
        if st_txt is not None:
            s = norm_key(st_txt)
            if s in REALIZED_WORDS:
                st = "REALIZED"
            elif s in PLANNED_WORDS:
                st = "PLANNED"
            else:
                raise ValueError(f"situação inválida: {st_txt!r} (use realizado ou planejado)")
        else:
            st = "REALIZED" if paid else "PLANNED"
        if st == "PLANNED" and paid:
            paid = None
            r.warnings.append("lançamento planejado: data de pagamento ignorada")

        cat_txt = _pick(raw, "category")
        r.category_given = bool(cat_txt)
        cat = CATEGORIES.get((typ, norm_key(cat_txt))) if cat_txt else None
        if cat is None:
            cat = FALLBACK[typ]
            if cat_txt:
                r.warnings.append(f"categoria {cat_txt!r} não reconhecida: usada '{cat}'")

        desc = clean_text(unsanitize_cell(_pick(raw, "description") or ""))
        if not desc:
            desc = (EXPENSE_CATEGORIES if typ == "EXPENSE" else INCOME_CATEGORIES)[cat]
        if len(desc) > 200:
            desc = desc[:200]
            r.warnings.append("descrição truncada em 200 caracteres")

        units_txt = _pick(raw, "units")
        units = int(units_txt) if units_txt else None

        r.body = TransactionIn(
            type=typ, status=st, competence_month=month_str(competence), paid_at=paid,
            amount_cents=amount, is_reversal=reversal, category=cat, description=desc, units=units,
        )  # fmt: skip
        r.hash = _dedupe_hash(r.body)
        r.row_hash = hashlib.sha256(
            "|".join(
                [typ, str(paid), competence.isoformat(), str(amount), str(reversal), desc.lower()]
            ).encode()
        ).hexdigest()
    except ValidationError as exc:
        r.state, r.message = "ERROR", exc.errors()[0]["msg"]
    except ValueError as exc:
        r.state, r.message = "ERROR", str(exc)
    return r


def _chunks(items: list, n: int = 500):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _classify(db: Session, ctx: TenantContext, rows: list[Row], allow_in_file: bool) -> None:
    ok = [r for r in rows if r.state != "ERROR"]
    existing: set[str] = set()
    imported_rows: set[str] = set()
    for chunk in _chunks([r.row_hash for r in ok]):
        imported_rows |= set(
            db.scalars(
                select(Transaction.import_row_hash).where(
                    Transaction.organization_id == ctx.organization_id,
                    Transaction.deleted_at.is_(None),
                    Transaction.import_row_hash.in_(chunk),
                )
            )
        )
    for chunk in _chunks([r.hash for r in ok]):
        existing |= set(
            db.scalars(
                select(Transaction.dedupe_hash).where(
                    Transaction.organization_id == ctx.organization_id,
                    Transaction.deleted_at.is_(None),
                    Transaction.dedupe_hash.in_(chunk),
                )
            )
        )
    months = {parse_month(r.body.competence_month) for r in ok if r.body.status == "REALIZED"}
    pool: list[Transaction] = []
    if months:
        pool = list(
            db.scalars(
                select(Transaction)
                .where(
                    Transaction.organization_id == ctx.organization_id,
                    Transaction.deleted_at.is_(None),
                    Transaction.status == "PLANNED",
                    Transaction.competence_month.in_(months),
                )
                .order_by(Transaction.created_at, Transaction.id)
            )
        )
    claimed: set[uuid.UUID] = set()

    def candidates(b: TransactionIn, category_given: bool) -> list[Transaction]:
        """Planejados com mesmo tipo, competência, valor e sentido; com categoria só se informada."""
        comp = parse_month(b.competence_month)
        return [
            t
            for t in pool
            if t.id not in claimed
            and (t.type, t.competence_month, t.amount_cents, t.is_reversal)
            == (b.type, comp, b.amount_cents, b.is_reversal)
            and (not category_given or t.category == b.category)
        ]

    seen: set[str] = set()
    for r in ok:
        if r.row_hash in imported_rows:
            r.state, r.message = (
                "DUPLICATE_EXISTING",
                "esta linha de arquivo já foi importada antes",
            )
        elif r.hash in existing:
            r.state, r.message = "DUPLICATE_EXISTING", "já existe um lançamento igual"
        elif r.hash in seen and not allow_in_file:
            r.state, r.message = "DUPLICATE_IN_FILE", "linha repetida no próprio arquivo"
        elif r.body.status == "REALIZED":
            cands = candidates(r.body, r.category_given)
            if cands:
                t = cands[0]
                # o hash do planejado muda ao virar realizado; se colidir, é duplicata
                future = TransactionIn(
                    **{**_as_input(t).model_dump(), "status": "REALIZED", "paid_at": r.body.paid_at}
                )
                if _dedupe_hash(future) in existing:
                    r.state, r.message = "DUPLICATE_EXISTING", "já existe um lançamento igual"
                else:
                    claimed.add(t.id)
                    r.state, r.reconcile_id = "RECONCILE", t.id
                    r.reconcile_prev_hash = t.dedupe_hash
                    r.message = f"concilia o planejado «{t.description}»"
                    if len(cands) > 1:
                        r.warnings.append(
                            f"{len(cands)} planejados com o mesmo valor: usado o mais antigo"
                        )
        seen.add(r.hash)


def _row_out(r: Row) -> dict:
    b = r.body
    return {
        "line": r.line,
        "state": r.state,
        "message": r.message,
        "warnings": r.warnings,
        "type": b.type if b else None,
        "competence_month": b.competence_month if b else None,
        "paid_at": b.paid_at.isoformat() if b and b.paid_at else None,
        "amount_cents": b.amount_cents if b else None,
        "is_reversal": b.is_reversal if b else None,
        "category": b.category if b else None,
        "description": b.description if b else None,
        "reconcile_with": str(r.reconcile_id) if r.reconcile_id else None,
    }


def _job_out(j: ImportJob) -> dict:
    return {
        "id": str(j.id), "filename": j.filename, "status": j.status, "row_count": j.row_count,
        "imported_count": j.imported_count, "reconciled_count": j.reconciled_count,
        "duplicate_count": j.duplicate_count, "error_count": j.error_count,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    }  # fmt: skip


@router.post("")
def import_csv(
    body: ImportIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Importa lançamentos de um CSV. `dry_run` (padrão) só mostra o que aconteceria.

    Idempotente: o mesmo arquivo não entra duas vezes, e linhas já existentes ou repetidas são
    puladas, então reenviar não cria faturamento duplicado.
    """
    ctx.require(P_FIN_WRITE)
    try:
        _, table = read_table(body.content, MAX_ROWS)
    except ValueError as exc:
        raise HTTPException(422, f"CSV inválido: {exc}") from None
    rows = [_parse_row(line, raw) for line, raw in table]
    if not rows:
        raise HTTPException(422, "O arquivo não tem linhas de dados")
    _classify(db, ctx, rows, body.allow_in_file_duplicates)

    counts = {
        "new": sum(r.state == "NEW" for r in rows),
        "reconcile": sum(r.state == "RECONCILE" for r in rows),
        "duplicates": sum(r.state.startswith("DUPLICATE") for r in rows),
        "errors": sum(r.state == "ERROR" for r in rows),
    }
    file_hash = hashlib.sha256(
        "\n".join(ln.strip() for ln in body.content.lstrip("﻿").splitlines() if ln.strip()).encode()
    ).hexdigest()
    previous = db.scalar(
        select(ImportJob).where(
            ImportJob.organization_id == ctx.organization_id,
            ImportJob.file_hash == file_hash,
            ImportJob.status == "COMPLETED",
        )
    )
    preview = [_row_out(r) for r in rows[:PREVIEW_ROWS]]
    errors = [{"line": r.line, "message": r.message} for r in rows if r.state == "ERROR"]
    base = {
        "dry_run": body.dry_run, "counts": counts, "rows": preview, "errors": errors,
        "truncated_preview": len(rows) > PREVIEW_ROWS,
        "already_imported": _job_out(previous) if previous else None,
    }  # fmt: skip
    if body.dry_run:
        return {**base, "job": None}

    if counts["errors"] and not body.skip_invalid:
        raise HTTPException(
            422,
            detail={
                "message": "O arquivo tem linhas inválidas; corrija ou importe só as válidas",
                "errors": errors,
            },  # fmt: skip
        )
    if previous and not body.allow_reimport:
        raise HTTPException(409, detail={"message": "Este arquivo já foi importado",
                                         "job": _job_out(previous)})  # fmt: skip

    job = ImportJob(
        organization_id=ctx.organization_id, created_by=ctx.user.id, filename=body.filename.split("/")[-1][:255],
        file_hash=file_hash, row_count=len(rows), imported_count=counts["new"],
        reconciled_count=counts["reconcile"], duplicate_count=counts["duplicates"],
        error_count=counts["errors"], errors_json=errors[:200],
    )  # fmt: skip
    db.add(job)
    db.flush()
    created, reconciled = [], []
    for r in rows:
        if r.state == "NEW":
            t = Transaction(
                organization_id=ctx.organization_id, created_by=ctx.user.id, import_job_id=job.id,
                dedupe_hash=r.hash, import_row_hash=r.row_hash,
                competence_month=parse_month(r.body.competence_month),
                **r.body.model_dump(exclude={"allow_duplicate", "competence_month"}),
            )  # fmt: skip
            db.add(t)
            db.flush()
            created.append(str(t.id))
        elif r.state == "RECONCILE":
            t = db.get(Transaction, r.reconcile_id)
            t.status, t.paid_at = "REALIZED", r.body.paid_at
            t.dedupe_hash = _dedupe_hash(_as_input(t))
            t.import_row_hash = r.row_hash
            reconciled.append({"id": str(t.id), "prev_hash": r.reconcile_prev_hash})
    job.result_json = {"created_ids": created, "reconciled": reconciled}
    audit(db, action="import.complete", object_type="import_job", object_id=job.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"rows": len(rows), "imported": len(created), "reconciled": len(reconciled)})  # fmt: skip
    db.commit()
    return {**base, "job": _job_out(job)}


@router.get("")
def list_imports(
    limit: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    ctx.require(P_FIN_READ)
    rows = db.scalars(
        select(ImportJob)
        .where(ImportJob.organization_id == ctx.organization_id)
        .order_by(ImportJob.created_at.desc(), ImportJob.id)
        .limit(limit)
    )
    return [_job_out(j) for j in rows]


@router.post("/{job_id}/undo")
def undo_import(
    job_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Desfaz uma importação: exclui o que ela criou e devolve ao planejado o que ela conciliou."""
    ctx.require(P_FIN_WRITE)
    job = db.scalar(
        select(ImportJob).where(
            ImportJob.id == job_id, ImportJob.organization_id == ctx.organization_id
        )
    )
    if job is None:
        raise HTTPException(404, "Importação não encontrada")
    if job.status != "COMPLETED":
        raise HTTPException(409, "Esta importação já foi desfeita")
    removed = 0
    for t in db.scalars(select(Transaction).where(Transaction.import_job_id == job.id)):
        if t.deleted_at is None:
            t.deleted_at = utcnow()
            removed += 1
    reverted = 0
    for item in job.result_json.get("reconciled", []):
        t = db.get(Transaction, uuid.UUID(item["id"]))
        if t is not None and t.deleted_at is None and t.status == "REALIZED":
            t.status, t.paid_at, t.dedupe_hash = "PLANNED", None, item["prev_hash"]
            t.import_row_hash = None
            reverted += 1
    job.status, job.undone_at = "UNDONE", utcnow()
    audit(db, action="import.undo", object_type="import_job", object_id=job.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"removed": removed, "reverted": reverted})  # fmt: skip
    db.commit()
    return {**_job_out(job), "removed": removed, "reverted_to_planned": reverted}
