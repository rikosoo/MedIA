# Plano de implementação incremental

Fonte: `docs/PRD.md` §18 (sprints) e §19 (backlog). Regras invioláveis: §23.

## Entregue nesta etapa

| Sprint | Escopo | Evidência |
|---|---|---|
| 0 — Fundamentos | Monorepo, Docker (db/api/web), CI, auth por sessão+CSRF, migrations, `finance_engine` | `pytest`, `alembic up/down/up` em PG16, `next build`, e2e manual em Chromium |
| 1 — Perfis e rendas | Organizações, memberships, RBAC, CRUD de trabalhos com remuneração (US-001), ganho/hora, soft-delete, auditoria | 22 testes de API, incl. cross-tenant e papéis (US-010, US-012) |

## Próximas sprints

| Sprint | Dependências | Critério de aceite (PRD) | Principais riscos |
|---|---|---|---|
| 2 Consultório e custos | 1 | Fórmula de retornos (§7.2: 40min/20min/76% → 55,2 min) com testes; retorno no mês correto (US-002) | Competência vs caixa |
| 3 Agenda e capacidade | 2 | Slots reais, feriados/férias, descanso e colisões; sobreaviso condicional | Modelagem de calendário |
| 4 Metas e dashboard | 2, 3 | Lacuna auditável (US-003); plano ≠ realizado | Dupla contagem de renda |
| 5 Otimizador | 3, 4 | CP-SAT, 5 objetivos, validação independente, caso inviável com máximo alcançável (US-004..006) | Determinismo; unidades em centavos/minutos |
| 6–11 | Ver PRD §18 | Realizado/CSV, relatórios, captação, IA, cobrança, hardening | LGPD, prompt injection, tributação |

## Pendências conhecidas da Sprint 0/1

- Sem rate limiting / bloqueio por tentativas no login, sem 2FA, sem recuperação de senha e sem verificação de e-mail.
- Convite por e-mail não existe: `POST /team/members` vincula usuário já cadastrado. Não há tela de Equipe.
- Sem RLS no PostgreSQL (o isolamento hoje é só na camada de aplicação, testado).
- Tailwind não foi adotado (CSS simples com tokens e dark mode); decisão reversível.
- Playwright não está na CI; o fluxo e2e foi verificado manualmente.
- Edição e arquivamento de trabalhos existem na API, mas não na UI.
- Seed fictício opcional não implementado.
- Docker não foi exercitado neste ambiente (validado com PostgreSQL local); `docker compose up` ainda precisa ser rodado.
