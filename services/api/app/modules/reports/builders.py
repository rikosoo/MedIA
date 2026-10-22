"""Monta o conteúdo de cada relatório reutilizando os cálculos do dashboard, comparação e finanças.

Nada aqui recalcula regra de negócio: o relatório só organiza números que o app já mostra.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.rbac import P_FIN_READ, P_GOAL_READ, P_SCHED_READ
from app.db.models import (
    OptimizerRun,
    OptimizerScenario,
    SavedPlan,
    ScheduleEvent,
    Transaction,
)
from app.modules.finances.router import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    _signed,
    _tx_q,
    parse_month,
)
from app.modules.finances.router import summary as finance_summary
from app.modules.goals.projection import (
    _realized,
    get_goal,
    month_numbers,
    month_str,
    shift_month,
)
from app.modules.goals.router import dashboard as dashboard_data
from app.modules.realized.router import compare as compare_data
from app.modules.reports.render import (
    document,
    minutes,
    money,
    pct,
    section,
    table,
)
from app.modules.schedule.router import _events_q, _tz
from finance_engine import format_brl
from finance_engine.schedule import local_month

MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
             "setembro", "outubro", "novembro", "dezembro"]  # fmt: skip

# código -> (nome, tipo de período, permissões exigidas, descrição)
TEMPLATES = {
    "MONTHLY_RESULT": (
        "Resultado mensal",
        "month",
        {P_GOAL_READ},
        "Meta, projetado, realizado, fontes, despesas e próximos passos.",
    ),
    "EXPENSES": (
        "Despesas",
        "month",
        {P_FIN_READ},
        "Despesas por categoria (planejado x realizado) e lançamentos do mês.",
    ),
    "INCOME_BY_SOURCE": (
        "Renda por vínculo",
        "month",
        {P_GOAL_READ},
        "Esperado x obtido por trabalho e serviço, com líquido estimado.",
    ),
    "OPTIMIZATION": (
        "Otimização",
        "month",
        {P_GOAL_READ},
        "Última execução do otimizador do mês e planos salvos ou aplicados.",
    ),
    "ACCOUNTANT": (
        "Contador",
        "month",
        {P_GOAL_READ, P_FIN_READ},
        "Receitas e despesas por competência e caixa, com todos os lançamentos.",
    ),
    "YEAR": ("Ano", "year", {P_GOAL_READ}, "Os 12 meses do ano: meta, projetado e realizado."),
}


def label_month(m: date) -> str:
    return f"{MONTHS_PT[m.month - 1].capitalize()} de {m.year}"


def parse_period(template: str, period: str) -> tuple[date, str]:
    kind = TEMPLATES[template][1]
    try:
        if kind == "year":
            if len(period) != 4:
                raise ValueError
            return date(int(period), 1, 1), period
        if len(period) != 7:
            raise ValueError
        return parse_month(period), period
    except (ValueError, TypeError):
        raise HTTPException(422, "Período inválido para este relatório") from None


def _need(ctx: TenantContext, perms: set[str]) -> None:
    for p in perms:
        ctx.require(p)


def build(
    db: Session, ctx: TenantContext, template: str, period: str, now: datetime, as_of: date
) -> dict:
    if template not in TEMPLATES:
        raise HTTPException(422, "Relatório desconhecido")
    _need(ctx, TEMPLATES[template][2])
    m, period = parse_period(template, period)
    fn = {
        "MONTHLY_RESULT": monthly_result, "EXPENSES": expenses, "INCOME_BY_SOURCE": income_by_source,
        "OPTIMIZATION": optimization, "ACCOUNTANT": accountant, "YEAR": year,
    }[template]  # fmt: skip
    return fn(db, ctx, m, period, now, as_of)


# ---------- Resultado mensal ----------


def monthly_result(db, ctx, m, period, now, as_of):
    d = dashboard_data(month=month_str(m), goal_id=None, ctx=ctx, db=db)
    cmp_ = compare_data(month=month_str(m), as_of=as_of, ctx=ctx, db=db)
    g = d["goal"]
    kpis = [
        ("Meta do mês", money(g["effective_target_cents"]) if g else "Sem meta definida"),
        ("Projetado (planos)", money(d["projection"]["value_cents"])),
        ("  do qual garantido", money(d["projection"]["assured_cents"])),
        ("Realizado (receitas - despesas lançadas)", money(d["realized"]["value_cents"])),
    ]
    if g:
        kpis.append(("Falta para a meta", money(g["gap_cents"])))
        kpis.append(("Situação", {"SHORT": "Abaixo da meta", "MET": "Meta atingida",
                                  "MET_WITH_ISSUES": "Atingida, com problemas na agenda"}[g["status"]]))  # fmt: skip
    kpis.append(("Qualidade dos dados", {"complete": "Completos", "partial": "Parciais",
                                         "insufficient": "Insuficientes"}[d["data_quality"]]))  # fmt: skip
    secs = [section(f"Resumo de {label_month(m)}", kpis, notes=d["data_quality_reasons"])]

    rows = [[s["name"], f"{s['planned_units']} / {s['realized_units']}", money(s["planned_gross_cents"]),
             money(s["realized_gross_cents"]), money(s["variance_gross_cents"]), pct(s["variance_pct_bps"]),
             {"NOT_STARTED": "Sem registro", "BEHIND": "Abaixo", "ON_PLAN": "No plano", "AHEAD": "Acima",
              "UNPLANNED": "Sem plano", "NONE": "-"}[s["status"]]] for s in cmp_["sources"]]  # fmt: skip
    notes = [n for n in cmp_["notes"][:2]]
    if cmp_["totals"]["unlinked_income_cents"]:
        notes.insert(0, "Receitas sem vínculo a uma fonte: "
                        f"{fmt_cents(cmp_['totals']['unlinked_income_cents'])}.")  # fmt: skip
    secs.append(section("Esperado x obtido por fonte", tables=[table(
        "Unidades (planejadas / realizadas) e valores brutos",
        ["Fonte", "Unid.", "Planejado", "Realizado", "Diferença", "% do plano", "Situação"], rows)],
        notes=notes))  # fmt: skip

    exp_rows = [
        [e["label"], money(e["planned_cents"]), money(e["realized_cents"])]
        for e in cmp_["expenses"]
    ]
    exp_rows.append(["Total", money(cmp_["totals"]["planned_expenses_cents"]),
                     money(cmp_["totals"]["realized_expenses_cents"])])  # fmt: skip
    secs.append(
        section(
            "Despesas",
            tables=[
                table(
                    "Planejado x realizado por categoria",
                    ["Categoria", "Planejado", "Realizado"],
                    exp_rows,
                )
            ],
        )
    )

    hours = d["hours"]
    if hours:
        secs.append(section("Tempo", [
            ("Horas ativas na agenda", minutes(hours["active_minutes"])),
            ("Horas comprometidas na agenda", minutes(hours["committed_minutes"])),
            ("Horas comprometidas previstas nas fontes", minutes(hours["planned_committed_minutes"])),
            ("Agenda dentro dos limites", "Sim" if hours["schedule_feasible"] else "Não"),
        ]))  # fmt: skip
    if d["alerts"]:
        secs.append(section("Alertas do mês", notes=[a["message"] for a in d["alerts"]]))
    secs.append(_future(db, ctx, m))
    secs.append(_next_steps(db, ctx, m, g))
    secs.append(section("Como calculamos", notes=d["assumptions"]))
    return document("MONTHLY_RESULT", "Resultado mensal", label_month(m), period, secs, now)


def fmt_cents(c: int) -> str:
    return format_brl(c)


def _future(db, ctx, m: date) -> dict:
    """Compromissos do mês seguinte: despesas planejadas/recorrentes, planos aplicados e agenda."""
    nxt = shift_month(m, 1)
    kpis, notes = [], []
    if ctx.can(P_FIN_READ):
        fin = finance_summary(month=month_str(nxt), ctx=ctx, db=db)["competence"]
        kpis.append(
            (f"Despesas já previstas em {label_month(nxt)}", money(fin["expense_planned_cents"]))
        )
        planned = list(db.scalars(_tx_q(ctx).where(Transaction.status == "PLANNED",
                                                   Transaction.competence_month == nxt).limit(10)))  # fmt: skip
        notes += [f"{t.description}: {fmt_cents(t.amount_cents)}" for t in planned]
    if ctx.can(P_GOAL_READ):
        n_plans = len(list(db.scalars(select(SavedPlan.id).where(
            SavedPlan.organization_id == ctx.organization_id, SavedPlan.user_id == ctx.user.id,
            SavedPlan.status == "APPLIED", SavedPlan.month == nxt))))  # fmt: skip
        kpis.append(("Planos do otimizador aplicados ao mês seguinte", n_plans))
    if ctx.can(P_SCHED_READ):
        win = local_month(nxt, _tz(db, ctx))
        utc = ZoneInfo("UTC")
        n_ev = len(
            list(
                db.scalars(
                    _events_q(ctx, ctx.user.id).where(
                        ScheduleEvent.starts_at < win.end.astimezone(utc),
                        ScheduleEvent.ends_at > win.start.astimezone(utc),
                    )
                )
            )
        )
        kpis.append(("Eventos já na agenda do mês seguinte", n_ev))
    return section("Compromissos futuros", kpis, notes=notes)


def _next_steps(db, ctx, m: date, goal) -> dict:
    nxt = shift_month(m, 1)
    notes = []
    existing = get_goal(db, ctx, nxt)
    if existing is not None:
        notes.append(f"Meta de {label_month(nxt)} já definida: {fmt_cents(existing.target_cents)}.")
    elif goal:
        notes.append(f"Sugestão: repetir a meta de {fmt_cents(goal['goal']['target_cents'])} "
                     f"para {label_month(nxt)} (use 'Duplicar meta' em Minha Meta).")  # fmt: skip
    else:
        notes.append(f"Defina a meta de {label_month(nxt)} em Minha Meta.")
    notes.append(
        "Rode o Otimizador Misto para o mês seguinte depois de ajustar a composição do mês."
    )
    notes.append("Atualize o realizado e concilie o extrato antes de fechar o mês.")
    return section("Próximo mês", notes=notes)


# ---------- Despesas ----------


def expenses(db, ctx, m, period, now, as_of):
    fin = finance_summary(month=month_str(m), ctx=ctx, db=db)["competence"]
    cats: dict[str, dict] = {}
    txs = list(db.scalars(_tx_q(ctx).where(Transaction.type == "EXPENSE",
                                           Transaction.competence_month == m)
                          .order_by(Transaction.paid_at, Transaction.created_at, Transaction.id)))  # fmt: skip
    for t in txs:
        e = cats.setdefault(t.category, {"planned": 0, "realized": 0})
        e["realized" if t.status == "REALIZED" else "planned"] += -_signed(t)
    rows = [[EXPENSE_CATEGORIES.get(c, c), money(v["planned"]), money(v["realized"])]
            for c, v in sorted(cats.items())]  # fmt: skip
    lines = [[t.paid_at.isoformat() if t.paid_at else "-", t.description,
              EXPENSE_CATEGORIES.get(t.category, t.category),
              "Realizado" if t.status == "REALIZED" else "Planejado", money(-_signed(t))]
             for t in txs[:300]]  # fmt: skip
    secs = [
        section(f"Despesas de {label_month(m)}", [
            ("Realizadas", money(fin["expense_realized_cents"])),
            ("Planejadas (inclui recorrências ainda sem lançamento)", money(fin["expense_planned_cents"])),
        ]),  # fmt: skip
        section("Por categoria", tables=[table("Planejado x realizado", ["Categoria", "Planejado", "Realizado"], rows)]),
        section("Lançamentos", tables=[table("Despesas do mês (até 300)", ["Pago em", "Descrição", "Categoria", "Situação", "Valor"], lines)],
                notes=["Estornos aparecem com sinal invertido."] if any(t.is_reversal for t in txs) else []),
    ]  # fmt: skip
    return document("EXPENSES", "Despesas", label_month(m), period, secs, now)


# ---------- Renda por vínculo ----------


def income_by_source(db, ctx, m, period, now, as_of):
    cmp_ = compare_data(month=month_str(m), as_of=as_of, ctx=ctx, db=db)
    nums = month_numbers(db, ctx, m, "PERSONAL_NET")
    hourly = {nums["meta"][r.key]["source_id"]: r.net_per_committed_hour_cents
              for r in nums["results"] if not r.key.endswith(":extra")}  # fmt: skip
    rows = [[s["name"], "Trabalho" if s["source_type"] == "WORK" else "Serviço",
             f"{s['planned_units']} / {s['realized_units']}", money(s["planned_gross_cents"]),
             money(s["realized_gross_cents"]), money(s["planned_net_cents"]),
             money(s["realized_net_estimate_cents"]) if s["realized_net_estimate_cents"] is not None else "-",
             money(hourly[s["source_id"]]) if hourly.get(s["source_id"]) else "-"]
            for s in cmp_["sources"]]  # fmt: skip
    secs = [
        section(
            f"Renda por vínculo em {label_month(m)}",
            [
                ("Planejado (bruto)", money(cmp_["totals"]["planned_gross_cents"])),
                ("Realizado (bruto, com vínculo)", money(cmp_["totals"]["realized_gross_cents"])),
                ("Receitas sem vínculo", money(cmp_["totals"]["unlinked_income_cents"])),
            ],
        ),  # fmt: skip
        section(
            "Por fonte",
            tables=[
                table(
                    "Esperado x obtido",
                    [
                        "Fonte",
                        "Tipo",
                        "Unid. (plan./real.)",
                        "Bruto planejado",
                        "Bruto realizado",
                        "Líquido planejado*",
                        "Líquido realizado*",
                        "Líq./h comprometida*",
                    ],
                    rows,
                )
            ],
            notes=[
                "* Estimativas com a retenção informada por você; não validadas.",
                *cmp_["notes"][:1],
            ],
        ),  # fmt: skip
    ]
    return document("INCOME_BY_SOURCE", "Renda por vínculo", label_month(m), period, secs, now)


# ---------- Otimização ----------


def optimization(db, ctx, m, period, now, as_of):
    run = db.scalar(select(OptimizerRun).where(
        OptimizerRun.organization_id == ctx.organization_id, OptimizerRun.created_by == ctx.user.id,
        OptimizerRun.month == m).order_by(OptimizerRun.created_at.desc(), OptimizerRun.id))  # fmt: skip
    if run is None:
        return document(
            "OPTIMIZATION",
            "Otimização",
            label_month(m),
            period,
            [
                section(
                    "Sem execuções",
                    notes=["Nenhuma execução do otimizador neste mês. Rode o Otimizador Misto."],
                )
            ],
            now,
        )
    scs = list(db.scalars(select(OptimizerScenario).where(OptimizerScenario.run_id == run.id)
                          .order_by(OptimizerScenario.ranking)))  # fmt: skip
    secs = [section(f"Última execução em {label_month(m)}", [
        ("Resultado", {"FEASIBLE": "Meta alcançável", "INFEASIBLE": "Meta fora do alcance",
                       "ALREADY_MET": "Meta já atingida"}[run.outcome]),
        ("Base projetada", money(run.baseline_cents)), ("Lacuna", money(run.gap_cents)),
        ("Versão do motor", run.engine_version),
    ], notes=[run.result_json.get("message", "")] if run.result_json.get("message") else [])]  # fmt: skip
    if scs:
        rows = []
        for s in scs:
            o = s.output_json
            rows.append([" + ".join(o["labels"]),
                         ", ".join(f"{u['units']}x {u['label']}" for u in o["units"]),
                         money(o["net_gain_cents"]), money(o["surplus_cents"]), minutes(o["active_minutes"]),
                         minutes(o["committed_minutes"]), "Sim" if o["pessimistic_ok"] else "Não"])  # fmt: skip
        secs.append(
            section(
                "Cenários",
                tables=[
                    table(
                        "Comparação",
                        [
                            "Cenário",
                            "Composição",
                            "Ganho líquido",
                            "Folga",
                            "Ativas",
                            "Comprometidas",
                            "Fecha no pessimista",
                        ],
                        rows,
                    )
                ],
            )
        )
    elif run.outcome == "INFEASIBLE":
        r = run.result_json
        secs.append(section("Por que não fecha", [
            ("Máximo alcançável", money(r.get("max_achievable_gain_cents", 0))),
            ("Faltaria", money(r.get("shortfall_cents", 0)))],
            notes=[b["message"] for b in r.get("blocking_constraints", [])]))  # fmt: skip
    plans = list(db.scalars(select(SavedPlan).where(SavedPlan.organization_id == ctx.organization_id,
                                                    SavedPlan.user_id == ctx.user.id, SavedPlan.month == m)))  # fmt: skip
    if plans:
        secs.append(section("Planos salvos", tables=[table("Planos", ["Cenário", "Situação"], [
            [" + ".join(p.snapshot_json.get("labels", [])), {"SAVED": "Salvo", "APPLIED": "Aplicado",
                                                              "UNDONE": "Desfeito"}[p.status]] for p in plans])]))  # fmt: skip
    secs.append(section("Premissas", notes=run.result_json.get("assumptions", [])))
    return document("OPTIMIZATION", "Otimização", label_month(m), period, secs, now)


# ---------- Contador ----------


def accountant(db, ctx, m, period, now, as_of):
    real = list(db.scalars(_tx_q(ctx).where(Transaction.competence_month == m,
                                            Transaction.status == "REALIZED")))  # fmt: skip
    inc: dict[str, int] = {}
    exp: dict[str, int] = {}
    for t in real:
        (inc if t.type == "INCOME" else exp)[t.category] = (inc if t.type == "INCOME" else exp).get(t.category, 0) + (
            _signed(t) if t.type == "INCOME" else -_signed(t))  # fmt: skip
    nxt = shift_month(m, 1)
    cash = list(db.scalars(_tx_q(ctx).where(Transaction.status == "REALIZED", Transaction.paid_at >= m,
                                            Transaction.paid_at < nxt)))  # fmt: skip
    cash_in = sum(_signed(t) for t in cash if t.type == "INCOME")
    cash_out = -sum(_signed(t) for t in cash if t.type == "EXPENSE")
    d = dashboard_data(month=month_str(m), goal_id=None, ctx=ctx, db=db)
    tax_rows = [[b["name"], money(b["gross_cents"]), money(b["tax_cents"])] for b in d["breakdown"]]
    lines = [[month_str(t.competence_month), t.paid_at.isoformat() if t.paid_at else "-",
              "Receita" if t.type == "INCOME" else "Despesa",
              (INCOME_CATEGORIES if t.type == "INCOME" else EXPENSE_CATEGORIES).get(t.category, t.category),
              t.description, "Sim" if t.is_reversal else "Não", money(_signed(t))]
             for t in sorted(real, key=lambda t: (t.paid_at or date.min, t.created_at, t.id))[:1000]]  # fmt: skip
    secs = [
        section(f"Resumo de {label_month(m)}", [
            ("Receitas realizadas (competência)", money(sum(inc.values()))),
            ("Despesas realizadas (competência)", money(sum(exp.values()))),
            ("Resultado realizado (competência)", money(sum(inc.values()) - sum(exp.values()))),
            ("Recebido no mês (caixa)", money(cash_in)), ("Pago no mês (caixa)", money(cash_out)),
        ]),  # fmt: skip
        section("Por categoria", tables=[
            table("Receitas", ["Categoria", "Valor"], [[INCOME_CATEGORIES.get(c, c), money(v)] for c, v in sorted(inc.items())]),
            table("Despesas", ["Categoria", "Valor"], [[EXPENSE_CATEGORIES.get(c, c), money(v)] for c, v in sorted(exp.items())])]),  # fmt: skip
        section("Retenções estimadas pelo cliente", tables=[table("Por fonte planejada", ["Fonte", "Bruto planejado", "Retenção estimada"], tax_rows)],
                notes=["Valores informados pelo cliente (não apurados). Não substituem a apuração fiscal."]),
        section("Lançamentos realizados", tables=[table("Até 1.000 linhas", ["Competência", "Pago em", "Tipo", "Categoria", "Descrição", "Estorno", "Efeito"], lines)]),
    ]  # fmt: skip
    return document("ACCOUNTANT", "Relatório para o contador", label_month(m), period, secs, now)


# ---------- Ano ----------


def year(db, ctx, m, period, now, as_of):
    rows = []
    tot = {"proj": 0, "real": 0, "target": 0, "has_target": False}
    for i in range(12):
        mm = date(m.year, i + 1, 1)
        g = get_goal(db, ctx, mm)
        metric = g.metric_type if g else "PERSONAL_NET"
        n = month_numbers(db, ctx, mm, metric)
        real = _realized(db, ctx, mm)
        rows.append([MONTHS_PT[i].capitalize(), metric_label(metric),
                     money(g.target_cents) if g else "-", money(n["projection"].value_cents),
                     money(n["realized_value"]), money(real["income"]), money(real["expense"])])  # fmt: skip
        tot["proj"] += n["projection"].value_cents
        tot["real"] += n["realized_value"]
        if g:
            tot["target"] += g.target_cents
            tot["has_target"] = True
    secs = [
        section(f"Ano de {m.year}", [
            ("Soma das metas definidas", money(tot["target"]) if tot["has_target"] else "Nenhuma meta"),
            ("Soma do projetado", money(tot["proj"])), ("Soma do realizado", money(tot["real"]))],
            notes=["Cada mês usa o tipo da meta daquele mês (padrão: ganho líquido pessoal); somar tipos "
                   "diferentes serve só como visão geral."]),  # fmt: skip
        section("Mês a mês", tables=[table("Metas, projetado e realizado", [
            "Mês", "Tipo da meta", "Meta", "Projetado", "Realizado", "Receitas lançadas", "Despesas lançadas"], rows)]),
    ]  # fmt: skip
    return document("YEAR", "Relatório anual", str(m.year), period, secs, now)


def metric_label(metric: str) -> str:
    return {"PERSONAL_NET": "Líquida pessoal", "CLINIC_PROFIT": "Lucro do consultório",
            "GROSS_REVENUE": "Faturamento bruto"}[metric]  # fmt: skip
