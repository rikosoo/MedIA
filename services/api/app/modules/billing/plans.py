"""Catálogo de planos (PRD §17). Preços e limites são hipóteses para validação, não pesquisa de mercado."""

TRIAL_DAYS = 14
PERIOD_DAYS = 30
GRACE_DAYS = 7  # falha de pagamento: mantém acesso por 7 dias antes de voltar ao Gratuito

ORDER = ["FREE", "PRO", "PRO_AI", "CLINIC"]
RANK = {p: i for i, p in enumerate(ORDER)}

PLANS: dict[str, dict] = {
    "FREE": {
        "name": "Gratuito", "price_cents": 0,
        "summary": "1 meta por mês, até 2 trabalhos e 2 simulações por mês.",
        "limits": {"works": 2, "goals_per_month": 1, "optimizer_runs_per_month": 2},
        "features": [],
    },
    "PRO": {
        "name": "Pro", "price_cents": 3_900,
        "summary": "Trabalhos, despesas, histórico e relatórios; até 10 simulações por mês.",
        "limits": {"works": 50, "goals_per_month": None, "optimizer_runs_per_month": 10},
        "features": ["reports"],
    },
    "PRO_AI": {
        "name": "Pro AI", "price_cents": 7_900,
        "summary": "Otimizador completo, previsões e Consultor IA com limite de uso justo.",
        "limits": {"works": 50, "goals_per_month": None, "optimizer_runs_per_month": None,
                   "assistant_per_day": 30},
        "features": ["reports", "forecasts", "assistant"],
    },
    "CLINIC": {
        "name": "Clínica", "price_cents": 19_900,
        "summary": "Até 5 profissionais, equipe com permissões e tudo do Pro AI.",
        "limits": {"works": 200, "goals_per_month": None, "optimizer_runs_per_month": None,
                   "assistant_per_day": 60, "professionals": 5},
        "features": ["reports", "forecasts", "assistant", "team"],
    },
}  # fmt: skip

FEATURE_MIN_PLAN = {
    "reports": "PRO",
    "forecasts": "PRO_AI",
    "assistant": "PRO_AI",
    "team": "CLINIC",
}
FEATURE_LABEL = {
    "reports": "Relatórios", "forecasts": "Previsões", "assistant": "Consultor IA",
    "team": "Equipe e permissões",
}  # fmt: skip
LIMIT_LABEL = {
    "works": "trabalhos", "goals_per_month": "metas por mês",
    "optimizer_runs_per_month": "simulações do otimizador por mês",
    "assistant_per_day": "perguntas ao consultor por dia", "professionals": "profissionais",
}  # fmt: skip
