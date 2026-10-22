# Plano de implementação incremental

Fonte: `docs/PRD.md` §18 (sprints) e §19 (backlog). Regras invioláveis: §23.

## Entregue nesta etapa

| Sprint | Escopo | Evidência |
|---|---|---|
| 0 — Fundamentos | Monorepo, Docker (db/api/web), CI, auth por sessão+CSRF, migrations, `finance_engine` | `pytest`, `alembic up/down/up` em PG16, `next build`, e2e manual em Chromium |
| 1 — Perfis e rendas | Organizações, memberships, RBAC, CRUD de trabalhos com remuneração (US-001), ganho/hora, soft-delete, auditoria | 22 testes de API, incl. cross-tenant e papéis (US-010, US-012) |
| 2 — Consultório e custos | Serviços e políticas de retorno (gratuito/parcial/integral/personalizado, prazo, janela gratuita, faltas), economia por consulta nova, capacidade, agenda de retornos por mês, simulação de preço; lançamentos de receita/despesa com competência x caixa, estornos, detecção de duplicidade e recorrências planejadas (US-002) | Motor: 27 testes (55,2 min; retorno gratuito cai no mês seguinte). API: 54 testes. Migration 0002 up/down/up em PG16. E2E em Chromium |
| 3 — Agenda e capacidade | Disponibilidade (turno semanal), eventos (consulta, retorno, plantão, sobreaviso, CLT…), feriados com confirmação manual, férias, folgas, limites de jornada; conflitos (sobreaviso só com permissão explícita), descanso após plantão, horas ativas x comprometidas sem dupla contagem, slots reais por janela, capacidade de pacientes novos por serviço | Motor: 41 testes. API: 91 testes, também em PostgreSQL real (`TEST_DATABASE_URL`). Migration 0003 up/down/up. E2E em Chromium (achou 2 bugs de UI/CORS, corrigidos) |
| 4 — Metas e dashboard | Meta pessoal líquida/lucro do consultório/faturamento bruto, reserva opcional (desligada por padrão), duplicar e histórico; composição do mês (planos de renda: unidades, certeza garantido/provável, retenção estimada); lacuna auditável (garantido x provável x realizado) com decomposição por fonte e premissas; dashboard com 12 meses, alertas, qualidade dos dados, horas e meta atingida só com restrições duras satisfeitas (US-003) | Motor: 55 testes. API: 121 testes, também em PostgreSQL real. Migration 0004 up/down/up. E2E em Chromium (números do navegador = números dos testes) |
| 5 — Otimizador misto | Pacote `optimizer_engine` (OR-Tools CP-SAT, determinístico, lexicográfico) com 5 objetivos (menos horas, maior margem, menor mudança de rotina, equilibrado com pesos, conservador); atividades inteiras com datas reais (conflitos, descanso, dias fechados, agenda existente), demanda, orçamento, limites de horas/plantões noturnos; validação independente de cada cenário; inviabilidade com máximo alcançável e restrições bloqueadoras (inclusive pares); execuções, cenários e planos salvos persistidos; salvar (snapshot), comparar, aplicar (idempotente, atômico) e desfazer; tela Otimizador (US-004, US-005, US-006) | Motor: 21 testes (casos do PRD §20.2). API: 146 testes, também em PostgreSQL real. Migration 0005 up/down/up. E2E em Chromium |
| 6 — Realizado e importação | Registrar realizado ligado a trabalho/serviço (unidades e valor, duplicidade alertada), marcar planejado como realizado; comparação esperado × obtido por fonte, ritmo do mês e despesas planejadas × realizadas; importação CSV com pré-visualização (extrato bancário ou colunas tipadas, `;`/`,`, formatos BR), duplicatas no banco/arquivo/linha já importada, conciliação com lançamentos planejados, idempotência por hash do arquivo, desfazer; exportação CSV neutralizada contra injeção de fórmula e reimportável (US-007) | Motor CSV: 84 testes. API: 193 testes (SQLite e PostgreSQL real). Migration 0006 up/down/up. E2E em Chromium (achou 2 furos reais: conciliação sem categoria e linhas repetidas entre extratos) |

## Próximas sprints

| Sprint | Dependências | Critério de aceite (PRD) | Principais riscos |
|---|---|---|---|
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
- Sprint 4: a projeção é **por plano** (unidades que você informa por fonte e mês); não há aprendizado automático de sazonalidade nem previsão. Realizado nunca é somado ao projetado: aparecem lado a lado.
- Sprint 4: despesas lançadas são abatidas integralmente e custos variáveis por unidade já estão no líquido de cada fonte; lançar o mesmo custo nos dois lugares o conta duas vezes (premissa exibida no painel, sem detecção automática).
- Sprint 4: metas e rendas são pessoais (nem o dono vê as de outro usuário). Serviços e despesas são da organização inteira, então em clínica com vários médicos os serviços entrariam em todas as metas; atribuição de serviço a médico fica para o plano Clínica.
- Sprint 4: tributos seguem sendo só alíquota estimada por fonte (sem motor fiscal); consultório não tem tributo próprio além da retenção informada no plano do serviço.
- Sprint 4: "Otimizar minha meta" está desabilitado até a Sprint 5; MANAGER e ACCOUNTANT não têm acesso a metas/dashboard pessoais.
- Sprint 4: o gráfico de 12 meses recalcula a projeção de cada mês a cada abertura (12 consultas em lote); sem cache nem snapshot.
- Sprint 5: oportunidades de plantão/sobreaviso (`max_units` e dias possíveis) e demanda de pacientes são **informadas pelo usuário** (PRD §24.7); o app não as prevê nem as busca em integrações. Sem demanda informada, as consultas aparecem como risco ("dependem de captação").
- Sprint 5: o otimizador nunca propõe sobreposição condicional de sobreaviso (só em dias livres, sem sobrepor nada); sobreavisos permitidos pelo usuário na agenda não bloqueiam candidatos.
- Sprint 5: consultas usam a economia da Sprint 4 (retornos no mesmo mês). Retornos que caem no mês seguinte geram aviso de risco, mas não são redistribuídos nem consomem a capacidade de outro mês.
- Sprint 5: capacidade de consulta é por minutos livres e número de horários da agenda (não escolhe horários individuais); plantões e sobreavisos têm data/hora, consultas não.
- Sprint 5: cenário "conservador" aplica um corte único (padrão 20%, ajustável) em ganhos e demanda; não há distribuição de probabilidade. Apenas um solver sequencial (1 worker) com limite de 20 s por etapa; problemas muito grandes não foram medidos.
- Sprint 5: "Aplicar" soma unidades como adicional *provável* (`extra_units`) e, só se pedido, cria eventos PLANEJADOS de plantão/sobreaviso; não há integração para efetivar os plantões. Desfazer subtrai o adicional e cancela os eventos criados; alterações manuais posteriores no plano não são reconciliadas.
- Sprint 5: o objetivo "Maior margem" usa toda a folga de horas informada (sem teto próprio além de `max_block_events`); sem limite de horas, o cenário não tem teto de esforço e isso é sinalizado.
- Sprint 6: o realizado comparado por fonte é **bruto** (o que entrou); o líquido do realizado é uma estimativa com a mesma retenção e os mesmos custos do plano. O número "Realizado até agora" do Início continua sendo receita − despesa lançadas (não líquido de tributo).
- Sprint 6: conciliação é por tipo + competência + valor (+ categoria, se o arquivo a informar); não usa descrição nem aproximação de data/valor. Com vários planejados iguais usa o mais antigo e avisa. Sem leitura de OFX/PDF nem integração bancária.
- Sprint 6: importação só aceita CSV UTF-8 de até 2 MB e 5.000 linhas; o conteúdo vai no corpo JSON (não há upload multipart nem fila assíncrona). Arquivos com sinais ou formatos muito diferentes exigem ajuste manual das colunas (não há tela de mapeamento).
- Sprint 6: desfazer importação exclui os lançamentos que ela criou mesmo que tenham sido editados depois; lançamentos conciliados voltam a planejados.
- Sprint 6: receitas importadas entram sem vínculo a uma fonte (aparecem como "sem vínculo" na comparação); a vinculação é feita editando o lançamento pela API, ainda sem tela.
- Sprint 6: não há aprendizado de sazonalidade a partir do realizado (previsto para a Sprint 8); a comparação é descritiva.
