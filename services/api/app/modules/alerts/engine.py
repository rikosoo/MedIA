"""Alertas por regras (PRD §15): calculados na hora, e só viram notificação se o usuário optou.

Notificações carregam texto genérico, sem valores financeiros: o detalhe só aparece dentro do app,
depois do login (PRD: "preferir aviso com link autenticado").
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.rbac import P_ACQ_READ, P_BILLING, P_FIN_READ, P_GOAL_READ, P_SCHED_READ
from app.db.models import Leave, Notification, NotificationSettings, Transaction
from app.modules.goals.projection import _realized, get_goal, month_str, shift_month
from app.modules.schedule.router import _tz

GENERIC_BODY = "Abra o FinanceMed AI para ver os detalhes."
MAX_PER_DAY = 3

# código -> (título genérico, rota, intervalo mínimo entre avisos iguais, em dias)
KINDS: dict[str, tuple[str, str, int]] = {
    "UPDATE_REALIZED": ("Atualize o realizado do mês", "/app/dashboard", 7),
    "GOAL_BEHIND_PACE": ("Seu realizado está abaixo do ritmo do mês", "/app/dashboard", 7),
    "GOAL_GAP": ("Ainda falta para a meta do mês", "/app/goals", 7),
    "SCHEDULE_PROBLEM": (
        "A agenda do mês tem conflito, descanso insuficiente ou limite estourado",
        "/app/schedule",
        3,
    ),  # fmt: skip
    "SECRETARY_NO_COVERAGE": (
        "Há férias da secretária sem cobertura cadastrada",
        "/app/schedule",
        14,
    ),
    "EXPENSES_ABOVE_USUAL": ("As despesas do mês estão acima do habitual", "/app/finances", 14),
    "IDLE_CAPACITY": ("Há capacidade ociosa na agenda", "/app/schedule", 7),
    "CONVERSION_DROP": ("A conversão de contatos em agendamentos caiu", "/app/acquisition", 14),
    "SUBSCRIPTION_AT_RISK": ("Há um problema com o pagamento da assinatura", "/app/billing", 3),
    "REPORT_READY": ("O relatório mensal está pronto", "/app/reports", 1),
}

BEHIND_MARGIN_BPS = 2_000  # 20 p.p. abaixo do ritmo
EXPENSE_FACTOR_BPS = 13_000  # 30% acima da média dos 3 meses anteriores
EXPENSE_MIN_DELTA_CENTS = 10_000
STALE_DAYS = 14


@dataclass
class Alert:
    kind: str
    severity: str  # warning | info
    title: str
    detail: str  # com valores; nunca vai para notificações
    route: str
    ref: str

    def out(self) -> dict:
        return {"kind": self.kind, "severity": self.severity, "title": self.title,
                "detail": self.detail, "route": self.route, "ref": self.ref}  # fmt: skip


def _brl(c: int) -> str:
    from finance_engine import format_brl

    return format_brl(c)


def _phase(m: date, today: date) -> str:
    return "FUTURE" if today < m else ("CLOSED" if today >= shift_month(m, 1) else "OPEN")


def evaluate(db: Session, ctx: TenantContext, m: date, today: date) -> list[Alert]:
    ref, phase, out = month_str(m), _phase(m, today), []

    def add(kind, severity, detail):
        out.append(Alert(kind, severity, KINDS[kind][0], detail, KINDS[kind][1], ref))

    if ctx.can(P_BILLING):
        from app.modules.billing.entitlements import get_sub

        if get_sub(db, ctx.organization_id).state == "PAST_DUE":
            add("SUBSCRIPTION_AT_RISK", "warning",
                "O último pagamento da assinatura falhou. Atualize a forma de pagamento para não "
                "perder os recursos pagos.")  # fmt: skip

    if ctx.can(P_GOAL_READ) and phase == "OPEN":
        from app.modules.goals.projection import build_gap
        from app.modules.realized.router import compare

        cmp_ = compare(month=ref, as_of=today, ctx=ctx, db=db)
        planned = any(s["planned_units"] > 0 for s in cmp_["sources"])
        pace = cmp_["pace"]
        if planned:
            last = db.scalar(select(func.max(Transaction.paid_at)).where(
                Transaction.organization_id == ctx.organization_id, Transaction.deleted_at.is_(None),
                Transaction.status == "REALIZED", Transaction.competence_month == m))  # fmt: skip
            stale = last is None or (today - last).days >= STALE_DAYS
            if stale and pace["days_elapsed"] >= 10:
                add("UPDATE_REALIZED", "info", "Nenhum lançamento realizado neste mês." if last is None
                    else f"O último lançamento realizado foi em {last.strftime('%d/%m')}.")  # fmt: skip
            got = pace["realized_vs_planned_gross_pct_bps"]
            if (got is not None and pace["elapsed_pct_bps"] >= 3_000
                    and got < pace["elapsed_pct_bps"] - BEHIND_MARGIN_BPS):  # fmt: skip
                add("GOAL_BEHIND_PACE", "warning",
                    f"Você recebeu {round(got / 100)}% do planejado com {round(pace['elapsed_pct_bps'] / 100)}% "
                    "do mês já passado.")  # fmt: skip
        goal = get_goal(db, ctx, m)
        if goal is not None:
            gap = build_gap(db, ctx, goal)
            if gap["status"] == "SHORT":
                add("GOAL_GAP", "warning", f"Faltam {_brl(gap['gap_cents'])} para a meta "
                    f"(só com o garantido faltariam {_brl(gap['assured_gap_cents'])}).")  # fmt: skip

    if ctx.can(P_SCHED_READ) and phase != "CLOSED":
        from app.modules.schedule.router import capacity, overview

        ov = overview(month=ref, user_id=None, ctx=ctx, db=db)
        if not ov["feasible"]:
            parts = []
            if any(c["kind"] == "HARD" for c in ov["conflicts"]):
                parts.append("conflito de horário")
            if ov["rest_violations"]:
                parts.append("descanso insuficiente")
            if ov["over_limit"]:
                parts.append("limite de horas ou plantões estourado")
            if ov["events_on_closed_days"]:
                parts.append("atividade em dia fechado")
            add("SCHEDULE_PROBLEM", "warning", "Problemas: " + ", ".join(parts) + ".")
        leaves = db.scalars(select(Leave).where(
            Leave.organization_id == ctx.organization_id, Leave.staff_role == "SECRETARY",
            Leave.deleted_at.is_(None), Leave.coverage_factor_bps == 0,
            Leave.end_date >= today, Leave.start_date <= today + timedelta(days=30)))  # fmt: skip
        for lv in leaves:
            add("SECRETARY_NO_COVERAGE", "warning",
                f"Férias da secretária de {lv.start_date.strftime('%d/%m')} a {lv.end_date.strftime('%d/%m')} "
                "sem cobertura: pode cair a captação.")  # fmt: skip
            break
        if phase == "OPEN":
            cap = capacity(
                month=ref, user_id=None, slot_minutes=None, service_id=None, ctx=ctx, db=db
            )
            future = [d for d in cap["days"] if d["date"] >= today.isoformat()]
            avail = sum(d["available_minutes"] for d in future)
            free = sum(d["remaining_minutes"] for d in future)
            if avail >= 480 and free * 100 >= avail * 70:
                add("IDLE_CAPACITY", "info", f"{round(free * 100 / avail)}% da agenda restante do mês "
                    f"({free // 60} h) está livre.")  # fmt: skip

    if ctx.can(P_ACQ_READ) and phase != "FUTURE":
        from app.modules.acquisition.router import _fun, _metrics
        from finance_engine.funnel import combine, conversion_drop

        rows = _metrics(db, ctx, m)
        hist = [_metrics(db, ctx, shift_month(m, -i)) for i in (1, 2, 3)]
        history = [combine([_fun(r) for r in h]) for h in hist if h]
        if rows and history:
            cur = combine([_fun(r) for r in rows])
            drop = conversion_drop(cur, history)
            if drop:
                add("CONVERSION_DROP", "warning",
                    f"A conversão contato -> agendamento caiu {round(drop / 100)}% contra os meses anteriores "
                    f"({cur.leads} contatos no mês.)")  # fmt: skip

    if ctx.can(P_FIN_READ) and phase != "FUTURE":
        cur = _realized(db, ctx, m)["expense"]
        prev = [_realized(db, ctx, shift_month(m, -i))["expense"] for i in (1, 2, 3)]
        prev = [p for p in prev if p > 0]
        if len(prev) >= 2:
            avg = sum(prev) // len(prev)
            if cur * 10_000 > avg * EXPENSE_FACTOR_BPS and cur - avg >= EXPENSE_MIN_DELTA_CENTS:
                add("EXPENSES_ABOVE_USUAL", "warning",
                    f"Despesas de {_brl(cur)} contra média de {_brl(avg)} nos meses anteriores.")  # fmt: skip
    return out


# ---------- notificações ----------


def settings_for(db: Session, ctx: TenantContext) -> NotificationSettings:
    s = db.scalar(select(NotificationSettings).where(
        NotificationSettings.organization_id == ctx.organization_id,
        NotificationSettings.user_id == ctx.user.id))  # fmt: skip
    if s is None:
        s = NotificationSettings(organization_id=ctx.organization_id, user_id=ctx.user.id,
                                 enabled=False, kinds=[])  # fmt: skip
        db.add(s)
        db.flush()
    return s


def in_quiet_hours(s: NotificationSettings, local_hour: int) -> bool:
    a, b = s.quiet_start_hour, s.quiet_end_hour
    if a is None or b is None or a == b:
        return False
    return a <= local_hour < b if a < b else (local_hour >= a or local_hour < b)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _blocked(db: Session, ctx: TenantContext, s: NotificationSettings, kind: str, ref: str,
             now: datetime) -> str | None:  # fmt: skip
    """Motivo de não criar a notificação (opt-in, horário silencioso, frequência, limite diário)."""
    if not s.enabled:
        return "disabled"
    if kind not in (s.kinds or []):
        return "kind_off"
    local = now.astimezone(_tz(db, ctx))
    if in_quiet_hours(s, local.hour):
        return "quiet_hours"
    since = now - timedelta(days=KINDS[kind][2])
    recent = db.scalar(select(Notification.id).where(
        Notification.organization_id == ctx.organization_id, Notification.user_id == ctx.user.id,
        Notification.kind == kind, Notification.ref == ref, Notification.created_at >= since))  # fmt: skip
    if recent is not None:
        return "frequency"
    day_start = datetime.combine(local.date(), datetime.min.time(), tzinfo=local.tzinfo)
    today_n = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.organization_id == ctx.organization_id, Notification.user_id == ctx.user.id,
        Notification.created_at >= day_start))  # fmt: skip
    if today_n >= MAX_PER_DAY:
        return "daily_limit"
    return None


def notify(db: Session, ctx: TenantContext, kind: str, ref: str, now: datetime) -> dict | None:
    s = settings_for(db, ctx)
    if _blocked(db, ctx, s, kind, ref, now):
        return None
    title, route, _ = KINDS[kind]
    n = Notification(organization_id=ctx.organization_id, user_id=ctx.user.id, kind=kind, ref=ref,
                     title=title, body=GENERIC_BODY, route=route)  # fmt: skip
    n.created_at = now
    db.add(n)
    db.flush()
    return {"id": str(n.id), "kind": kind}


def generate(db: Session, ctx: TenantContext, m: date, now: datetime) -> dict:
    s = settings_for(db, ctx)
    today = now.astimezone(_tz(db, ctx)).date()
    created, skipped = [], []
    for a in evaluate(db, ctx, m, today):
        why = _blocked(db, ctx, s, a.kind, a.ref, now)
        if why:
            skipped.append({"kind": a.kind, "reason": why})
            continue
        n = notify(db, ctx, a.kind, a.ref, now)
        if n:
            created.append(n)
    return {"created": created, "skipped": skipped}
