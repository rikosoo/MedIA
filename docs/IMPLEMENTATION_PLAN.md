# Plano de implementação incremental

Fonte: `docs/PRD.md` §18 (sprints) e §19 (backlog). Regras invioláveis: §23.

## Entregue nesta etapa

| Sprint | Escopo | Evidência |
|---|---|---|
| 0 — Fundamentos | Monorepo, Docker (db/api/web), CI, auth por sessão+CSRF, migrations, `finance_engine` | `pytest`, `alembic up/down/up` em PG16, `next build`, e2e manual em Chromium |
| 1 — Perfis e rendas | Organizações, memberships, RBAC, CRUD de trabalhos com remuneração (US-001), ganho/hora, soft-delete, auditoria | 22 testes de API, incl. cross-tenant e papéis (US-010, US-012) |
| 2 — Consultório e custos | Serviços e políticas de retorno (gratuito/parcial/integral/personalizado, prazo, janela gratuita, faltas), economia por consulta nova, capacidade, agenda de retornos por mês, simulação de preço; lançamentos de receita/despesa com competência x caixa, estornos, detecção de duplicidade e recorrências planejadas (US-002) | Motor: 27 testes (55,2 min; retorno gratuito cai no mês seguinte). API: 54 testes. Migration 0002 up/down/up em PG16. E2E em Chromium |
| 3 — Agenda e capacidade | Disponibilidade (turno semanal), eventos (consulta, retorno, plantão, sobreaviso, CLT…), feriados com confirmação manual, férias, folgas, limites de jornada; conflitos (sobreaviso só com permissão explícita), descanso após plantão, horas ativas x comprometidas sem dupla contagem, slots reais por janela, capacidade de pacientes novos por serviço | Motor: 41 testes. API: 91 testes, também em PostgreSQL real (`TEST_DATABASE_URL`). Migration 0003 up/down/up. E2E em Chromium (achou 2 bugs de UI/CORS, corrigidos) |

## Próximas sprints

| Sprint | Dependências | Critério de aceite (PRD) | Principais riscos |
|---|---|---|---|
| 4 Metas e dashboard | 2, 3 | Lacuna auditável (US-003); plano ≠ realizado | Dupla contagem de renda |
| 5 Otimizador | 3, 4 | CP-SAT, 5 objetivos, validação independente, caso inviável com máximo alcançável (US-004..006) | Determinismo; unidades em centavos/minutos |
| 6–11 | Ver PRD §18 | Realizado/CSV, relatórios, captação, IA, cobrança, hardening | LGPD, prompt injection, tributação |

## Pendências conhecidas (Sprints 0–2)

- Sem rate limiting / bloqueio por tentativas no login, sem 2FA, sem recuperação de senha e sem verificação de e-mail.
- Convite por e-mail não existe: `POST /team/members` vincula usuário já cadastrado. Não há tela de Equipe.
- Sem RLS no PostgreSQL (o isolamento hoje é só na camada de aplicação, testado).
- Tailwind não foi adotado (CSS simples com tokens e dark mode); decisão reversível.
- Playwright não está na CI; o fluxo e2e foi verificado manualmente.
- Edição e arquivamento de trabalhos existem na API, mas não na UI.
- Seed fictício opcional não implementado.
- Docker não foi exercitado neste ambiente (validado com PostgreSQL local); `docker compose up` ainda precisa ser rodado.
- Sprint 2: categorias de lançamento são constantes no código (sem a tabela `expense_categories` do PRD §11); isso não permite categorias customizadas por organização.
- Sprint 2: a detecção de duplicidade é uma checagem na aplicação (hash do lançamento), sem constraint única no banco; duas requisições simultâneas idênticas podem passar.
- Sprint 2: sem subabas "Salas e capacidade" e "Custos" do PRD §6.5, sem receitas recorrentes, e a UI de Consultório/Finanças só cria (editar/excluir serviço e política existem apenas na API; lançamentos têm exclusão na UI).
- Sprint 2: faltas não geram taxa de no-show cobrada e não há receita atribuída por serviço; o resumo financeiro não consolida `work_sources` (evita dupla contagem, mas a consolidação de rendas fica para a Sprint 4).
- Sprint 2: a capacidade é a aproximação `floor(min / min esperados)`; slots reais chegam na Sprint 3.
- Sprint 3: feriados estaduais e municipais não são sugeridos (dependem da localidade); só os nacionais por lei, e sempre como "sugeridos" até o usuário confirmar. Carnaval e Corpus Christi (ponto facultativo) ficam de fora.
- Sprint 3: o fuso é o da organização (padrão America/Sao_Paulo), mas não há tela para alterá-lo; a tela de agenda assume esse fuso ao montar horários.
- Sprint 3: sem visão de calendário em grade (dia/semana/mês) nem importação CSV de agenda; a UI mostra listas e tabela dia a dia.
- Sprint 3: o descanso é um parâmetro informado (padrão por plantão + valor por evento); não há regra legal embutida. Plantões noturnos usam a janela fixa 22h–5h.
- Sprint 3: retornos pendentes de meses anteriores ainda não consomem capacidade automaticamente (só os eventos lançados na agenda); a alocação automática fica para o otimizador (Sprint 5).
- Sprint 3: eventos não têm colisão de sala (sem entidade Sala); `schedule_events.linked_return_of` do PRD não foi implementado.
