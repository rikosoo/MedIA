# FinanceMed AI

SaaS de planejamento financeiro e otimização de agenda para médicos. Especificação completa em [`docs/PRD.md`](docs/PRD.md); plano de execução em [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

**Estado atual: Sprints 0–11** (fundamentos, organizações, RBAC, trabalhos e rendas, consultório com retornos, receitas e despesas, agenda e capacidade, metas e dashboard, otimizador misto, realizado e importação CSV, relatórios PDF/CSV, alertas e fechamento mensal, captação e previsões, consultor IA somente leitura, planos e cobrança simulada, hardening beta, custos e ponto de equilíbrio). Provedor de pagamento real e LLM externo ainda não existem.

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

Requisitos: Python 3.11+, Node 22 e um PostgreSQL 16 (via Docker ou instalado na máquina).

1. **Banco de dados.** Com Docker: `docker compose up db -d`. Sem Docker, crie no seu PostgreSQL o usuário `financemed` (senha `financemed`) e o banco `financemed`.
2. **Variáveis de ambiente.** `cp .env.example .env` na raiz. Os valores padrão já funcionam em dev (a API usa os mesmos defaults se não houver `.env`). Em `ENVIRONMENT=prod` é obrigatório definir `SECRET_KEY` próprio e `COOKIE_SECURE=true`.
3. **API.**
   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -e packages/finance_engine -e packages/optimizer_engine -e "services/api[dev]"
   cd services/api && alembic upgrade head
   uvicorn app.main:app --reload      # http://localhost:8000/docs (health: /health)
   ```
4. **Web** (outro terminal): `cd apps/web && npm ci && npm run dev` → http://localhost:3000. A URL da API vem de `NEXT_PUBLIC_API_URL` (padrão `http://localhost:8000`).
5. **Primeiro acesso.** Não há usuário de exemplo: crie sua conta em http://localhost:3000/login (aceitando os termos) e a organização é criada junto.

Alternativa: `docker compose up --build` sobe banco, API (com migrations) e web.

Observação: o `.env` é lido a partir do diretório em que a API roda (`services/api`); como todos os valores têm default, só copie o arquivo para lá se for alterar algo.

## Testes e lint

```bash
pytest packages/finance_engine packages/optimizer_engine
cd services/api && pytest && ruff check . && ruff format --check .
cd apps/web && npm run lint && npm run typecheck && npm test && npm run build
```

Os testes da API (472), do motor financeiro (109) e do otimizador (21) usam SQLite em memória por padrão; com `TEST_DATABASE_URL=postgresql+psycopg://...` a suíte da API roda em PostgreSQL real; a CI também roda as migrations (up/down/up) em PostgreSQL 16.

Deploy na AWS (beta): [docs/DEPLOY_AWS.md](docs/DEPLOY_AWS.md). Operação e segurança: [docs/SECURITY.md](docs/SECURITY.md) (autenticação, LGPD, retenção, backup/restauração) e [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md) (estado real do checklist do PRD §22). Scripts: `scripts/backup.sh`, `scripts/restore_check.sh`; retenção: `python -m app.maintenance purge`.

## Decisões relevantes

- **Dinheiro** sempre em centavos inteiros; taxas em pontos-base (1500 = 15%). Nenhum float em cálculo financeiro.
- **Sessão** por cookie `httpOnly` + token CSRF obrigatório em toda requisição que altera estado (`X-CSRF-Token`).
- **Multi-tenant**: rotas de negócio exigem `X-Organization-Id`; o vínculo (membership) é consultado a cada request, então revogar acesso é imediato. Organização alheia e objeto alheio respondem `404`, sem revelar existência.
- **RBAC** em `services/api/app/core/rbac.py` (matriz papel → permissões). `ASSISTANT` não enxerga rendas; `PHYSICIAN` vê só as próprias; `MANAGER`/`ACCOUNTANT` leem tudo da organização (contador sem escrita).
- **Tributos** são estimativas informadas pelo usuário (`tax_is_estimate: true` em toda resposta).
