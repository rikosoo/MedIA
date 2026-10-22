# Checklist de lançamento beta (PRD §22) — estado real

Legenda: ✅ feito e verificado · 🟡 feito em parte / depende de algo externo · ❌ não feito

| # | Item do PRD | Estado | Evidência / o que falta |
|---|---|---|---|
| 1 | Onboarding com consultório e outro tipo de renda | ✅ | Cartão "Primeiros passos" no painel (derivado dos dados reais, respeita o papel); cadastros de trabalhos e serviços |
| 2 | "Quero R$ 50.000 líquidos" → lacuna auditável | ✅ | Meta + lacuna (Sprint 3), otimizador (5) e Consultor IA com "Ver cálculo" (9) |
| 3 | Otimizador combina consultas, plantões e sobreavisos e declara inviabilidade | ✅ | Sprint 5: CP-SAT, validador independente, 21 testes do motor + testes de API |
| 4 | Retornos gratuitos no mês em que ocupam a agenda | ✅ | Sprint 2 (testes de mês de ocorrência) |
| 5 | Dashboard projetado × realizado | ✅ | Sprints 3 e 6 |
| 6 | Dados exportáveis | ✅ | JSON pessoal e da organização, CSV de lançamentos, relatórios PDF/CSV. Não inclui agenda/captação/conversas |
| 7 | Termos e limitações tributárias/IA visíveis; controles LGPD | 🟡 | Rodapé, login, `/termos`, `/privacidade`; aceite registrado; exportação, exclusão, retenção e 2FA. **Textos são minutas sem revisão jurídica; sem DPO/RIPD/base legal definidos** |
| 8 | Perfis e isolamento multi-tenant testados | ✅ | Testes por sprint + varredura de todas as 145 rotas (401 sem sessão; 404 entre organizações) |
| 9 | Backup/restore testados, monitoramento e logs seguros | 🟡 | Scripts e prova de restauração (CI + local), `/ready`, log JSON sem dados sensíveis. **Falta: backup agendado/cifrado, RPO/RTO, monitoramento e alertas externos** |
| 10 | Acessibilidade básica e responsividade | ✅ | axe-core WCAG 2.1 AA: 0 violações em 14 telas × claro/escuro (após 1 correção); auditoria de 11 tamanhos de tela sem rolagem horizontal. **Falta teste manual com leitor de tela** |
| 11 | Assinaturas, cancelamento e falhas de pagamento testados antes de cobrar | 🟡 | Fluxos testados (26 testes) com **provedor simulado**. Repetir tudo com o provedor real antes de cobrar |
| 12 | Testes críticos automatizados passando em CI | 🟡 | Workflow atualizado (lint, testes em PostgreSQL, migrations, restauração, auditorias de dependências, build). **Ainda não rodou no GitHub**: não houve push |

## Bloqueadores antes de cobrar usuários reais
1. Revisão jurídica dos Termos/Privacidade; definir controlador, encarregado e base legal.
2. Provedor de pagamento real, nota fiscal e repetição dos testes de cobrança/falha.
3. Backup agendado e cifrado, monitoramento e plano de incidentes.
4. Revisão contábil dos cálculos tributários antes de chamá-los de oficiais (hoje são sempre "estimativa").
5. Next 16 (avisos restantes do `npm audit`) e teste de intrusão.
6. Validar preços e limites dos planos com usuários (hipóteses do PRD §17).
