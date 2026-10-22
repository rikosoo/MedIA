import hashlib
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_FIN_READ, P_GOAL_READ, P_SCHED_READ
from app.db.base import utcnow
from app.db.models import ImportJob, ReportJob, ReportShare, Transaction
from app.db.session import get_db
from app.modules.billing.entitlements import require_feature
from app.modules.finances.router import _tx_q, parse_month
from app.modules.goals.projection import get_goal, month_str, shift_month
from app.modules.reports.builders import TEMPLATES, build, label_month
from app.modules.reports.render import to_csv_bytes, to_pdf_bytes
from app.modules.schedule.router import _tz

router = APIRouter(prefix="/api/v1", tags=["reports"])

RETENTION_DAYS = 30
MAX_SHARE_DAYS = 30
CONTENT_TYPES = {"PDF": "application/pdf", "CSV": "text/csv; charset=utf-8"}


class ReportIn(BaseModel):
    template: str
    period: str = Field(min_length=4, max_length=7)  # YYYY-MM (mensal) ou YYYY (anual)
    format: str = Field(default="PDF", pattern="^(PDF|CSV)$")
    as_of: date | None = None  # data de referência do "ritmo do mês" (padrão: hoje)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_of(db: Session, ctx: TenantContext, v: date | None) -> date:
    return v or _now().astimezone(_tz(db, ctx)).date()


@router.get("/reports/templates")
def templates(ctx: TenantContext = Depends(get_tenant)):
    """Relatórios que o usuário pode gerar (cada um exige as permissões dos dados que mostra)."""
    out = []
    for code, (name, kind, perms, desc) in TEMPLATES.items():
        out.append({"template": code, "name": name, "period": kind, "description": desc,
                    "allowed": all(ctx.can(p) for p in perms)})  # fmt: skip
    return out


@router.post("/reports/preview")
def preview(
    body: ReportIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Conteúdo estruturado do relatório, sem gerar arquivo nem guardar nada."""
    require_feature(db, ctx, "reports")
    return build(db, ctx, body.template, body.period, _now(), _as_of(db, ctx, body.as_of))


def _meta(r: ReportJob) -> dict:
    return {
        "id": str(r.id), "template": r.template, "template_name": TEMPLATES[r.template][0],
        "period": r.period, "format": r.format, "filename": r.filename, "size_bytes": r.size_bytes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "expires_at": r.expires_at.isoformat(),
    }  # fmt: skip


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.post("/reports/generate", status_code=status.HTTP_201_CREATED)
def generate(
    body: ReportIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    require_feature(db, ctx, "reports")
    now = _now()
    doc = build(db, ctx, body.template, body.period, now, _as_of(db, ctx, body.as_of))
    data = to_pdf_bytes(doc) if body.format == "PDF" else to_csv_bytes(doc)
    name = f"{body.template.lower()}-{body.period}.{body.format.lower()}"
    # limpeza de retenção: relatórios vencidos da organização saem junto com seus links
    for old in db.scalars(select(ReportJob).where(
        ReportJob.organization_id == ctx.organization_id, ReportJob.deleted_at.is_(None),
        ReportJob.expires_at < now,
    )):  # fmt: skip
        old.deleted_at, old.content = now, b""
    r = ReportJob(
        organization_id=ctx.organization_id, created_by=ctx.user.id, template=body.template,
        period=body.period, format=body.format, filename=name, content_type=CONTENT_TYPES[body.format],
        size_bytes=len(data), content=data, expires_at=now + timedelta(days=RETENTION_DAYS),
    )  # fmt: skip
    db.add(r)
    db.flush()
    audit(db, action="report.generate", object_type="report_job", object_id=r.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"template": body.template, "format": body.format})  # fmt: skip
    if body.template == "MONTHLY_RESULT":
        from app.modules.alerts.engine import notify

        notify(db, ctx, "REPORT_READY", body.period, now)
    db.commit()
    return _meta(r)


def _own_report(db: Session, ctx: TenantContext, rid: uuid.UUID) -> ReportJob:
    r = db.scalar(select(ReportJob).where(
        ReportJob.id == rid, ReportJob.organization_id == ctx.organization_id,
        ReportJob.created_by == ctx.user.id, ReportJob.deleted_at.is_(None)))  # fmt: skip
    if r is None or _aware(r.expires_at) < _now():
        raise HTTPException(404, "Relatório não encontrado ou expirado")
    return r


def _file(r: ReportJob) -> Response:
    return Response(
        content=r.content, media_type=r.content_type,
        headers={"Content-Disposition": f'attachment; filename="{r.filename}"',
                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )  # fmt: skip


@router.get("/reports")
def list_reports(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    rows = db.scalars(select(ReportJob).where(
        ReportJob.organization_id == ctx.organization_id, ReportJob.created_by == ctx.user.id,
        ReportJob.deleted_at.is_(None), ReportJob.expires_at >= _now(),
    ).order_by(ReportJob.created_at.desc(), ReportJob.id).limit(50))  # fmt: skip
    return [_meta(r) for r in rows]


@router.get("/reports/{report_id}/download")
def download(
    report_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    r = _own_report(db, ctx, report_id)
    audit(db, action="report.download", object_type="report_job", object_id=r.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return _file(r)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    r = _own_report(db, ctx, report_id)
    now = utcnow()
    r.deleted_at, r.content = now, b""
    for sh in db.scalars(select(ReportShare).where(ReportShare.report_id == r.id,
                                                   ReportShare.revoked_at.is_(None))):  # fmt: skip
        sh.revoked_at = now  # apagar o relatório derruba os links
    audit(db, action="report.delete", object_type="report_job", object_id=r.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- compartilhamento temporário (ex.: contador) ----------


class ShareIn(BaseModel):
    days: int = Field(default=7, ge=1, le=MAX_SHARE_DAYS)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _share_out(sh: ReportShare) -> dict:
    return {
        "id": str(sh.id), "report_id": str(sh.report_id), "expires_at": sh.expires_at.isoformat(),
        "revoked": sh.revoked_at is not None, "access_count": sh.access_count,
        "last_access_at": sh.last_access_at.isoformat() if sh.last_access_at else None,
    }  # fmt: skip


@router.post("/reports/{report_id}/share", status_code=status.HTTP_201_CREATED)
def share(report_id: uuid.UUID, body: ShareIn | None = None, ctx: TenantContext = Depends(get_tenant),
          db: Session = Depends(get_db)):  # fmt: skip
    """Cria um link que dá acesso ao arquivo sem login, até `days` dias. O token aparece só aqui."""
    r = _own_report(db, ctx, report_id)
    days = (body or ShareIn()).days
    token = secrets.token_urlsafe(32)
    expires = min(_now() + timedelta(days=days), _aware(r.expires_at))
    sh = ReportShare(
        organization_id=ctx.organization_id, report_id=r.id, created_by=ctx.user.id,
        token_hash=_token_hash(token), expires_at=expires,
    )  # fmt: skip
    db.add(sh)
    db.flush()
    audit(db, action="report.share", object_type="report_share", object_id=sh.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"days": days})  # fmt: skip
    db.commit()
    return {**_share_out(sh), "token": token, "path": f"/api/v1/shared-reports/{token}"}


@router.get("/reports/{report_id}/shares")
def list_shares(
    report_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    r = _own_report(db, ctx, report_id)
    rows = db.scalars(select(ReportShare).where(ReportShare.report_id == r.id)
                      .order_by(ReportShare.created_at.desc(), ReportShare.id))  # fmt: skip
    return [_share_out(s) for s in rows]


@router.delete("/report-shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share(
    share_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    sh = db.scalar(select(ReportShare).where(ReportShare.id == share_id,
                                             ReportShare.organization_id == ctx.organization_id,
                                             ReportShare.created_by == ctx.user.id))  # fmt: skip
    if sh is None:
        raise HTTPException(404, "Link não encontrado")
    sh.revoked_at = sh.revoked_at or utcnow()
    audit(db, action="report.share_revoke", object_type="report_share", object_id=sh.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


public = APIRouter(prefix="/api/v1", tags=["reports-public"])


@public.get("/shared-reports/{token}")
def shared_report(token: str, request: Request, db: Session = Depends(get_db)):
    """Acesso sem login por token. Qualquer falha (inexistente, expirado, revogado) é um 404
    igual, para não revelar o que existe."""
    sh = db.scalar(select(ReportShare).where(ReportShare.token_hash == _token_hash(token)))
    r = db.get(ReportJob, sh.report_id) if sh else None
    now = _now()
    if (sh is None or sh.revoked_at is not None or _aware(sh.expires_at) < now or r is None
            or r.deleted_at is not None or _aware(r.expires_at) < now):  # fmt: skip
        raise HTTPException(404, "Link inválido ou expirado")
    sh.access_count += 1
    sh.last_access_at = now
    ip = hashlib.sha256((request.client.host if request.client else "").encode()).hexdigest()[:16]
    audit(db, action="report.shared_access", object_type="report_share", object_id=sh.id,
          organization_id=r.organization_id, metadata={"ip_hash": ip})  # fmt: skip
    db.commit()
    return _file(r)


# ---------- fechamento mensal ----------


@router.get("/closing")
def closing(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    as_of: date | None = None,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Checklist de fechamento do mês (PRD 5.4): o que falta para o mês poder ser encerrado."""
    m = parse_month(month)
    today = _as_of(db, ctx, as_of)
    items: list[dict] = []

    def add(code, label, done, detail, route):
        items.append({"code": code, "label": label, "done": done, "detail": detail, "route": route})

    if ctx.can(P_FIN_READ):
        pending = list(db.scalars(_tx_q(ctx).where(Transaction.status == "PLANNED",
                                                   Transaction.competence_month == m)))  # fmt: skip
        add("PENDING_PLANNED", "Lançamentos planejados resolvidos", not pending,
            f"{len(pending)} planejado(s) ainda sem pagamento/recebimento" if pending else "Nenhum pendente",
            "/app/finances")  # fmt: skip
        unlinked = [t for t in db.scalars(_tx_q(ctx).where(
            Transaction.type == "INCOME", Transaction.status == "REALIZED", Transaction.competence_month == m))
            if not t.work_source_id and not t.clinic_service_id]  # fmt: skip
        add(
            "UNLINKED_INCOME",
            "Receitas vinculadas a uma fonte",
            not unlinked,
            f"{len(unlinked)} receita(s) sem vínculo" if unlinked else "Todas vinculadas",
            "/app/finances",
        )
        last_import = db.scalar(select(ImportJob).where(
            ImportJob.organization_id == ctx.organization_id, ImportJob.status == "COMPLETED")
            .order_by(ImportJob.created_at.desc()))  # fmt: skip
        add(
            "STATEMENT_IMPORTED",
            "Extrato do mês importado e conciliado",
            last_import is not None,
            "Há importação concluída" if last_import else "Nenhum extrato importado ainda",
            "/app/finances",
        )
    if ctx.can(P_GOAL_READ):
        from app.modules.realized.router import compare

        cmp_ = compare(month=month_str(m), as_of=today, ctx=ctx, db=db)
        not_started = [s["name"] for s in cmp_["sources"] if s["status"] == "NOT_STARTED"]
        add(
            "SOURCES_RECORDED",
            "Realizado registrado em todas as fontes planejadas",
            not not_started,
            ("Sem registro: " + ", ".join(not_started)) if not_started else "Todas com registro",
            "/app/dashboard",
        )
        nxt = shift_month(m, 1)
        add(
            "NEXT_GOAL",
            "Meta do mês seguinte definida",
            get_goal(db, ctx, nxt) is not None,
            f"Meta de {label_month(nxt)}",
            "/app/goals",
        )
    if ctx.can(P_SCHED_READ):
        from app.modules.schedule.router import overview

        ov = overview(month=month_str(m), user_id=None, ctx=ctx, db=db)
        add("SCHEDULE_OK", "Agenda sem conflitos nem limites estourados", ov["feasible"],
            "Sem problemas" if ov["feasible"] else "Há conflitos, descanso insuficiente ou limites estourados",
            "/app/schedule")  # fmt: skip
    if ctx.can(P_GOAL_READ):
        rep = db.scalar(select(ReportJob).where(
            ReportJob.organization_id == ctx.organization_id, ReportJob.created_by == ctx.user.id,
            ReportJob.template == "MONTHLY_RESULT", ReportJob.period == month_str(m),
            ReportJob.deleted_at.is_(None), ReportJob.expires_at >= _now()))  # fmt: skip
        add(
            "REPORT_GENERATED",
            "Relatório do mês gerado",
            rep is not None,
            "Relatório disponível" if rep else "Gere o relatório 'Resultado mensal'",
            "/app/reports",
        )
    done = sum(1 for i in items if i["done"])
    phase = "FUTURE" if today < m else ("CLOSED" if today >= shift_month(m, 1) else "OPEN")
    return {"month": month_str(m), "phase": phase, "items": items, "done": done, "total": len(items),
            "ready_to_close": bool(items) and done == len(items)}  # fmt: skip
