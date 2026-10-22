"""Consultor IA (PRD §6.11): só explica números que o motor determinístico já calculou.

Nenhuma resposta traz valor que não tenha saído de uma "ferramenta" abaixo (todas somente leitura).
O texto é montado a partir de `Facts`: cada número exibido é registrado como fato, e
`grounded()` confere que nenhum número do texto foi inventado. Um provedor de LLM, quando houver,
só poderá reescrever o texto se passar nessa mesma conferência (ver docs/IMPLEMENTATION_PLAN.md).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.rbac import P_ACQ_READ, P_CLINIC_READ, P_SCHED_READ
from app.db.models import AcquisitionMetric, ClinicService, WorkSource
from app.modules.clinic.router import _economics, _policy_of
from app.modules.goals.projection import (
    build_gap,
    get_goal,
    month_numbers,
    month_str,
    shift_month,
)
from app.modules.schedule.router import overview as schedule_overview
from finance_engine import StreamInput, compute_stream, format_brl, goal_gap_cents
from finance_engine.forecast import weekdays_in_month

MONTHS = [
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]  # fmt: skip
MONTH_LABELS = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]  # fmt: skip

SUGGESTED = [
    "Esse mês preciso ganhar R$ 50 mil; onde mexo?",
    "É melhor um plantão ou 8 consultas a mais?",
    "Se a secretária tirar 15 dias de férias, o que acontece?",
    "Qual é meu trabalho com maior renda por hora comprometida?",
    "Quanto perco com retornos gratuitos?",
    "O que mudou do mês passado para este mês?",
    "Explique os números do mês.",
]

COMMON_ASSUMPTIONS = (
    "Tributos são estimativas informadas por você, não consultoria tributária.",
    "Valores planejados/projetados nunca são somados ao realizado.",
)


# ---------- privacidade ----------

_REDACT = [
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[e-mail removido]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[documento removido]"),
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[documento removido]"),
    (re.compile(r"\(?\b\d{2}\)?\s?9?\d{4}-?\d{4}\b"), "[telefone removido]"),
]


def redact(text: str) -> str:
    """Remove e-mail, CPF/CNPJ e telefone antes de guardar o histórico."""
    for rx, repl in _REDACT:
        text = rx.sub(repl, text)
    return text[:500]


# ---------- fatos ----------


def _hm(minutes: int) -> str:
    h, m = divmod(abs(minutes), 60)
    return f"{'-' if minutes < 0 else ''}{h}h{m:02d}"


def _pct(bps: int) -> str:
    whole, frac = divmod(abs(bps), 100)
    return f"{'-' if bps < 0 else ''}{whole},{frac:02d}%".replace(",00%", "%")


@dataclass
class Facts:
    """Registro de tudo o que a resposta mostra: é o 'Ver cálculo' e a base da conferência."""

    items: list[dict] = field(default_factory=list)

    def add(self, key: str, label: str, value: int, kind: str, source: str) -> str:
        shown = {
            "money": format_brl, "minutes": _hm, "percent": _pct, "count": str, "days": str,
        }[kind](value)  # fmt: skip
        self.items.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "kind": kind,
                "shown": shown,
                "source": source,
            }  # fmt: skip
        )
        return shown

    def tokens(self) -> set[str]:
        return {t for f in self.items for t in re.findall(r"\d+", f["shown"])}


def grounded(text: str, facts: Facts, extra: str = "") -> bool:
    """Todo número do texto precisa vir de um fato (ou do rótulo de mês/ano em `extra`)."""
    allowed = facts.tokens() | set(re.findall(r"\d+", extra))
    return all(t in allowed for t in re.findall(r"\d+", text))


# ---------- interpretação da pergunta ----------


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


_MONEY = re.compile(r"(r\$\s*)?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\s*(mil\b|k\b)?")


def parse_money_cents(text: str) -> int | None:
    t = norm(text)
    for m in _MONEY.finditer(t):
        has_cur, whole, frac, mult = m.groups()
        n = int(whole.replace(".", ""))
        if mult:
            n *= 1000
        elif not has_cur and n < 1000:
            continue  # "8 consultas", "15 dias" não são valores em reais
        cents = int((frac or "0").ljust(2, "0"))
        total = n * 100 + cents
        if 0 < total <= 10**11:
            return total
    return None


def parse_count(text: str, unit: str) -> int | None:
    m = re.search(rf"(\d{{1,3}})\s*(?:{unit})", norm(text))
    return int(m.group(1)) if m else None


def parse_months(text: str, ref: date) -> list[date]:
    t = norm(text)
    found: list[tuple[int, date]] = []
    for i, name in enumerate(MONTHS):
        for m in re.finditer(rf"\b{name}\b", t):
            year = ref.year if i + 1 <= ref.month else ref.year - 1
            found.append((m.start(), date(year, i + 1, 1)))
    return [d for _, d in sorted(found)]


def intent_of(text: str) -> str:
    t = norm(text)
    has = lambda *w: any(x in t for x in w)  # noqa: E731
    if has("ferias", "licenca", "afastamento"):
        return "LEAVE"
    if has("plantao") and has("consulta") and has("ou ", "melhor", "compar", "vale"):
        return "COMPARE"
    if has("retorno") and has("perco", "perda", "gratuit", "gratis", "perdendo"):
        return "RETURNS"
    if has("por hora", "hora comprometida", "renda por hora", "maior renda"):
        return "HOURLY"
    if has("mudou", "mudanca", "diferenca", "variacao", "comparar", "comparado") or (
        has(" para ") and len(parse_months(text, date(2000, 12, 1))) >= 2
    ):
        return "CHANGE"
    if has("ganhar", "preciso", "onde mexo", "bater a meta", "atingir", "falta", "meta"):
        return "GAP"
    if has("explic", "numeros", "resumo", "como estou", "como esta", "situacao"):
        return "OVERVIEW"
    return "UNKNOWN"


# ---------- ferramentas (somente leitura) ----------


@dataclass
class Unit:
    kind: str  # WORK | SERVICE
    name: str
    result: object  # StreamResult de 1 unidade
    key: str = ""


def catalog(db: Session, ctx: TenantContext, with_services: bool) -> list[Unit]:
    """Resultado de 1 unidade de cada trabalho do usuário e (se permitido) de cada serviço."""
    out: list[Unit] = []
    for w in db.scalars(
        select(WorkSource).where(
            WorkSource.organization_id == ctx.organization_id,
            WorkSource.owner_user_id == ctx.user.id,
            WorkSource.deleted_at.is_(None),
            WorkSource.status == "active",
        )
    ):
        if w.work_type == "CLINIC":
            continue
        r = w.income_rule
        si = StreamInput(
            f"WORK:{w.id}", "WORK", 1, "PROBABLE", r.gross_cents, r.variable_cost_cents,
            r.tax_rate_bps, r.duration_minutes * 10, (r.commitment_minutes or r.duration_minutes) * 10,
        )  # fmt: skip
        u = Unit("WORK", w.name, compute_stream(si), w.work_type)
        out.append(u)
    if with_services:
        for s in db.scalars(
            select(ClinicService).where(
                ClinicService.organization_id == ctx.organization_id,
                ClinicService.deleted_at.is_(None),
                ClinicService.status == "active",
            )
        ):
            econ, _ = _economics(s, _policy_of(db, ctx, s), s.price_cents)
            si = StreamInput(
                f"SERVICE:{s.id}", "SERVICE", 1, "PROBABLE", econ.gross_revenue_cents,
                econ.fee_cents + econ.variable_cost_cents, 0, econ.minutes_x10, econ.minutes_x10,
            )  # fmt: skip
            out.append(Unit("SERVICE", s.name, compute_stream(si)))
    return out


def _per_hour(u: Unit) -> int:
    v = u.result.net_per_committed_hour_cents
    return v if v is not None else -(10**12)


def _named(units: list[Unit], text: str, kind: str, pick) -> Unit | None:
    t = norm(text)
    cands = [u for u in units if u.kind == kind]
    for u in cands:
        if norm(u.name) in t:
            return u
    return max(cands, key=pick) if cands else None


def _answer(intent: str, month: date) -> dict:
    return {
        "intent": intent, "month": month_str(month), "summary": "", "data_used": [],
        "scenarios": [], "constraints": [], "risks": [], "actions": [], "simulation_link": None,
        "facts": [], "assumptions": list(COMMON_ASSUMPTIONS), "grounded": True,
    }  # fmt: skip


def _finish(a: dict, f: Facts, month: date, question: str) -> dict:
    a["facts"] = f.items
    text = " ".join([a["summary"], *a["scenarios"], *a["constraints"], *a["risks"]])
    # rótulos de mês/ano e números da própria pergunta não são "inventados"
    extra = f"{question} {month_str(month)} {a['data_used']}"
    a["grounded"] = grounded(text, f, extra)
    return a


def _schedule_constraints(db: Session, ctx: TenantContext, month: date, f: Facts) -> list[str]:
    if not ctx.can(P_SCHED_READ):
        return []
    ov = schedule_overview(month=month_str(month), user_id=None, ctx=ctx, db=db)
    if ov["feasible"]:
        return ["A agenda do mês está dentro dos limites; horas extras precisam de espaço nela."]
    return ["A agenda do mês já tem conflitos ou excessos: confira antes de somar horas."]


def _quality_risks(nums: dict) -> list[str]:
    risks = []
    if not nums["results"]:
        risks.append("Sem plano de renda para o mês, a projeção considera só as despesas.")
    elif all(r.tax_cents == 0 for r in nums["results"]):
        risks.append("Nenhum tributo informado: o líquido pode estar superestimado.")
    prob = nums["projection"].probable_cents
    if prob:
        risks.append(
            "Parte da projeção é 'provável', não garantida; confirme antes de contar com ela."
        )
    return risks


def ask_gap(db, ctx, month, text, target_override, facts: Facts) -> dict:
    a = _answer("GAP", month)
    g = get_goal(db, ctx, month)
    metric = g.metric_type if g else "PERSONAL_NET"
    nums = month_numbers(db, ctx, month, metric)
    proj = nums["projection"]
    projected = facts.add("projected", "Projeção do mês", proj.value_cents, "money", "projeção")
    a["data_used"] = [f"Projeção de {MONTH_LABELS[month.month - 1]} (planos de renda e despesas)"]

    target = target_override or parse_money_cents(text)
    if target is None and g:
        target = build_gap(db, ctx, g, nums)["effective_target_cents"]
        a["data_used"].append("Sua meta do mês")
    if target is None:
        a["summary"] = (
            f"Sua projeção do mês é {projected}, mas não há meta definida nem valor na pergunta. "
            "Defina uma meta ou diga quanto quer ganhar."
        )
        a["actions"] = [{"label": "Definir a meta", "route": "/app/goals"}]
        return a
    t = facts.add("target", "Meta considerada", target, "money", "pergunta/meta")
    gap = goal_gap_cents(target, proj.value_cents)
    gap_s = facts.add("gap", "Falta para a meta", gap, "money", "meta − projeção")
    if gap == 0:
        a["summary"] = (
            f"Com a projeção de {projected}, a meta de {t} já é atingida (se o plano se cumprir)."
        )
        a["risks"] = _quality_risks(nums)
        a["actions"] = [{"label": "Revisar a composição do mês", "route": "/app/goals"}]
        return a
    a["summary"] = f"Para chegar a {t} faltam {gap_s} sobre a projeção atual de {projected}."
    cands = sorted(
        (r for r in nums["results"] if r.units > 0 and not r.key.endswith(":extra")),
        key=lambda r: -(r.net_per_committed_hour_cents or -(10**12)),
    )[:3]
    for r in cands:
        name = nums["meta"][r.key]["name"]
        unit_net = r.net_cents // r.units
        if unit_net <= 0:
            continue
        need = -(-gap // unit_net)  # teto
        minutes = need * (-(-r.committed_minutes // r.units))
        n = facts.add(
            f"extra_units_{r.key}",
            f"Unidades extras de {name}",
            need,
            "count",
            "lacuna ÷ líquido/un.",
        )
        h = facts.add(
            f"extra_hours_{r.key}",
            f"Horas comprometidas extras ({name})",
            minutes,
            "minutes",
            "unidades × horas/un.",
        )
        u = facts.add(
            f"unit_net_{r.key}", f"Líquido por unidade de {name}", unit_net, "money", "plano do mês"
        )
        a["scenarios"].append(
            f"Só com {name}: {n} unidade(s) a mais ({u} líquidos cada), cerca de {h} comprometidas."
        )
    if not a["scenarios"]:
        a["scenarios"] = ["Nenhuma fonte planejada para estimar quantas unidades faltam."]
    a["constraints"] = [
        "Cada cenário usa uma única fonte e ignora descanso e limites de carga: use o Otimizador "
        "para combinar fontes de forma viável.",
        *_schedule_constraints(db, ctx, month, facts),
    ]
    a["risks"] = _quality_risks(nums)
    a["actions"] = [
        {"label": "Simular combinações no Otimizador", "route": "/app/optimizer"},
        {"label": "Ajustar a composição do mês", "route": "/app/goals"},
    ]
    a["simulation_link"] = "/app/optimizer"
    return a


def ask_compare(db, ctx, month, text, facts: Facts) -> dict:
    a = _answer("COMPARE", month)
    units = catalog(db, ctx, ctx.can(P_CLINIC_READ))
    shift = _named(units, text, "WORK", _per_hour) if units else None
    shifts = [u for u in units if u.kind == "WORK" and u.key == "SHIFT"]
    if shifts:
        shift = _named(shifts, text, "WORK", lambda u: u.result.net_cents)
    svc = _named(units, text, "SERVICE", _per_hour)
    n = parse_count(text, "consultas?|atendimentos?|pacientes?") or 8
    if shift is None or svc is None:
        a["summary"] = (
            "Preciso de ao menos um plantão e um serviço de consultório cadastrados para comparar."
        )
        a["actions"] = [{"label": "Cadastrar trabalhos", "route": "/app/work"}]
        return a
    qty = facts.add("n_consults", "Consultas comparadas", n, "count", "pergunta")
    s_net = facts.add(
        "shift_net", f"Líquido de 1 {shift.name}", shift.result.net_cents, "money", "regra de renda"
    )
    s_min = facts.add(
        "shift_minutes",
        "Horas comprometidas do plantão",
        shift.result.committed_minutes,
        "minutes",
        "regra de renda",
    )
    c_net_total = svc.result.net_cents * n
    c_net = facts.add(
        "consult_net", f"Líquido de {n} × {svc.name}", c_net_total, "money", "economia da consulta"
    )
    c_min_total = svc.result.committed_minutes * n
    c_min = facts.add(
        "consult_minutes",
        "Horas comprometidas nas consultas",
        c_min_total,
        "minutes",
        "economia da consulta",
    )
    a["data_used"] = [
        f"Regra de renda de {shift.name}",
        f"Economia de {svc.name} (com retornos esperados)",
    ]
    sh_h = shift.result.net_per_committed_hour_cents
    cs_h = (c_net_total * 60) // c_min_total if c_min_total else None
    if sh_h is not None:
        facts.add("shift_hour", "Líquido/hora do plantão", sh_h, "money", "líquido ÷ horas")
    if cs_h is not None:
        facts.add("consult_hour", "Líquido/hora das consultas", cs_h, "money", "líquido ÷ horas")
    a["summary"] = (
        f"1 {shift.name} rende {s_net} líquidos em {s_min}; {qty} consultas ({svc.name}) rendem {c_net} em {c_min}."
    )
    if sh_h is not None and cs_h is not None:
        by_hour = shift.name if sh_h >= cs_h else f"as {qty} consultas"
        by_net = shift.name if shift.result.net_cents >= c_net_total else f"as {qty} consultas"
        a["scenarios"] = [
            f"Por renda líquida total, {by_net} rende(m) mais.",
            f"Por hora comprometida, {by_hour} rende(m) mais "
            f"({facts.items[-2]['shown']} contra {facts.items[-1]['shown']}).",
        ]
    a["constraints"] = [
        "Consultas dependem de demanda (agenda cheia) e de sala/equipe; o plantão tem renda fixa.",
        *_schedule_constraints(db, ctx, month, facts),
    ]
    a["risks"] = [
        "Consultas assumem que todos os horários serão preenchidos; faltas já entram na estimativa."
    ]
    a["actions"] = [{"label": "Simular no Otimizador", "route": "/app/optimizer"}]
    a["simulation_link"] = "/app/optimizer"
    return a


def ask_hourly(db, ctx, month, facts: Facts) -> dict:
    a = _answer("HOURLY", month)
    units = [
        u for u in catalog(db, ctx, ctx.can(P_CLINIC_READ)) if u.result.net_per_committed_hour_cents
    ]
    units.sort(key=_per_hour, reverse=True)
    a["data_used"] = ["Regras de renda dos trabalhos", "Economia dos serviços (com retornos)"]
    if not units:
        a["summary"] = "Ainda não há trabalhos ou serviços com tempo e valor cadastrados."
        a["actions"] = [{"label": "Cadastrar trabalhos", "route": "/app/work"}]
        return a
    for i, u in enumerate(units[:5]):
        v = facts.add(
            f"hour_{i}",
            f"Líquido/hora comprometida — {u.name}",
            u.result.net_per_committed_hour_cents,
            "money",
            "líquido ÷ horas",
        )
        a["scenarios"].append(f"{u.name}: {v} por hora comprometida.")
    top = units[0]
    a["summary"] = f"O maior líquido por hora comprometida é {top.name}: {facts.items[0]['shown']}."
    a["constraints"] = [
        "Hora comprometida inclui deslocamento e espera; renda por hora não mede cansaço nem demanda."
    ]
    a["risks"] = [
        "Serviços usam preço e retornos cadastrados; tributos do serviço só entram se informados no plano."
    ]
    a["actions"] = [{"label": "Rever trabalhos e rendas", "route": "/app/work"}]
    return a


def ask_returns(db, ctx, month, facts: Facts) -> dict:
    a = _answer("RETURNS", month)
    if not ctx.can(P_CLINIC_READ):
        a["summary"] = "Você não tem permissão para ver os dados do consultório."
        return a
    a["data_used"] = ["Serviços e políticas de retorno do consultório"]
    lines = 0
    for s in db.scalars(select(ClinicService).where(
        ClinicService.organization_id == ctx.organization_id,
        ClinicService.deleted_at.is_(None), ClinicService.status == "active")):  # fmt: skip
        p = _policy_of(db, ctx, s)
        econ, rp = _economics(s, p, s.price_cents)
        if p is None or rp["return_rate_bps"] == 0:
            continue
        lines += 1
        free = rp["return_price_cents"] == 0
        mins = econ.expected_returns_bps * rp["return_minutes"] // 10_000
        rate = facts.add(
            f"ret_rate_{lines}",
            f"Retornos por consulta nova — {s.name}",
            econ.expected_returns_bps,
            "percent",
            "política de retorno",
        )
        m = facts.add(
            f"ret_min_{lines}",
            f"Minutos de retorno por consulta nova — {s.name}",
            mins,
            "minutes",
            "taxa × duração",
        )
        if free and econ.margin_per_hour_cents:
            loss = mins * econ.margin_per_hour_cents // 60
            lv = facts.add(
                f"ret_loss_{lines}",
                f"Custo de oportunidade por consulta nova — {s.name}",
                loss,
                "money",
                "minutos × margem/hora",
            )
            a["scenarios"].append(
                f"{s.name}: {rate} de retornos gratuitos ocupam {m} por consulta nova; "
                f"se esse tempo gerasse consultas novas, seriam cerca de {lv} de margem."
            )
        else:
            rv = facts.add(
                f"ret_rev_{lines}",
                f"Receita de retornos por consulta nova — {s.name}",
                econ.revenue_returns_cents,
                "money",
                "política de retorno",
            )
            a["scenarios"].append(
                f"{s.name}: retornos pagos ({rate}) somam {rv} por consulta nova."
            )
    if not lines:
        a["summary"] = (
            "Nenhum serviço tem política de retorno cadastrada, então não há retorno a medir."
        )
        a["actions"] = [{"label": "Cadastrar política de retorno", "route": "/app/clinic"}]
        return a
    a["summary"] = (
        "Retornos gratuitos não geram receita, mas ocupam agenda: veja o custo de oportunidade estimado."
    )
    a["constraints"] = ["Só vale se houver demanda para preencher o horário liberado."]
    a["risks"] = ["Custo de oportunidade é uma hipótese, não dinheiro perdido de fato."]
    a["actions"] = [{"label": "Ajustar políticas de retorno", "route": "/app/clinic"}]
    return a


def ask_change(db, ctx, month, text, facts: Facts) -> dict:
    a = _answer("CHANGE", month)
    ms = parse_months(text, month)
    if len(ms) >= 2:
        before, after = ms[0], ms[1]
    elif len(ms) == 1:
        before, after = ms[0], shift_month(ms[0], 1)
    else:
        before, after = shift_month(month, -1), month
    a["month"] = month_str(after)
    g = get_goal(db, ctx, after)
    metric = g.metric_type if g else "PERSONAL_NET"
    n0, n1 = month_numbers(db, ctx, before, metric), month_numbers(db, ctx, after, metric)
    lb, la = MONTH_LABELS[before.month - 1], MONTH_LABELS[after.month - 1]
    a["data_used"] = [
        f"Projeção e realizado de {month_str(before)}",
        f"Projeção e realizado de {month_str(after)}",
    ]
    rows = [
        ("projected", "Projetado", n0["projection"].value_cents, n1["projection"].value_cents),
        ("realized", "Realizado", n0["realized_value"], n1["realized_value"]),
        ("expenses", "Despesas (realizadas + planejadas)", n0["expenses"], n1["expenses"]),
    ]
    for k, label, v0, v1 in rows:
        s0 = facts.add(f"{k}_0", f"{label} — {month_str(before)}", v0, "money", "motor")
        s1 = facts.add(f"{k}_1", f"{label} — {month_str(after)}", v1, "money", "motor")
        d = facts.add(f"{k}_delta", f"{label}: variação", v1 - v0, "money", "diferença")
        a["scenarios"].append(f"{label}: {s0} em {lb} → {s1} em {la} (variação {d}).")
    a["summary"] = a["scenarios"][0]
    c0, c1 = n0["finance"]["expense_by_category_cents"], n1["finance"]["expense_by_category_cents"]
    moves = sorted(
        ((c, c1.get(c, 0) - c0.get(c, 0)) for c in set(c0) | set(c1)), key=lambda x: -abs(x[1])
    )
    for c, d in moves[:3]:
        if d:
            a["constraints"].append(
                f"Despesa em {c}: variação de {facts.add(f'cat_{c}', f'Variação em {c}', d, 'money', 'categorias')}."
            )
    a["risks"] = [
        "Comparar mês fechado com mês em andamento exagera a diferença: o realizado ainda está incompleto."
    ]
    a["actions"] = [{"label": "Ver o painel do mês", "route": "/app/dashboard"}]
    return a


def ask_leave(db, ctx, month, text, facts: Facts) -> dict:
    a = _answer("LEAVE", month)
    t = norm(text)
    days = parse_count(text, "dias?") or 0
    if days <= 0:
        a["summary"] = (
            "Diga quantos dias (por exemplo: '15 dias de férias') para eu estimar o efeito."
        )
        return a
    d = facts.add("leave_days", "Dias de férias considerados", days, "days", "pergunta")
    secretary = "secretaria" in t or "assistente" in t
    dim = (shift_month(month, 1) - month).days
    if secretary:
        a["data_used"] = [
            "Cobertura da secretária (hipótese: sem cobertura nos dias de férias)",
            f"Captação de {month_str(month)}",
        ]
        lost_bps = min(days, dim) * 10_000 // dim
        cov = facts.add(
            "coverage_lost",
            "Parte do mês sem atendimento de contatos",
            lost_bps,
            "percent",
            "dias ÷ dias do mês",
        )
        a["summary"] = (
            f"{d} dias sem a secretária deixam cerca de {cov} do mês sem atendimento de novos contatos."
        )
        if ctx.can(P_ACQ_READ):
            bookings = db.scalar(select(func.coalesce(func.sum(AcquisitionMetric.bookings_count), 0)).where(
                AcquisitionMetric.organization_id == ctx.organization_id,
                AcquisitionMetric.month == month)) or 0  # fmt: skip
            if bookings:
                lost = (bookings * lost_bps) // 10_000
                b = facts.add(
                    "bookings", "Agendamentos do mês (captação)", bookings, "count", "captação"
                )
                lb = facts.add(
                    "lost_bookings",
                    "Agendamentos em risco",
                    lost,
                    "count",
                    "agendamentos × parte sem atendimento",
                )
                a["scenarios"] = [f"Dos {b} agendamentos do mês, cerca de {lb} ficam em risco."]
        a["constraints"] = [
            "A estimativa supõe que ninguém cobre a secretária e que contatos se perdem proporcionalmente."
        ]
        a["risks"] = [
            "Contatos recentes costumam esperar; a perda real depende de resposta automática e de cobertura."
        ]
        a["actions"] = [
            {"label": "Registrar a licença e a cobertura", "route": "/app/schedule"},
            {"label": "Ver captação", "route": "/app/acquisition"},
        ]
        return a
    # férias do médico: dias úteis perdidos reduzem a capacidade de atender
    a["data_used"] = [
        f"Projeção de {month_str(month)}",
        "Dias úteis do mês (hipótese: férias a partir do dia 1)",
    ]
    wd = weekdays_in_month(month.year, month.month)
    start_cal = [date(month.year, month.month, i) for i in range(1, min(days, dim) + 1)]
    lost = sum(1 for x in start_cal if x.weekday() < 5)
    nums = month_numbers(db, ctx, month, "PERSONAL_NET")
    income_net = sum(r.net_cents for r in nums["results"])
    cut = income_net * lost // len(wd) if wd else 0
    wl = facts.add("lost_days", "Dias úteis perdidos", lost, "count", "calendário")
    wt = facts.add("work_days", "Dias úteis do mês", len(wd), "count", "calendário")
    net = facts.add("net_planned", "Líquido planejado das fontes", income_net, "money", "projeção")
    cv = facts.add(
        "net_cut",
        "Redução estimada do líquido",
        cut,
        "money",
        "líquido × dias perdidos ÷ dias úteis",
    )
    a["summary"] = (
        f"{d} dias de férias tiram {wl} dos {wt} dias úteis; do líquido planejado de {net}, cerca de {cv} ficam em risco."
    )
    a["constraints"] = [
        "Supõe renda distribuída por igual nos dias úteis; plantões fixos podem ser afetados de forma diferente."
    ]
    a["risks"] = ["É uma estimativa proporcional, não uma reprogramação da agenda."]
    a["actions"] = [
        {"label": "Registrar as férias na agenda", "route": "/app/schedule"},
        {"label": "Ajustar o plano do mês", "route": "/app/goals"},
    ]
    return a


def ask_overview(db, ctx, month, facts: Facts) -> dict:
    a = _answer("OVERVIEW", month)
    g = get_goal(db, ctx, month)
    metric = g.metric_type if g else "PERSONAL_NET"
    nums = month_numbers(db, ctx, month, metric)
    proj = nums["projection"]
    a["data_used"] = [f"Projeção e realizado de {month_str(month)}"]
    p = facts.add("projected", "Projetado", proj.value_cents, "money", "motor")
    r = facts.add("realized", "Realizado até agora", nums["realized_value"], "money", "lançamentos")
    e = facts.add(
        "expenses", "Despesas (realizadas + planejadas)", nums["expenses"], "money", "finanças"
    )
    a["summary"] = (
        f"Em {MONTH_LABELS[month.month - 1]}: projetado {p}, realizado até agora {r}, despesas {e}."
    )
    if g:
        gap = build_gap(db, ctx, g, nums)
        t = facts.add("target", "Meta", gap["effective_target_cents"], "money", "meta")
        gp = facts.add("gap", "Falta para a meta", gap["gap_cents"], "money", "meta − projeção")
        a["scenarios"] = [f"Meta de {t}: faltam {gp} na projeção."]
    a["constraints"] = _schedule_constraints(db, ctx, month, facts)
    a["risks"] = _quality_risks(nums)
    a["actions"] = [{"label": "Abrir o painel", "route": "/app/dashboard"}]
    return a


def answer(db: Session, ctx: TenantContext, text: str, month: date, target_override: int | None):
    """(resposta estruturada, ferramentas usadas). Não grava nada."""
    facts = Facts()
    it = intent_of(text)
    if it == "GAP":
        a = ask_gap(db, ctx, month, text, target_override, facts)
    elif it == "COMPARE":
        a = ask_compare(db, ctx, month, text, facts)
    elif it == "HOURLY":
        a = ask_hourly(db, ctx, month, facts)
    elif it == "RETURNS":
        a = ask_returns(db, ctx, month, facts)
    elif it == "CHANGE":
        a = ask_change(db, ctx, month, text, facts)
    elif it == "LEAVE":
        a = ask_leave(db, ctx, month, text, facts)
    elif it == "OVERVIEW":
        a = ask_overview(db, ctx, month, facts)
    else:
        a = _answer("UNKNOWN", month)
        a["summary"] = (
            "Não consegui ligar essa pergunta a um cálculo que eu possa explicar. "
            "Não invento números: tente uma das perguntas sugeridas."
        )
    return _finish(a, facts, month, text), [it]
