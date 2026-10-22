"""Retenção: remove o que venceu. Rodar periodicamente (ex.: cron diário):

    python -m app.maintenance purge          # aplica
    python -m app.maintenance purge --dry-run

Políticas (documentadas em docs/SECURITY.md): sessões vencidas; avisos lidos há mais de 180 dias;
conversas do consultor excluídas há mais de 30 dias; relatórios vencidos/excluídos há mais de 30 dias.
Faturas e auditoria são retidas (obrigação fiscal/segurança) e não passam por aqui.
"""

import sys
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import (
    AiConversation,
    AiMessage,
    Notification,
    ReportJob,
    ReportShare,
    UserSession,
)  # fmt: skip

NOTIFICATION_DAYS, AI_DAYS, REPORT_DAYS = 180, 30, 30


def purge(db: Session, now=None, dry_run: bool = False) -> dict[str, int]:
    now = now or utcnow()
    old_conv = select(AiConversation.id).where(
        AiConversation.deleted_at.is_not(None),
        AiConversation.deleted_at < now - timedelta(days=AI_DAYS),
    )
    old_reports = select(ReportJob.id).where(
        ReportJob.deleted_at.is_not(None), ReportJob.deleted_at < now - timedelta(days=REPORT_DAYS))  # fmt: skip
    plan = [
        ("sessions", delete(UserSession).where(UserSession.expires_at < now),
         select(func.count()).select_from(UserSession).where(UserSession.expires_at < now)),
        ("notifications", delete(Notification).where(
            Notification.read_at.is_not(None), Notification.read_at < now - timedelta(days=NOTIFICATION_DAYS)),
         select(func.count()).select_from(Notification).where(
            Notification.read_at.is_not(None), Notification.read_at < now - timedelta(days=NOTIFICATION_DAYS))),
        ("ai_messages", delete(AiMessage).where(AiMessage.conversation_id.in_(old_conv)),
         select(func.count()).select_from(AiMessage).where(AiMessage.conversation_id.in_(old_conv))),
        ("ai_conversations", delete(AiConversation).where(AiConversation.id.in_(old_conv)),
         select(func.count()).select_from(AiConversation).where(AiConversation.id.in_(old_conv))),
        ("report_shares", delete(ReportShare).where(ReportShare.report_id.in_(old_reports)),
         select(func.count()).select_from(ReportShare).where(ReportShare.report_id.in_(old_reports))),
        ("reports", delete(ReportJob).where(ReportJob.id.in_(old_reports)),
         select(func.count()).select_from(ReportJob).where(ReportJob.id.in_(old_reports))),
    ]  # fmt: skip
    out = {}
    for name, stmt, count in plan:
        out[name] = db.scalar(count) or 0
        if not dry_run:
            db.execute(stmt)
    if not dry_run:
        db.commit()
    return out


if __name__ == "__main__":
    from app.db.session import get_factory

    if len(sys.argv) < 2 or sys.argv[1] != "purge":
        sys.exit("uso: python -m app.maintenance purge [--dry-run]")
    with get_factory()() as s:
        print(purge(s, dry_run="--dry-run" in sys.argv))
