# FinanceMed AI

SaaS de planejamento financeiro e otimização de agenda para médicos. Especificação completa em [`docs/PRD.md`](docs/PRD.md); plano de execução em [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

**Estado atual: Sprints 0–6** (fundamentos, organizações, RBAC, trabalhos e rendas, consultório com retornos, receitas e despesas, agenda e capacidade, metas e dashboard, otimizador misto, realizado e importação CSV). Captação, previsões, relatórios em PDF, consultor de IA e cobrança ainda não existem.

## Estrutura

```
apps/web                 Next.js (TS) — login, shell autenticado, Trabalhos & Rendas, Consultório, Receitas & Despesas, Agenda & Capacidade, Minha Meta, dashboard, Otimizador Misto
services/api             FastAPI + SQLAlchemy + Alembic
packages/finance_engine  Cálculos puros em centavos inteiros (sem LLM, sem I/O)
packages/optimizer_engine  Otimizador misto (OR-Tools CP-SAT) + validação independente
docs/                    PRD e plano
infra/docker             Dockerfiles
```

## Rodando

Requisitos: Python 3.11+, Node 22, Docker (ou um PostgreSQL 16).

```bash
cp .env.example .env
docker compose up db -d                       # PostgreSQL

python -m venv .venv && . .venv/bin/activate
pip install -e packages/finance_engine -e packages/optimizer_engine -e "services/api[dev]"
cd services/api && alembic upgrade head
uvicorn app.main:app --reload                 # http://localhost:8000/docs

cd apps/web && npm ci && npm run dev          # http://localhost:3000
```

Tudo via Docker: `docker compose up --build` (API aplica as migrations ao subir).

## Testes e lint

```bash
pytest packages/finance_engine packages/optimizer_engine
cd services/api && pytest && ruff check . && ruff format --check .
cd apps/web && npm run lint && npm run typecheck && npm test && npm run build
```

Os testes da API (193), do motor financeiro (84) e do otimizador (21) usam SQLite em memória por padrão; com `TEST_DATABASE_URL=postgresql+psycopg://...` a suíte da API roda em PostgreSQL real; a CI também roda as migrations (up/down/up) em PostgreSQL 16.

## Decisões relevantes

- **Dinheiro** sempre em centavos inteiros; taxas em pontos-base (1500 = 15%). Nenhum float em cálculo financeiro.
- **Sessão** por cookie `httpOnly` + token CSRF obrigatório em toda requisição que altera estado (`X-CSRF-Token`).
- **Multi-tenant**: rotas de negócio exigem `X-Organization-Id`; o vínculo (membership) é consultado a cada request, então revogar acesso é imediato. Organização alheia e objeto alheio respondem `404`, sem revelar existência.
- **RBAC** em `services/api/app/core/rbac.py` (matriz papel → permissões). `ASSISTANT` não enxerga rendas; `PHYSICIAN` vê só as próprias; `MANAGER`/`ACCOUNTANT` leem tudo da organização (contador sem escrita).
- **Tributos** são estimativas informadas pelo usuário (`tax_is_estimate: true` em toda resposta).
