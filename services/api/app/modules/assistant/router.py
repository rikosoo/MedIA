import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_GOAL_READ
from app.db.base import utcnow
from app.db.models import AiConversation, AiMessage
from app.db.session import get_db
from app.modules.assistant.engine import SUGGESTED, answer, redact

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

DAILY_LIMIT = 60  # perguntas por usuário por dia (custo/abuso)
MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"


class ChatIn(BaseModel):
    message: str = Field(min_length=2, max_length=500)
    conversation_id: uuid.UUID | None = None
    month: str | None = Field(default=None, pattern=MONTH_RE)
    target_cents: int | None = Field(default=None, gt=0, le=10**11)  # "refazer com outra meta"


class SaveIn(BaseModel):
    saved: bool = True


def _own_conv(db: Session, ctx: TenantContext, cid: uuid.UUID) -> AiConversation:
    c = db.scalar(
        select(AiConversation).where(
            AiConversation.id == cid,
            AiConversation.organization_id == ctx.organization_id,
            AiConversation.owner_user_id == ctx.user.id,
            AiConversation.deleted_at.is_(None),
        )
    )
    if c is None:
        raise HTTPException(404, "Conversa não encontrada")
    return c


def _msg_out(m: AiMessage) -> dict:
    return {
        "id": str(m.id), "role": m.role, "content": m.content_redacted, "answer": m.answer_json,
        "saved": m.saved, "created_at": m.created_at.isoformat(),
    }  # fmt: skip


@router.get("/suggestions")
def suggestions(ctx: TenantContext = Depends(get_tenant)):
    ctx.require(P_GOAL_READ)
    return SUGGESTED


@router.post("/chat")
def chat(body: ChatIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    """Responde com números já calculados pelo motor. Somente leitura: nada é alterado."""
    ctx.require(P_GOAL_READ)
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(
        select(func.count(AiMessage.id))
        .join(AiConversation, AiConversation.id == AiMessage.conversation_id)
        .where(
            AiConversation.owner_user_id == ctx.user.id,
            AiMessage.role == "USER",
            AiMessage.created_at >= start,
        )
    )
    if (used or 0) >= DAILY_LIMIT:
        raise HTTPException(429, "Limite diário de perguntas atingido. Tente amanhã.")

    today = date.today()
    month = date(today.year, today.month, 1)
    if body.month:
        month = date(int(body.month[:4]), int(body.month[5:]), 1)

    conv = _own_conv(db, ctx, body.conversation_id) if body.conversation_id else None
    result, tools = answer(db, ctx, body.message, month, body.target_cents)
    if conv is None:
        conv = AiConversation(
            organization_id=ctx.organization_id, owner_user_id=ctx.user.id,
            summary=redact(body.message)[:200],
        )  # fmt: skip
        db.add(conv)
        db.flush()
    db.add(AiMessage(conversation_id=conv.id, role="USER", content_redacted=redact(body.message)))
    reply = AiMessage(
        conversation_id=conv.id, role="ASSISTANT", content_redacted=result["summary"][:2000],
        answer_json=result, tool_calls_json=tools,
    )  # fmt: skip
    db.add(reply)
    conv.updated_at = utcnow()
    audit(
        db, action="assistant.chat", object_type="ai_conversation", object_id=conv.id,
        organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
        metadata={"intent": result["intent"], "grounded": result["grounded"]},
    )  # fmt: skip
    db.commit()
    return {"conversation_id": str(conv.id), "message": _msg_out(reply)}


@router.get("/conversations")
def conversations(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_GOAL_READ)
    rows = db.scalars(
        select(AiConversation)
        .where(
            AiConversation.organization_id == ctx.organization_id,
            AiConversation.owner_user_id == ctx.user.id,
            AiConversation.deleted_at.is_(None),
        )
        .order_by(AiConversation.updated_at.desc())
        .limit(30)
    )
    return [
        {"id": str(c.id), "summary": c.summary, "updated_at": c.updated_at.isoformat()}
        for c in rows
    ]


@router.get("/conversations/{conv_id}")
def conversation(
    conv_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_READ)
    c = _own_conv(db, ctx, conv_id)
    msgs = db.scalars(
        select(AiMessage)
        .where(AiMessage.conversation_id == c.id)
        .order_by(AiMessage.created_at, AiMessage.role.desc())
    )
    return {"id": str(c.id), "summary": c.summary, "messages": [_msg_out(m) for m in msgs]}


@router.delete("/conversations/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conv_id: uuid.UUID, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    ctx.require(P_GOAL_READ)
    c = _own_conv(db, ctx, conv_id)
    c.deleted_at = utcnow()
    audit(db, action="assistant.delete", object_type="ai_conversation", object_id=c.id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id)  # fmt: skip
    db.commit()
    return Response(status_code=204)


@router.post("/messages/{msg_id}/save")
def save_message(
    msg_id: uuid.UUID,
    body: SaveIn,
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """'Salvar recomendação': só marca a resposta; não aplica nada ao planejamento."""
    ctx.require(P_GOAL_READ)
    m = db.scalar(
        select(AiMessage)
        .join(AiConversation, AiConversation.id == AiMessage.conversation_id)
        .where(
            AiMessage.id == msg_id, AiMessage.role == "ASSISTANT",
            AiConversation.organization_id == ctx.organization_id,
            AiConversation.owner_user_id == ctx.user.id, AiConversation.deleted_at.is_(None),
        )
    )  # fmt: skip
    if m is None:
        raise HTTPException(404, "Mensagem não encontrada")
    m.saved = body.saved
    db.commit()
    return _msg_out(m)


@router.get("/saved")
def saved(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_GOAL_READ)
    rows = db.scalars(
        select(AiMessage)
        .join(AiConversation, AiConversation.id == AiMessage.conversation_id)
        .where(
            AiMessage.saved.is_(True),
            AiConversation.organization_id == ctx.organization_id,
            AiConversation.owner_user_id == ctx.user.id, AiConversation.deleted_at.is_(None),
        )
        .order_by(AiMessage.created_at.desc())
        .limit(50)
    )  # fmt: skip
    return [_msg_out(m) for m in rows]
