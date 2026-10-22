# FinanceMed AI — Especificação Completa do Produto (PRD + Blueprint Técnico)

**Versão:** 1.0  
**Data:** 21/10/2026  
**Status:** documento de implementação para Codex  
**Idioma da interface:** português brasileiro (pt-BR)  
**Moeda principal:** BRL (R$)  
**Público inicial:** médicos brasileiros com consultório próprio e/ou múltiplos vínculos profissionais; expansão para clínicas multiprofissionais.

> **Objetivo principal:** responder, com cálculos verificáveis: “Quero receber R$ X líquidos este mês. Considerando consultório, plantões, sobreavisos, outros trabalhos, despesas, impostos, retornos, horas disponíveis e captação, em quais atividades devo mexer para chegar à meta sem comprometer limites de jornada e descanso?”
>
> **Regra arquitetural:** cálculo financeiro e otimização são determinísticos; IA apenas explica resultados, identifica anomalias, formula recomendações fundamentadas em dados e sugere ações. Nunca utilizar resposta do LLM como fonte de números oficiais.

---

## 1. Resumo executivo

O FinanceMed AI é um SaaS de planejamento financeiro e otimização de agenda para médicos. Diferentemente de uma calculadora estática, acompanha resultados reais versus projetados, combina múltiplas fontes de renda, estima demanda e capacidade, projeta sazonalidade e recomenda cenários de aumento de renda ou redução de horas. Deve ser útil no primeiro dia e todos os meses seguintes.

### 1.1 Perguntas que o produto precisa responder

1. Quanto ganho **líquido**, no total e por atividade, após custos e tributos estimados?
2. Qual é minha renda líquida **por hora de trabalho ativo**? E por **hora comprometida** incluindo sobreaviso, deslocamento, intervalos e retornos?
3. Quantas primeiras consultas, retornos, plantões e sobreavisos cabem na agenda?
4. Com uma meta de R$ 50.000 líquidos e projeção atual de R$ 38.000, qual combinação de **2 plantões + 10 consultas + 3 sobreavisos**, ou outra, alcança a meta com menor ocupação de tempo, respeitando disponibilidade?
5. Se aumentar o preço da consulta, quanto muda o resultado considerando hipóteses de demanda e conversão?
6. Como feriados, férias da secretária, faltas, férias do próprio médico e sazonalidade alteram receita e captação?
7. Qual é o principal gargalo do mês: demanda, conversão, agenda, tributos, margem ou custos?
8. Quanto de reserva é necessário para meses com menor receita?
9. Como o resultado efetivo se compara ao planejado, e quais ações funcionaram?
10. Quais canais de aquisição de novos pacientes têm melhor custo por agendamento e retorno financeiro?

### 1.2 Limites do produto

- Não é prontuário eletrônico nem oferece diagnóstico/triagem/decisão clínica.
- Não substitui contabilidade, consultoria tributária, jurídica ou aconselhamento profissional personalizado.
- Não presume que horas extras, plantões ou sobreavisos estejam disponíveis ou sejam seguros; recomendações obedecem a limites informados e restrições contratuais, de saúde e de descanso.
- Não promete captação garantida e não incentiva publicidade médica fora das regras aplicáveis.
- É possível operar inicialmente sem dados identificáveis de pacientes; preferir informações agregadas.

---

## 2. Personas e papéis

| Persona | Necessidade | Principal sucesso |
|---|---|---|
| Médico autônomo / plantonista | Comparar vínculos e metas | Otimização de renda/hora |
| Médico com consultório particular | Calcular preço, retorno, despesas e captação | Lucro líquido e ocupação |
| Médico com múltiplos vínculos CLT/PJ/PF | Consolidar receitas com bases tributárias distintas | Renda líquida consolidada |
| Sócio / gestor de clínica | Analisar profissionais, salas e custos compartilhados | Margem consolidada |
| Secretária / assistente | Registrar agenda e leads sem acesso a dados financeiros privados | Operação eficiente |
| Contador convidado | Revisar premissas tributárias e gerar relatório | Confiabilidade tributária |
| Admin da plataforma | Gerenciar assinatura e suporte sem acesso indevido | Operação segura |

### 2.1 Permissões (RBAC)

- **OWNER:** gerencia organização, cobrança, acessos, dados e configurações.
- **PHYSICIAN:** gerencia trabalhos, metas, disponibilidade e simulações próprias; vê apenas seus dados autorizados.
- **MANAGER:** gerencia custos, agendas e indicadores autorizados da clínica.
- **ASSISTANT:** gerencia registros administrativos e captação; sem visibilidade de metas pessoais, rendimentos ou impostos, salvo autorização granular.
- **ACCOUNTANT:** acesso somente leitura a dados fiscais/financeiros aprovados; pode sugerir parâmetros, alterações precisam de aprovação.
- **PLATFORM_SUPPORT:** dados técnicos e de cobrança, sem acesso padrão a dados privados do cliente.

---

## 3. Escopo e prioridades

### MVP (primeira versão comercial)
- Cadastro/login, organização, perfil e assinatura.
- Trabalho/vínculos com remuneração e jornada.
- Despesas e receita por centro de custo.
- Consultório: duração, valor, taxa de retorno, % de duração, política de cobrança, faltas e capacidade.
- Agenda manual + disponibilidade mensal + feriados locais configuráveis.
- Meta mensal **bruta ou líquida** e composição atual por trabalho.
- **Otimizador misto** com quantidades inteiras de consultas, plantões e sobreavisos, limites e ranking de cenários.
- Motor tributário **paramétrico** explicitamente marcado como estimativa, com PF/PJ separados.
- Dashboard: esperado x realizado, lacuna para meta, resultados por fonte, tempo comprometido.
- Registro de realizados, importação CSV e exportação PDF/CSV.
- Onboarding em menos de 10 minutos com dados de exemplo opcionais.

### V1.1
- Funil de captação, marketing e custo por agendamento; férias da secretária e cobertura.
- Prazos de retorno entre meses; alocação de agenda de retornos pendentes.
- Alertas e relatório semanal/mensal.
- Calendário e feriados por cidade/UF, com confirmação manual.
- Projeção de 3/6/12 meses, cenários de sazonalidade e reserva financeira.

### V1.2
- Camada de IA com explicações auditáveis e perguntas em linguagem natural.
- Integrações com agenda e pagamentos mediante conexão autorizada.
- Testes A/B de preço e marketing (somente análises, sem execução automática).
- Multiusuário e plano clínica aprimorados.

### V2
- Aplicativos iOS/Android em Flutter utilizando a mesma API.
- Integrações ampliadas, automações e previsões com intervalos de incerteza.
- Operações de clínica multiunidade e múltiplas especialidades.

---

## 4. Estrutura global de navegação

**Desktop:** barra lateral fixa; header com mês, organização, busca e alertas.  
**Mobile web:** navegação inferior para Início, Meta, Trabalhos, Agenda e Menu; demais abas no Menu.  
**URL prefix:** `/app` com rotas autenticadas. Exemplo: `/app/dashboard?month=2026-10`.

| Ordem | Aba / rota | Objetivo | Ações/botões principais |
|---|---|---|---|
| 1 | **Início** `/app/dashboard` | KPIs e próximos passos | `Alterar mês`, `Atualizar realizado`, `Criar simulação`, `Ver diagnóstico`, `Exportar relatório` |
| 2 | **Minha Meta** `/app/goals` | Definir meta e lacuna | `Definir meta`, `Duplicar meta`, `Comparar cenários`, `Salvar meta` |
| 3 | **Otimizador Misto** `/app/optimizer` | Encontrar combinações viáveis de trabalhos | `Otimizar`, `Adicionar restrição`, `Comparar`, `Salvar cenário`, `Aplicar ao planejamento`, `Desfazer` |
| 4 | **Trabalhos & Rendas** `/app/work` | Catalogar serviços e vínculos | `Novo trabalho`, `Editar`, `Duplicar`, `Pausar`, `Excluir`, `Registrar recebimento` |
| 5 | **Consultório** `/app/clinic` | Custos, consultas e retornos | `Adicionar serviço`, `Configurar retorno`, `Simular preço`, `Cadastrar sala`, `Definir capacidade` |
| 6 | **Agenda & Capacidade** `/app/schedule` | Jornada, bloqueios e ocupação | `Bloquear dia`, `Adicionar turno`, `Importar agenda`, `Registrar falta`, `Exportar agenda` |
| 7 | **Receitas & Despesas** `/app/finances` | Lançamentos e custos | `Nova receita`, `Nova despesa`, `Importar CSV`, `Conciliar`, `Exportar` |
| 8 | **Tributos PF/PJ** `/app/taxes` | Simular regras fiscais por fonte | `Novo cenário tributário`, `Validar com contador`, `Atualizar alíquotas`, `Comparar PF x PJ` |
| 9 | **Captação** `/app/acquisition` | Leads, conversão e canais | `Registrar lead agregado`, `Nova campanha`, `Registrar gasto`, `Analisar canal`, `Planejar cobertura` |
| 10 | **Previsões** `/app/forecasts` | Sazonalidade e cenário futuro | `Projetar 3/6/12 meses`, `Adicionar férias`, `Simular feriados`, `Criar cenário` |
| 11 | **Consultor IA** `/app/assistant` | Perguntas e recomendações | `Perguntar`, `Explicar números`, `Criar plano de ação`, `Exportar plano` |
| 12 | **Relatórios** `/app/reports` | Compartilhar indicadores | `Gerar PDF`, `Baixar CSV`, `Agendar resumo`, `Compartilhar com contador` |
| 13 | **Equipe** `/app/team` | Convidar pessoas e permissões | `Convidar`, `Editar papel`, `Revogar acesso`, `Ver auditoria` |
| 14 | **Planos & Cobrança** `/app/billing` | Assinatura SaaS | `Alterar plano`, `Atualizar pagamento`, `Ver faturas`, `Cancelar` |
| 15 | **Configurações** `/app/settings` | Organização, LGPD, preferências | `Salvar`, `Exportar dados`, `Excluir conta`, `Ativar 2FA`, `Configurar notificações` |

**Global:** `Ajuda`, `Buscar`, `Notificações`, `Perfil`, `Sair`. Todos os botões devem ter estados de loading, erro, sucesso e autorização. Exclusões críticas pedem confirmação.

---

## 5. Fluxos de ponta a ponta

### 5.1 Onboarding guiado

1. **Boas-vindas** — nome da clínica/uso pessoal, especialidade opcional, estado/cidade, timezone `America/Sao_Paulo`, moeda BRL. Botões `Começar` e `Ver demonstração`.
2. **Tipo de renda** — checkboxes `Consultório`, `Plantões`, `Sobreavisos`, `CLT`, `Telemedicina`, `Procedimentos`, `Perícias/aulas`, `Outro`. Botão `Continuar`.
3. **Primeiro trabalho** — modalidade, nome, valor, frequência, duração, retenções estimadas, custos relacionados. `Adicionar outro`, `Continuar`.
4. **Consultório (condicional)** — consulta inicial, retorno %, janela do retorno, duração relativa, gratuito/pago, despesas de operação. `Salvar e continuar`.
5. **Disponibilidade** — dias trabalháveis, turnos, horas máximas/mês, descansos, deslocamentos e indisponibilidades. `Continuar`.
6. **Meta** — receita bruta x ganho líquido, meta de R$, prazo, prioridade (`Menos horas`, `Mais renda`, `Equilíbrio`). `Calcular meu plano`.
7. **Resultado inicial** — cards `Projeção atual`, `Diferença`, `3 cenários viáveis`, `Premissas faltantes`. CTA `Ativar acompanhamento`.
8. **Consentimentos e privacidade** — termos, política de privacidade e preferências separadas para marketing. `Concluir`.

### 5.2 Fluxo “Esse mês preciso ganhar X: onde eu mexo?”

1. Abrir `Minha Meta` e selecionar competência.
2. Definir objetivo como **líquido disponível para o médico**, e não faturamento da clínica (possibilitar alternância).
3. Conferir renda fixa prevista, receita agendada, despesas, encargos e tributos.
4. Abrir `Otimizador Misto`, selecionar atividades permitidas.
5. Inserir limites: horas adicionais, número de plantões, sobreavisos, consultas, orçamento de marketing, dias indisponíveis, ocupação, descanso, cobertura da secretária e expectativa de demanda.
6. Clicar `Otimizar`.
7. Ver até 5 cenários ordenados, incluindo `Menor tempo comprometido`, `Maior margem`, `Menor alteração de rotina`, `Mais equilibrado` e `Plano conservador`.
8. Clicar num cenário -> ver composição, renda incremental **líquida**, horas ativas e comprometidas, chance/capacidade de demanda, impacto da agenda, custos, premissas e riscos.
9. `Comparar cenários` lado a lado.
10. `Salvar cenário` preserva snapshot das entradas e versão do motor; `Aplicar ao planejamento` cria metas de agenda **sem registrar trabalho como executado nem lançar receitas recebidas**.
11. Após execução, registrar resultados reais. Tela compara esperado x obtido e aprende sazonalidade de forma estatística, mantendo parâmetros explícitos.

### 5.3 Fluxo captação

1. Selecionar mês/canal (Google, indicação, Instagram, site, retorno espontâneo, outros).
2. Informar novos contatos, respostas no prazo, agendamentos, comparecimento, custo e origem.
3. Registrar férias/ausência da secretaria e cobertura.
4. Exibir gargalo do funil; recomendar melhorias operacionais e canais permitidos.
5. Comparar captação necessária com capacidade real e receita potencial — evitar recomendar captação se a agenda estiver cheia.

### 5.4 Fluxo fechamento mensal

1. Importar/lançar receitas, despesas, consultas realizadas, retornos realizados e horas trabalhadas.
2. Conciliar eventos, identificar duplicidade e validar ausência de registros.
3. Gerar relatório do mês: previsto/realizado, variação, resultados por atividade, custo por paciente novo, compromissos futuros.
4. Sugerir meta e cenário do mês seguinte.

---

## 6. Especificação de telas (campos, componentes, botões, validações)

### 6.1 Início / Dashboard

**Filtros:** organização, médico (se permitido), competência, cenário (base/otimista/conservador).  
**Cards:** meta líquida, projeção líquida, realizado até agora, diferença, horas comprometidas, receita por fonte, próximos recebimentos, despesas, falta de pacientes/plantões, captação, alerta de gargalo.  
**Gráficos:** evolução 12 meses, pizza de fontes de renda, lucro/hora por atividade, previsto x realizado, capacidade de agenda.  
**CTA principal:** `Otimizar minha meta`.  
**Estados:** sem dados -> wizard curto; dados parciais -> informar hipótese faltante; valores negativos -> mostrar déficit sem esconder sinal.

### 6.2 Minha Meta

**Campos:** competência, meta tipo (`Líquida pessoal`, `Lucro do consultório`, `Faturamento bruto`), valor desejado, renda garantida, renda provável, reservas que podem ser utilizadas (off por padrão), preferência de esforço.  
**Botões:** `Salvar meta`, `Calcular diferença`, `Enviar ao otimizador`, `Histórico de metas`.  
**Resultado:** quanto falta, data estimada, percentual já assegurado vs provável, alertas de dados insuficientes.

### 6.3 Otimizador Misto

**Configurações editáveis:**
- Consulta: ganho incremental líquido, duração inicial, retorno esperado, atraso do retorno, taxa de falta, capacidade restante, demanda provável, limite de consultas novas.
- Plantão: valor líquido, duração, escala existente, número máximo adicional, descanso necessário após plantão, deslocamento, disponibilidade real.
- Sobreaviso: remuneração fixa, duração de disponibilidade, probabilidade/horas de acionamento, conflitos com atendimento presencial.
- Telemedicina/procedimentos/CLT-extra/outro: rendimento, custo e tempo por unidade.
- Limite adicional de horas ativas e de horas comprometidas; tempo mínimo de descanso; dias bloqueados; limites de jornada configuráveis.
- Meta: valor, mês, tipo bruto/líquido; função objetivo: `Menos horas`, `Mais lucro`, `Menor esforço de mudança`, `Equilíbrio`.

**Botões:** `Adicionar atividade`, `Otimizar`, `Limpar filtros`, `Comparar cenários`, `Salvar`, `Aplicar ao planejamento`, `Exportar PDF`, `Por que esse cenário?`.  
**Resultados:** cards de 3–5 cenários, decomposição de unidades por atividade, renda adicional, folga financeira, custos, horas ativas/comprometidas, demanda, restrições ativas, riscos, meses afetados por retornos.  
**Sem solução:** informar **quais restrições impedem o objetivo** e o valor máximo alcançável; nunca inventar cenário.

### 6.4 Trabalhos & Rendas

**Campos comuns:** nome do trabalho, tipo, fonte pagadora (nome opcional), regime (`PF`, `PJ`, `CLT`, `Outro`), moeda, valor bruto, periodicidade (`por evento`, `semanal`, `mensal`, `por consulta`), duração, horas ativas, horas de disponibilidade, deslocamento, alimentação, custos variáveis, impostos/retenções, data de recebimento, frequência, disponibilidade, observações.  
**Campos específicos:**
- Plantão: horas (6/12/24/custom), noturno/diurno, adicionais, intervalo, recuperação/descanso.
- Sobreaviso: horas de disponibilidade, valor fixo, acionamento médio, deslocamento potencial, pagamento extra por acionamento.
- CLT: salário fixo, descontos estimados, benefícios separados, jornada e vínculo.
- Consultas/procedimentos: duração, preço, custo unitário, retorno e no-show.

**Botões:** `Novo`, `Duplicar`, `Editar`, `Arquivar`, `Excluir`, `Importar`, `Registrar realizado`.  
**Validação:** renda não pode ser contada duas vezes em `consultório` e `trabalhos`; cada registro aponta a um centro de receita.

### 6.5 Consultório

**Subabas:** `Serviços`, `Retornos`, `Custos`, `Salas e capacidade`, `Simulação de preço`.  
**Campos:** serviço, preço bruto, duração, forma de pagamento, taxas, custo variável, taxa de retornos, tempo do retorno (%) ou minutos, preço retorno (gratuito / parcial / integral / personalizado), retorno dentro de X dias, política comercial, estimativa de absenteísmo, horários, sala, capacidade e equipe.  
**Botões:** `Adicionar serviço`, `Calcular capacidade`, `Simular preço`, `Salvar política`, `Comparar antes/depois`.

### 6.6 Agenda & Capacidade

**Views:** dia/semana/mês; calendário de disponibilidade por médico e sala.  
**Eventos:** consulta inicial, retorno, plantão, sobreaviso, CLT, telemedicina, deslocamento, pausa, férias, feriado e bloqueio.  
**Botões:** `Adicionar evento`, `Bloquear`, `Duplicar turno`, `Importar CSV`, `Vincular calendário` (futuro), `Registrar comparecimento`, `Marcar remarcação`.  
**Regra:** calcular colisões de atividades e descansos; sobreaviso só pode coincidir com outra atividade se explicitamente permitido e condicional ao acionamento.

### 6.7 Receitas & Despesas

**Campos de lançamento:** competência, vencimento, pagamento/recebimento, categoria, centro de custo, vínculo de trabalho, descrição, bruto/líquido, impostos estimados, fixo/variável, recorrência, forma de pagamento, anexo opcional.  
**Categorias de despesa:** aluguel, condomínio, água/luz/internet, secretaria e encargos, papelaria, café/água, limpeza, material, software, equipamentos, manutenção, marketing, contabilidade, taxas de cartão, impostos, deslocamento, treinamento, outros.  
**Botões:** `Nova despesa`, `Nova receita`, `Recorrência`, `Importar CSV`, `Revisar duplicados`, `Exportar`, `Conciliar`.  
**Regra:** separar resultado por competência de fluxo de caixa por data de pagamento.

### 6.8 Tributos PF/PJ

**Subabas:** `Cenários`, `Por fonte de renda`, `Histórico`, `Parâmetros`.  
**MVP:** percentual efetivo manual, valor fixo manual, descontos/encargos informados, parâmetros por trabalho, validade por período, fonte da premissa e selo `Estimativa não validada`.  
**Futuro:** motor fiscal versionado e revisado por contador para regras aplicáveis (INSS, IRPF, ISS, Simples Nacional, fator R, pró-labore etc.), sem supor que todas são devidas em cada caso.  
**Botões:** `Adicionar parâmetro`, `Duplicar cenário`, `Solicitar revisão contábil`, `Exportar hipóteses`.  
**Obrigatório:** nunca calcular regime fiscal a partir de um único toggle PF/PJ como se fosse uma cotação definitiva.

### 6.9 Captação

**Campos:** mês, canal, contatos/leads agregados, tempo de primeira resposta, agendamentos, consultas realizadas, investimento, qualidade do lead (indicador não clínico), capacidade livre, férias da secretaria, cobertura, variação sazonal, dados históricos.  
**KPIs:** lead -> agendamento, agendamento -> comparecimento, custo por agendamento, custo por comparecimento, receita atribuída com atribuição transparente.  
**Botões:** `Adicionar campanha`, `Registrar resultados`, `Planejar férias`, `Gerar recomendações`, `Comparar canais`.  
**Limites:** nada de compra de listas ou mensagens sem base legal; respeitar opt-outs e regras de publicidade médica.

### 6.10 Previsões

**Componentes:** horizonte 3/6/12 meses, cenário base/otimista/conservador, fatores de feriados, férias, sazonalidade e inflação de custos; gráfico de faixa de cenários; lista de principais drivers.  
**Botões:** `Projetar`, `Salvar hipótese`, `Adicionar evento`, `Exportar plano`.  
**Regra:** resultados mostram incerteza; não prometer previsões garantidas. Sem histórico, usar hipóteses explícitas, não “IA inventada”.

### 6.11 Consultor IA

**Perguntas sugeridas:**
- “Esse mês preciso ganhar R$ 50 mil; onde mexo?”
- “É melhor um plantão ou 8 consultas a mais?”
- “Se a secretária tirar 15 dias de férias, o que acontece?”
- “Qual é meu trabalho com maior renda por hora comprometida?”
- “Quanto perco com retornos gratuitos?”
- “O que mudou de setembro para outubro?”

**Resposta padronizada:** `Resumo`, `Dados utilizados`, `Cenários`, `Restrições`, `Riscos e incertezas`, `Ações sugeridas`, `Link para simulação`.  
**Botões:** `Refazer com outra meta`, `Ver cálculo`, `Salvar recomendação`, `Abrir cenário`, `Copiar`.

### 6.12 Relatórios

**Templates:** `Resultado mensal`, `Despesas`, `Renda por vínculo`, `Otimização`, `Captação`, `Contador`, `Ano`.  
**Formatos:** PDF, CSV; considerar XLSX na V1.1.  
**Botões:** `Gerar`, `Pré-visualizar`, `Baixar`, `Agendar envio`, `Compartilhar acesso temporário`.

### 6.13 Equipe, Billing e Configurações

**Equipe:** convite por e-mail, permissões granularizadas, revogação imediata, logs de acesso.  
**Billing:** planos gratuito/pro/IA/clínica; status, invoices, upgrade/downgrade, cancelamento e exportação.  
**Configurações:** perfil, especialidade opcional, região/feriados, segurança, 2FA, consentimentos, preferências, retenção, download de dados, exclusão.  
**Botões:** presentes em cada tela conforme tabela da seção 4; nunca esconder cancelamento ou exportação.

---

## 7. Modelo financeiro: definições rigorosas

### 7.1 Definições

- **Receita bruta:** valores faturados, antes de tributos/descontos.
- **Receita líquida operacional:** receita bruta menos tributos sobre a receita, taxas e custos atribuíveis; não confundir com rendimento pessoal.
- **Lucro do consultório:** receitas operacionais menos custos/despesas/tributos pertinentes; respeitar provisões e competência.
- **Ganho líquido pessoal:** disponibilidade estimada ao médico após tributos pessoais/empresariais pertinentes, custos e demais descontos; separar pró-labore, distribuição e salário para evitar dupla contagem.
- **MRR do SaaS:** receita recorrente mensal da plataforma, **não** do consultório cliente.
- **Hora ativa:** tempo em atividade efetiva; **hora comprometida:** período reservado, incluindo plantão/sobreaviso e deslocamentos conforme configuração.
- **Receita assegurada:** contrato/atendimento já confirmado com nível de certeza configurado; **projetada:** expectativa, não dinheiro recebido.

### 7.2 Consultas e retornos

Para `N` primeiras consultas em determinado período:

- `retornos_esperados = N × taxa_retorno` (com `taxa_retorno` entre 0 e 1, por episódio definido).
- `minutos_esperados_por_paciente_novo = minutos_consulta + taxa_retorno × minutos_retorno`, **quando retornos acontecem no mesmo horizonte analisado**.
- `receita_bruta_esperada_por_paciente_novo = preco_consulta + taxa_retorno × preco_retorno`.
- `capacidade_novos = floor(minutos_disponiveis / minutos_esperados_por_paciente_novo)` como **aproximação**; motor de calendário deverá contar slots e retornos em suas datas reais.
- Se retorno ocorrer no mês seguinte, o consumo de agenda deve ser lançado no mês seguinte; a receita da consulta original permanece no período definido pela política de competência.
- Taxa de retorno **não** é percentual de todos os atendimentos; e não pressupõe sempre exatamente um retorno por paciente. Suportar número de retornos por episódio no futuro.
- Faltas: separar `no_show_novas`, `no_show_retornos`, cancelamento prévio, remarcação e eventual taxa cobrada, evitando descontos duplos.

**Exemplo:** consulta 40 min; retorno 20 min; 76% dos pacientes novos retornam -> tempo esperado = 55,2 min por paciente novo, quando no mesmo período e sem outras pausas.

### 7.3 Agregação de receitas por atividade

Para cada atividade `i`:

`incremento_liquido_i(q_i) = receita_bruta_i(q_i) − custos_variaveis_i(q_i) − tributos_marginais_estimados_i(q_i) − despesas_incrementais_i(q_i)`

- Incluir efeitos não lineares quando uma decisão muda faixa tributária, custo de secretaria/infraestrutura ou preço.
- Não tratar impostos sempre como percentual fixo; no MVP, usar aproximação declarada como estimativa.
- Separar valor por evento, valor por hora, salário fixo e pagamento de horas adicionais.
- Plantão e sobreaviso não são intercambiáveis: sobreaviso possui horas comprometidas e acionamentos incertos.
- Não contar salários fixos como oportunidade incremental a cada nova hora: incremento apenas se o contrato permitir trabalho adicional remunerado.

### 7.4 Cálculo de lacuna e metas

`lacuna = max(0, meta_liquida − liquido_projetado_base)`

`liquido_projetado_base = receitas_liquidas_previstas − despesas_nao_alocadas − provisoes_pertinentes`

**Meta alcançada** se `liquido_total_estimado >= meta` **e** restrições duras forem satisfeitas. Meta de faturamento é calculada separadamente da meta de ganho pessoal.

### 7.5 Tributação

- Cada fonte pode ter `regime`, `estimador`, `alíquota efetiva/manual`, `retenções` e `período de validade` próprios.
- CLT, PF e PJ podem coexistir. Consolidação pessoal requer prevenção de dupla contagem de tributos, pró-labore e distribuições.
- Mostrar `simulação tributária informativa` até existir motor validado e revisão especializada.
- Tributação brasileira e tabelas mudam; armazenar versão dos parâmetros e data de vigência.

---

## 8. Otimização mista: especificação matemática e funcional

### 8.1 Variáveis de decisão

Para cada atividade opcional `i`, definir variável inteira `x_i >= 0`: número **adicional** de consultas, plantões, sobreavisos, procedimentos ou sessões.

Parâmetros por atividade:

- `net_gain_i`: ganho incremental líquido estimado por unidade (R$).
- `active_minutes_i`: minutos de trabalho efetivo esperados.
- `committed_minutes_i`: minutos de disponibilidade ocupada.
- `recovery_minutes_i`: descanso/bloqueio adicional exigido.
- `travel_minutes_i`: deslocamento.
- `max_units_i`: limite de oportunidades reais disponíveis.
- `demand_limit_i`: número de consultas com demanda plausível, quando aplicável.
- `slots_i`: horários/turnos viáveis.
- `uncertainty_i`: faixa mínima/central/máxima da remuneração ou comparecimento.

### 8.2 Restrições duras

1. `sum(net_gain_i * x_i) >= lacuna`, na versão linear do modelo.
2. `sum(active_minutes_i * x_i) <= max_active_minutes`.
3. `sum(committed_minutes_i * x_i) <= max_committed_minutes`, sem dupla contagem de sobreposições explicitamente permitidas.
4. `0 <= x_i <= max_units_i`; variável inteira.
5. Respeitar janelas da agenda, bloqueios, horários de consultas, sala disponível e indisponibilidades.
6. Descanso mínimo informado após jornadas, e quaisquer limites legais/contratuais aplicáveis; jamais contorná-los para cumprir meta.
7. Demanda: `consultas_adicionais <= min(slots_disponiveis, demanda_estimativa)`.
8. Orçamento opcional: `sum(custo_aquisicao_i * x_i) <= budget_marketing`, quando houver.
9. Retornos projetados ocupam janelas futuras e não devem criar overbooking.
10. Conflitos por duas unidades no mesmo período tornam solução inviável, mesmo se soma de horas for possível.

**Sobreaviso:** usar duas dimensões distintas de tempo (comprometido e ativo provável), e cenários de acionamento; quando risco de conflito for alto, impedir agenda sobreposta.

### 8.3 Funções objetivo

1. **Menos horas:** minimizar minutos comprometidos, com penalidade por deslocamento/fragmentação, sujeito à meta.
2. **Maior margem:** maximizar excedente líquido dentro das restrições, evitando recomendar horas desnecessárias; impor preferência por número razoável de eventos.
3. **Menor alteração da rotina:** minimizar desvios da agenda habitual, número de dias novos e fragmentação.
4. **Equilíbrio:** pontuação normalizada entre renda, tempo, descanso, risco e alteração de rotina; pesos configuráveis e explicados.
5. **Conservador:** usar ganhos pessimistas plausíveis e demanda conservadora, apresentando faixa de resultados.

**Solver:** para MVP com poucos trabalhos, enumeração limitada/pruning ou `OR-Tools CP-SAT` (inteiros e slots); para versões maiores, CP-SAT/MILP. Trabalhar sempre em **centavos inteiros** para dinheiro, e **minutos inteiros** para tempo. Versão de solver e parâmetros registradas em cada execução.

### 8.4 Estratégia de solução

1. Calcular baseline e lacuna.
2. Filtrar trabalhos desabilitados, indisponíveis, com contribuição incremental <= 0 ou sem dados.
3. Gerar oportunidades discretas na agenda com remuneração, duração e restrições.
4. Rodar solver para 4–5 funções objetivo (ou gerar fronteira de Pareto limitada).
5. Validar cada solução por uma segunda rotina independente (dinheiro, horário, descanso, demanda).
6. Arredondar apenas na exibição; cálculo interno em centavos/minutos.
7. Mostrar combinação e justificativa; se inviável, calcular maior renda possível e mostrar restrição bloqueadora.
8. Persistir cenário como snapshot e não alterar agenda efetiva sem confirmação.

### 8.5 Exemplo de cenário misto

**Fictício:** meta R$ 50.000 líquidos; projeção R$ 38.000; lacuna R$ 12.000.

| Incremento | Unidades | Ganho/unidade | Ganho total | Tempo comprometido/unidade |
|---|---:|---:|---:|---:|
| Plantões | 2 | R$ 2.000 | R$ 4.000 | 12h |
| Consultas | 10 | R$ 300 | R$ 3.000 | 1h, incluindo retorno esperado |
| Sobreavisos | 3 | R$ 1.000 | R$ 3.000 | 12h |
| **Total** | | | **R$ 10.000** | **70h comprometidas** |

**Atenção:** essa combinação **não atinge** lacuna de R$ 12.000. A UI precisa mostrar **R$ 2.000 faltantes**, jamais declarar meta cumprida. Pode sugerir alternativas adicionais se houver disponibilidade e descanso; por exemplo, `+1 plantão` renderia R$ 2.000 se válido, levando a 82h comprometidas; ou outras combinações com menos tempo e segurança adequada.

### 8.6 Casos extremos

- Receita marginal negativa -> não recomendar.
- Sem disponibilidade real -> cenário inviável.
- Férias/feriados reduzem slots de fato, não somente um percentual arbitrário.
- Sobreaviso pode conflitar com plantão ou consulta mesmo sem acionamento certo.
- Retorno de consulta de mês anterior consome capacidade atual, mas não gera nova receita se gratuito.
- Sem demanda para 10 consultas extras -> não propor 10 como garantidas; oferecer plano de captação condicionado.
- Meta já alcançada -> recomendar estabilidade/reserva em vez de incentivar mais trabalho.
- Se existir múltipla solução ótima -> ordenação consistente e deduplicação.

---

## 9. Previsão, captação, férias e sazonalidade

### 9.1 Modelo inicial

`leads_validos = leads_brutos × fator_cobertura_secretaria × fator_sazonalidade`  
`agendamentos_esperados = leads_validos × taxa_conversao`  
`consultas_realizadas = min(agendamentos_esperados, slots_livres) × taxa_comparecimento`

Esses fatores devem ser calibrados com histórico e acompanhados de intervalos; perdas por férias e feriados não são universais. Cobertura por secretária substituta ou automação pode reduzir efeitos de ausência.

### 9.2 Recomendações permitidas

- Ajustar horários com base em demanda real.
- Aumentar conversão via tempo de resposta e facilidades de agendamento.
- Planejar cobertura de férias e feriados.
- Avaliar custo por canal e campanhas informativas compatíveis com publicidade médica.
- Melhorar cadastro comercial, site e perfil profissional em diretórios pertinentes.
- Nunca sugerir práticas antiéticas, compra de pacientes/listas, publicidade enganosa ou envio não autorizado de mensagens.

### 9.3 Projeções

- Sem histórico: três cenários manuais `conservador`, `base`, `otimista`.
- Com histórico: médias móveis e sazonalidade simples, excluindo meses incompletos; depois modelos mais robustos, com validação.
- Sinalizar fontes de incerteza: preço, demanda, faltas, retorno, tributos, sazonalidade, acionamento de sobreaviso.

---

## 10. IA: design de ferramentas e guardrails

### 10.1 Abordagem

IA não faz aritmética livre nem acessa tabelas de usuários diretamente. O backend expõe ferramentas autorizadas:

- `get_financial_snapshot(month, scope)`
- `get_goal_gap(month, goal_id)`
- `run_optimizer(parameters)`
- `compare_scenarios(ids)`
- `get_acquisition_funnel(month)`
- `get_forecast(month_range)`
- `explain_calculation(reference_id)`

As ferramentas entregam resultados calculados pelo backend com metadados de proveniência. Exibir fonte, período e premissas em todas as respostas financeiras.

### 10.2 Formato de resposta IA

```json
{
  "summary": "Faltam R$ 12.000 líquidos para a meta.",
  "period": "2026-10",
  "data_quality": "partial",
  "assumptions": ["Tributação PJ estimada manualmente"],
  "scenario_ids": ["uuid-1", "uuid-2"],
  "risks": ["Consulta adicional depende de captação"],
  "actions": [{"title": "Rever capacidade", "route": "/app/schedule"}],
  "disclaimer": "Simulação gerencial, não consultoria tributária."
}
```

### 10.3 Segurança da IA

- Não usar instruções presentes em anexos, notas ou descrições de lançamentos para comandar o assistente (proteção contra prompt injection).
- Isolar organizações; todos os tools filtrados por tenant/RBAC.
- Não enviar dados identificáveis de pacientes por padrão.
- Logs de acesso, minimização de dados, possibilidade de desabilitar IA.
- Não permitir mutações financeiras/agenda por IA sem confirmação explícita.
- Evitar respostas que incentivem sobrecarga de trabalho ou contrariem limites de descanso.

---

## 11. Modelo de dados (PostgreSQL)

Todas as tabelas de negócio incluem `id UUID`, `organization_id UUID`, `created_at`, `updated_at`; registros críticos também `created_by` e `deleted_at` para soft-delete quando necessário. Valores monetários como `BIGINT` em centavos, percentuais como `NUMERIC`, timestamps em UTC e timezone por usuário/organização.

| Tabela | Campos principais | Relações |
|---|---|---|
| `organizations` | name, legal_type, timezone, currency, location_uf, location_city | raiz multi-tenant |
| `users` | email, name, auth_provider, mfa_enabled | perfil |
| `memberships` | user_id, organization_id, role, permissions_json | RBAC |
| `subscriptions` | plan, state, provider_customer_id, next_billing_at | organização |
| `work_sources` | owner_user_id, name, work_type, taxation_profile_id, status | modalidades |
| `income_rules` | work_source_id, gross_cents, unit_type, duration_minutes, commitment_minutes, travel_minutes, variable_cost_cents, expected_calls | parâmetros por trabalho |
| `clinic_services` | name, price_cents, duration_minutes, cost_cents, return_policy_id, no_show_rate | consultório |
| `return_policies` | occurrence_rate, delay_days, duration_minutes, price_cents, no_show_rate | consultas |
| `expense_categories` | name, parent_id, is_system | categorias |
| `transactions` | date, competence_month, due_date, paid_at, amount_cents, type, category_id, work_source_id, status, dedupe_hash | receitas/despesas |
| `recurrences` | frequency, next_at, stop_at, amount_cents | lançamentos futuros |
| `tax_profiles` | regime, model, effective_rate, fixed_cents, effective_from, effective_to, reviewed_by, reviewed_at, notes | simulações fiscais |
| `availability_slots` | user_id, starts_at, ends_at, availability_type, location_id | agenda |
| `schedule_events` | source_id, type, starts_at, ends_at, status, duration_minutes, linked_return_of | trabalho/consulta |
| `time_constraints` | max_active_minutes, max_committed_minutes, min_rest_minutes, max_night_shifts, days_off | por período |
| `holidays` | date, scope, name, location | feriados |
| `leaves` | user_id, staff_role, start_at, end_at, coverage_factor | férias/licenças |
| `monthly_goals` | month, metric_type, target_cents, priority | metas |
| `optimizer_runs` | goal_id, engine_version, params_json, outcome, runtime_ms, created_by | auditoria |
| `optimizer_scenarios` | run_id, ranking, objective, output_json, feasibility_status, warnings_json | cenários |
| `saved_plans` | scenario_id, month, status, applied_at, snapshot_json | planejamento |
| `acquisition_channels` | name, channel_type | funil |
| `acquisition_metrics` | month, channel_id, leads_count, responses_count, bookings_count, attended_count, spend_cents | mensuração |
| `forecasts` | period_start, period_end, version, assumptions_json, result_json | projeção |
| `ai_conversations` | owner_user_id, summary, created_at | consultor IA |
| `ai_messages` | conversation_id, role, content_redacted, tool_calls_json | histórico IA |
| `notifications` | user_id, kind, body, read_at, channel | alertas |
| `audit_logs` | actor, action, object_type, object_id, timestamp, ip_hash, metadata_json | segurança |
| `import_jobs` | filename, type, status, row_count, errors_json | importação |
| `report_jobs` | type, period, state, output_path, expires_at | relatórios |

**Índices:** em `(organization_id, competence_month)`, `(organization_id, starts_at)`, `(organization_id, work_source_id)` e auditoria.  
**Isolamento:** toda consulta deve impor `organization_id` e autorização; RLS do PostgreSQL é defesa adicional, não substitui o filtro no backend.  
**Sem PII de pacientes no MVP:** consultas como eventos agregados/identificadores pseudônimos, sem CPF, nome, diagnóstico ou prontuário.

---

## 12. API HTTP (FastAPI)

Autenticação por sessão segura/OIDC, cookies httpOnly e CSRF conforme arquitetura. OpenAPI gerado pelo FastAPI. Paginação, filtragem, autorização e erros consistentes.

| Método | Rota | Finalidade |
|---|---|---|
| `POST` | `/api/v1/auth/register` | conta |
| `POST` | `/api/v1/auth/login` | sessão |
| `POST` | `/api/v1/auth/logout` | encerrar sessão |
| `GET/PATCH` | `/api/v1/me` | perfil |
| `GET/POST` | `/api/v1/organizations` | organização |
| `GET/POST/PATCH/DELETE` | `/api/v1/work-sources` | rendas |
| `GET/POST/PATCH/DELETE` | `/api/v1/clinic-services` | serviços |
| `GET/POST/PATCH/DELETE` | `/api/v1/return-policies` | retornos |
| `GET/POST/PATCH/DELETE` | `/api/v1/transactions` | financeiro |
| `GET/POST/PATCH/DELETE` | `/api/v1/schedule/events` | agenda |
| `GET/POST/PATCH/DELETE` | `/api/v1/availability` | turnos |
| `GET/POST/PATCH/DELETE` | `/api/v1/goals` | metas |
| `GET/POST/PATCH/DELETE` | `/api/v1/tax-profiles` | tributos |
| `POST` | `/api/v1/optimizer/runs` | otimizar |
| `GET` | `/api/v1/optimizer/runs/{id}` | resultados |
| `POST` | `/api/v1/optimizer/scenarios/{id}/save` | salvar |
| `POST` | `/api/v1/optimizer/scenarios/{id}/apply` | aplicar planejamento |
| `GET` | `/api/v1/dashboard?month=YYYY-MM` | painel |
| `GET/POST` | `/api/v1/acquisition/metrics` | funil |
| `POST` | `/api/v1/forecasts/run` | previsão |
| `POST` | `/api/v1/assistant/chat` | perguntas |
| `GET` | `/api/v1/reports` | relatórios disponíveis |
| `POST` | `/api/v1/reports/generate` | gerar arquivo |
| `POST` | `/api/v1/imports` | CSV |
| `GET/POST` | `/api/v1/team/invitations` | convites |
| `GET/PATCH` | `/api/v1/billing/subscription` | assinatura |
| `GET` | `/api/v1/audit-logs` | auditoria autorizada |
| `GET` | `/api/v1/export` | portabilidade |
| `DELETE` | `/api/v1/account` | solicitação exclusão |

**Payload-chave do otimizador:**

```json
{
  "month": "2026-10",
  "goal_type": "personal_net",
  "target_cents": 5000000,
  "baseline_cents": 3800000,
  "max_additional_active_minutes": 2400,
  "max_additional_committed_minutes": 4800,
  "objective": "balanced",
  "activities": [
    {"source_id": "uuid-consult", "max_units": 30},
    {"source_id": "uuid-shift", "max_units": 4},
    {"source_id": "uuid-oncall", "max_units": 3}
  ],
  "scenario_type": "base"
}
```

**Resposta esperada:** `run_id`, `feasible`, `baseline_cents`, `gap_cents`, `scenarios[]` (com unidades, receita incremental, horas, restrições, incertezas), `blocking_constraints[]`, `assumptions[]`, `engine_version`. Validar no backend `target_cents >= 0`, datas consistentes, tenant e ownership dos `source_id`.

---

## 13. Arquitetura técnica e repositório

**Stack recomendada:**
- Web/PWA: **Next.js + TypeScript + Tailwind + componentes acessíveis**.
- Backend: **Python + FastAPI + Pydantic**.
- Motor financeiro: biblioteca Python separada, pura/testável, em centavos, sem dependência de LLM.
- Otimização: **OR-Tools CP-SAT**, com testes de validação independentes.
- Banco: **PostgreSQL**, migrations com Alembic.
- Jobs: worker/background queue conforme necessidade (Celery/RQ + Redis ou alternativa gerenciada).
- Testes: Pytest, Playwright, testes unitários TypeScript e testes de contrato API.
- Observabilidade: logs estruturados, métricas, tracing, monitoramento de erro.
- Infra: containers, CI/CD, HTTPS, secrets manager, backups automatizados, ambientes dev/staging/prod.
- Mobile V2: **Flutter/Dart**, usando APIs existentes.

### 13.1 Estrutura sugerida

```text
financemed-ai/
  README.md
  docs/
    PRD.md
    architecture.md
    api.md
    formulas.md
    privacy.md
    adr/
  apps/
    web/                 # Next.js
    mobile/              # Flutter (V2)
  services/
    api/                 # FastAPI
      app/
        modules/
          auth/
          organizations/
          work/
          clinic/
          schedule/
          finances/
          taxes/
          goals/
          optimizer/
          acquisition/
          forecasts/
          assistant/
          reports/
          billing/
        core/
        db/
      alembic/
      tests/
    worker/
  packages/
    finance_engine/      # cálculos puros
    optimizer_engine/    # solver
    contracts/           # OpenAPI, schemas, exemplos
  infra/
    docker/
    terraform/
  scripts/
  .github/workflows/
  docker-compose.yml
  .env.example
```

### 13.2 Diretrizes de engenharia

- Git branches curtas, revisão e CI obrigatório antes de merge.
- OpenAPI como contrato, cliente TS gerado quando apropriado.
- Sem credenciais no repositório; `.env.example` contém apenas placeholders.
- Dependências travadas por lockfile; testes automatizados e scans de segurança.
- UTC no banco, renderizar horário no timezone da organização.
- Multitenancy verificado em **todos** os endpoints e queries.
- Nunca arredondar dinheiro com float binário; inteiros em centavos/Decimal.
- Feature flags para IA e integrações.
- Idempotência para criação de cobranças, importações e aplicação de cenários.

---

## 14. Design system e experiência

- Visual: profissional, limpo, confiança financeira; evitar estética infantil.
- Layout: sidebar desktop, header com competência e organização, cards KPI, tabelas e gráficos com legendas.
- Mobile: bottom navigation com 5 ações-chave, formulários em etapas, botões grandes, sem tabelas largas sem alternativa em cards.
- Paleta proposta: azul profundo/cinza neutro + verde discreto para status positivo e âmbar para avisos; nunca usar cor como único sinal.
- Tipografia com suporte a números tabulares; moeda pt-BR; unidades h/min; formatos de data locais.
- Acessibilidade: WCAG 2.2 AA como objetivo, teclado, foco visível, contraste, labels, leitores de tela, estados de erro descritivos.
- Estados obrigatórios: loading, empty, error, partial data, permission denied, success, offline parcial.
- Microcopy: distinguir claramente `projetado`, `realizado`, `garantido`, `possível`, `bruto` e `líquido`.
- Salvamento explícito; `Aplicar cenário` nunca executa cobrança, contato com pacientes ou marcação automática sem confirmação.

---

## 15. Notificações, relatórios e retenção

**Eventos elegíveis (opt-in):**
- Meta semanal abaixo do ritmo planejado.
- Conflito de agenda ou descanso.
- Férias da secretaria sem cobertura cadastrada.
- Despesas acima de orçamento.
- Queda de conversão e capacidade ociosa.
- Relatório mensal pronto.
- Assinatura em risco de falha de pagamento.

**Regra anti-spam:** limites de frequência, preferências por canal, horários silenciosos e opção de desligar. Não enviar dados financeiros sensíveis no corpo de push/e-mail sem justificativa; preferir aviso com link autenticado.

**Métricas de produto:** ativação em 7 dias; tempo para primeira simulação; % com ≥2 trabalhos cadastrados; % metas revisitadas mensalmente; retenção 30/90 dias; churn logo após primeira simulação; adoção de integração/importação; taxa de cenários salvos/aplicados; MRR, ARPA, LTV, CAC; falhas de cálculo e solicitações de suporte.

---

## 16. Segurança, privacidade e conformidade

- LGPD: definir controlador/operador segundo relações concretas; base legal por finalidade; transparência, minimização, direitos dos titulares, segurança e políticas de retenção.
- Preferir dados agregados em vez de dados de pacientes; não coletar CPF, diagnóstico, prontuário ou nome de paciente no MVP.
- Criptografia em trânsito e repouso; HTTPS; MFA/2FA; RBAC; auditoria; rate limiting; backup testado; rotação de segredos; isolamento tenant.
- Exportação e exclusão de conta mediante processo verificável, ressalvadas obrigações legais de retenção.
- Dados bancários/fiscais e rendimentos são confidenciais, mesmo quando não enquadrados como dados sensíveis de saúde.
- Logs não devem registrar senhas, dados bancários completos ou conteúdo de anexos por padrão.
- IA: proteção contra injeção de prompt e exfiltração; provedores e transferências internacionais avaliados e contratados adequadamente.
- Publicidade médica: recomendações de captação devem ser submetidas às regras do CFM vigentes.
- Tributação: revisão contábil antes de oferecer cálculos automáticos oficiais. Incluir disclaimers proporcionais, sem ocultar limitações.
- Integrações futuras de WhatsApp e pagamentos somente via APIs oficiais/contratos permitidos e consentimentos/autorizações apropriadas.

---

## 17. Modelo de assinatura e monetização

Valores **hipotéticos para validação**, sem alegação de pesquisa de mercado:

| Plano | Preço sugerido | Recursos |
|---|---:|---|
| Gratuito | R$ 0 | 1 meta, até 2 trabalhos, 2 simulações/mês, sem histórico longo |
| Pro | R$ 39/mês | trabalhos ilimitados razoáveis, despesas, histórico e relatórios |
| Pro AI | R$ 79/mês | otimizador misto completo, previsões e recomendações com limites de uso justos |
| Clínica | R$ 199/mês | até 5 profissionais, metas por equipe, dashboards e RBAC |

**Regras:** teste gratuito simples, upgrade e downgrade claros, transparência sobre limites da IA e cancelamento fácil. Não prometer que software aumentará automaticamente faturamento. Permitir exportação de dados em todos os planos conforme obrigações legais.

---

## 18. Roadmap implementável (sprints sugeridas)

| Sprint | Entrega | Critério de pronto |
|---|---|---|
| 0 — Fundamentos | Monorepo, CI, Docker dev, auth, DB, design system | `docker compose up`, testes smoke, login funcionando |
| 1 — Perfis e rendas | Organização, membros, trabalhos e remuneração | CRUD, RBAC e testes de isolamento |
| 2 — Consultório e custos | Consultas, retornos, despesas, custos recorrentes | Fórmulas e histórico confiáveis |
| 3 — Agenda e capacidade | Turnos, feriados, férias, conflitos | Slots reais e descanso validado |
| 4 — Metas e dashboard | Meta bruta/líquida, snapshot, gráficos | Projeção auditável e explicável |
| 5 — Otimizador misto | Solver, comparação, cenários e no-solution | Cenários respeitam restrições e metas |
| 6 — Realizado e importação | CSV, lançamentos, realizado vs planejado | Idempotência e reconciliação básica |
| 7 — Retenção e relatórios | Relatório mensal, alertas e PDF | Uso recorrente, opt-in de alertas |
| 8 — Captação e sazonalidade | Funil e previsões simples | Cenários com premissas explícitas |
| 9 — Consultor IA | Explicar resultados e sugerir ações | Não inventa valores e não altera dados |
| 10 — Monetização | Assinaturas, limites, cobrança | Fluxos de cancelamento/upgrade testados |
| 11 — Beta e hardening | Revisões, testes, segurança, acessibilidade | Checklist de lançamento concluído |

**Sequência é orientativa**, não promessa de duração. O MVP pode ser lançado antes das sprints de IA/captação se os fluxos financeiros estiverem corretos.

---

## 19. Backlog com critérios de aceite (histórias prioritárias)

### US-001 — Criar perfil de trabalho

**Como** médico, quero cadastrar um plantão com valor, duração, deslocamento e tributos, **para** comparar rendas.  
**Aceite:** dados obrigatórios validados, campos opcionais definidos, persistência segura, edição, arquivamento, visão de ganho/hora e testes unitários.

### US-002 — Configurar retorno de consulta

**Como** médico, quero dizer que 76% dos pacientes retornam e gastam metade do tempo, **para** calcular ocupação e receita.  
**Aceite:** retorno gratuito/parcial/pago; prazo de retorno; impacto no mês correto; zero divisão/valores inválidos tratados.

### US-003 — Definir meta líquida

**Como** médico, quero definir R$ 50.000 líquidos em outubro, **para** visualizar a diferença a recuperar.  
**Aceite:** separar bruto/líquido; renda atual sem duplicações; mês identificado; explicação de premissas.

### US-004 — Otimizar combinação de atividades

**Como** médico, quero receber uma combinação de plantões, consultas e sobreavisos **para** alcançar minha meta com menor tempo.  
**Aceite:** decisões inteiras, somatório financeiro correto, restrição de slots/descanso/demanda, nenhum cenário inviável declarado válido, resultado reprodutível.

### US-005 — Comparar cenários

**Como** médico, quero comparar menor horas, maior margem e equilibrado, **para** escolher conscientemente.  
**Aceite:** cada cenário mostra renda líquida estimada, horas ativas/comprometidas, premissas, riscos e motivo de ranking.

### US-006 — Explicar meta impossível

**Como** médico, quero saber por que a meta não cabe na agenda, **para** decidir o que alterar.  
**Aceite:** exibir máximo alcançável e restrições bloqueadoras; nunca omitir déficit.

### US-007 — Registrar realizado

**Como** médico, quero registrar plantões feitos e consultas recebidas, **para** comparar com plano.  
**Aceite:** plano não conta como realizado; lançamento possui data, fonte, competência e status; duplicidade alertada.

### US-008 — Analisar sazonalidade

**Como** gestor, quero registrar férias, feriados e histórico de captação, **para** projetar meses com menos consultas.  
**Aceite:** impactos atribuídos às variáveis declaradas, valores com faixas e marcação de dados insuficientes.

### US-009 — Receber recomendações com IA

**Como** médico, quero perguntar “onde mexo neste mês?”, **para** receber explicações acionáveis.  
**Aceite:** números extraídos apenas de ferramentas calculadas, fontes/hipóteses visíveis, IA não executa ação sem consentimento.

### US-010 — Proteger dados entre clínicas

**Como** dono de clínica, quero que ninguém de outro cliente acesse meus dados.  
**Aceite:** testes automatizados de tentativas cross-tenant em CRUD, exportação, relatórios, IA e otimização; zero vazamento.

### US-011 — Cancelar e exportar

**Como** assinante, quero cancelar e exportar meus dados facilmente.  
**Aceite:** rotas acessíveis, confirmação clara, efeito de cobrança transparente e exportação segura.

### US-012 — Permissões da secretaria

**Como** médico, quero permitir agenda/funil sem mostrar renda pessoal.  
**Aceite:** permissões testadas no backend e UI, logs e revogação efetiva.

---

## 20. Casos de teste indispensáveis

### 20.1 Financeiro

1. `meta=50.000`, `base=38.000` -> lacuna `12.000`.
2. Duas fontes com mesmo recebimento não podem ser duplicadas ao consolidar.
3. Valores negativos de estorno são exibidos corretamente.
4. Diferenciar recebimento de outubro de competência setembro.
5. `76%` retorno de 40min com retorno 20min -> `55,2 min` esperado por nova consulta no mesmo horizonte.
6. Retorno gratuito 30 dias depois ocupa próximo mês sem gerar receita duplicada.
7. PF e PJ com perfis tributários diferentes respeitam parâmetros e validade.

### 20.2 Otimizador

1. `2×2.000 + 10×300 + 3×1.000 = 10.000`: déficit residual `2.000` frente à lacuna de `12.000`.
2. Se houver plantão adicional de R$ 2.000 e capacidade/descanso, a combinação pode fechar lacuna; caso contrário deve ser inviável.
3. `max_hours=0` não pode retornar trabalho adicional válido.
4. 10 consultas com demanda máxima de 3 não podem ser recomendadas como garantidas.
5. Sobreaviso e plantão com conflito de horário: não coexistem salvo regra explícita compatível.
6. Ordenação estável para empates; solver reproduz resultados com mesma semente e dados.
7. Resposta inviável informa limite máximo atingível e motivo.
8. Não há conta com floats que gere diferença de centavos.
9. Cenário salvo mantém snapshot mesmo quando valores de trabalho mudam depois.

### 20.3 Segurança e produto

1. Cross-tenant bloqueado em API, download e ferramentas de IA.
2. Usuário `ASSISTANT` não vê rendimentos pessoais sem permissão.
3. Import CSV com duplicatas não cria faturamento duplicado.
4. Arquivo com fórmula maliciosa não executa na exportação CSV (CSV injection).
5. IA nunca interpreta anexo como instrução privilegiada.
6. Fechamento mensal não envia dados financeiros sensíveis por notificações abertas.
7. Botões de salvar, aplicar, desfazer e cancelar têm efeito real e feedback.
8. Layout funciona em 360px, tablet e desktop, com teclado e screen reader.

---

## 21. Métricas de sucesso e riscos de negócio

**North star metric:** número de usuários pagantes que **revisam uma meta e atualizam ao menos um resultado real por mês**, com valor percebido.  
**KPIs:** cadastro -> primeira simulação, primeira simulação -> assinatura, uso do otimizador, importação de dados, retenção 30/90 dias, churn, receita média por usuário, CAC, suporte por assinante e taxa de divergência financeira.

| Risco | Mitigação |
|---|---|
| Usuário usa calculadora e cancela | Histórico, atualizações mensais, importações, alertas úteis |
| Preenchimento manual excessivo | Templates, recorrências, CSV, integrações graduais |
| IA gera recomendação incorreta | Engine determinístico, ferramenta versionada, premissas claras |
| Suposição de demanda irreais | Limites de slots/leads, estimativas conservadoras |
| Tributação incorreta | Parâmetros manuais, revisão contábil, versionamento |
| Overwork / descanso insuficiente | Restrições duras e informações de segurança |
| Competições com ERP completo | Foco em otimização pessoal e múltiplos vínculos |
| Vazamento multi-tenant | RBAC, RLS, testes, auditoria, segurança desde MVP |
| Custo alto de IA | IA sob demanda, cache de snapshots, limites por plano |

---

## 22. Definição de pronto para lançamento beta

- [ ] Onboarding concluído com consultório e ao menos um outro tipo de renda.
- [ ] Usuário consegue perguntar `Quero R$ 50.000 líquidos neste mês` e receber lacuna auditável.
- [ ] Otimizador misto combina consultas, plantões e sobreavisos com restrições e declara inviabilidade corretamente.
- [ ] Cálculos de retornos gratuitos respeitam o mês em que ocupam a agenda.
- [ ] Dashboard compara projetado x realizado.
- [ ] Dados financeiros podem ser exportados.
- [ ] Termos e limitações tributárias/IA visíveis; controles LGPD implementados.
- [ ] Perfis e isolamento multi-tenant testados.
- [ ] Backup/restore testados, monitoramento e logs seguros ativos.
- [ ] Acessibilidade básica e responsividade verificadas.
- [ ] Assinaturas, cancelamento e falhas de pagamento testados antes de cobrar.
- [ ] Testes automatizados críticos passando em CI.

---

## 23. Prompt inicial para Codex (copiar e colar)

```text
Você é um engenheiro full-stack sênior e arquiteto de software. Leia integralmente o arquivo FinanceMed_AI_PRD_Completo.md e implemente o FinanceMed AI por etapas, sem omitir regras de negócio. Comece pela Sprint 0 e Sprint 1; NÃO tente fazer todas as funcionalidades numa única entrega.

Stack: Next.js + TypeScript (PWA), FastAPI + Python, PostgreSQL, SQLAlchemy/Alembic, testes Pytest/Playwright. Estruture packages/finance_engine e packages/optimizer_engine; OR-Tools CP-SAT quando chegar à sprint do otimizador.

Regras invioláveis:
1. Não inventar resultados financeiros nem permitir que LLM realize contas definitivas.
2. Valores monetários em centavos inteiros, períodos/competências explícitos e timezone consistente.
3. Isolamento por organization_id, RBAC, validações server-side, testes de segurança.
4. Nunca considerar cenário planejado como receita realizada.
5. Distinguir renda bruta, receita líquida, lucro de consultório e renda pessoal.
6. Respeitar agenda, demanda, períodos de retorno, descanso e conflitos.
7. Tributos PF/PJ paramétricos e marcados como estimativa até validação profissional.
8. Design responsivo em pt-BR, estados de loading/erro/vazio, botões funcionais.

Primeira entrega:
- Leia PRD e gere um plano incremental em docs/IMPLEMENTATION_PLAN.md com dependências, escopo, riscos e critérios de aceite.
- Crie monorepo, docker-compose para PostgreSQL, backend FastAPI com health endpoint e autenticação base, frontend Next.js com layout e login, migrations, seed fictício opcional, variáveis de ambiente de exemplo e CI para lint/testes.
- Implemente organizações, usuários, memberships e RBAC, com testes de isolamento de tenants.
- Inclua README de instalação, comandos de execução e testes.
- Ao terminar, mostre arquivos criados, comandos executados, testes que passaram, pendências e próxima sprint. Não alegue que algo funciona se não tiver sido verificado.
```

---

## 24. Decisões de produto em aberto (registrar como ADR, não bloquear MVP)

1. Limites de jornada/descanso: parametrizados e condicionados a contratos; regras locais dependem de avaliação jurídica/ocupacional.
2. Definição de retorno por paciente: um retorno por episódio inicialmente; suportar múltiplos após validação.
3. Competência x caixa e distribuição de lucros PJ: definir explicações orientativas e regras por fonte.
4. Granularidade de dados: MVP sem identificação de pacientes; integrações futuras podem exigir revisão de privacidade.
5. Modelo de precificação final: validar willingness-to-pay antes de fixar planos.
6. Provedores de login, pagamentos e IA: selecionar após revisão de custo, privacidade, compliance e disponibilidade.
7. Confirmação de disponibilidade de plantões/sobreavisos: edição manual no MVP; integrações futuras.
8. Versão mobile: Flutter após evidência de tração do PWA.

---

**Critério norteador:** se uma nova função não melhora a precisão da meta, a viabilidade da agenda, a decisão de alocação de horas ou a retenção mensal do médico, adiar em favor do fluxo principal.
