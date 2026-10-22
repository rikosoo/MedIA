# Segurança, privacidade e operação

Resumo do que existe no código (Sprint 11) e do que **depende de infraestrutura/decisão fora dele**.

## Autenticação e conta
- Senha com Argon2; política: 10+ caracteres, sem sequências triviais, sem conter e-mail/nome.
- Sessão por cookie `httpOnly` + CSRF em toda mutação; alterar a senha encerra as outras sessões; o usuário lista e encerra seus dispositivos.
- 2FA (TOTP, RFC 6238) opcional com 8 códigos de recuperação de uso único (só o hash fica no banco). O segredo TOTP é cifrado em repouso (Fernet, chave derivada de `SECRET_KEY`).
- Bloqueio por conta: 5 falhas → 15 min (429), configurável. Limite por IP nas rotas de autenticação (60/min, em memória **por processo**: com vários processos/instâncias, usar limite no proxy/WAF).
- Ações sensíveis (excluir conta, desligar 2FA, trocar senha) pedem a senha de novo (e o 2FA, se ativo).
- Fora de `ENVIRONMENT=dev` a API **não sobe** sem `SECRET_KEY` próprio e `COOKIE_SECURE=true`.

## Isolamento e permissões
- `X-Organization-Id` + vínculo consultado a cada request; organização alheia = 404. RBAC em `app/core/rbac.py`.
- `tests/test_route_guards.py` percorre **todas as rotas**: sem sessão → 401 (lista pública explícita: health/ready, login/cadastro, webhook com HMAC, link temporário de relatório) e, com a sessão de outro cliente, toda rota de organização → 404.

## Cabeçalhos, logs e erros
- API: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` em `/api`, HSTS quando `COOKIE_SECURE=true`.
- Web: CSP (sem frames/plugins; conexões só para a API; `unsafe-inline` em scripts/estilos por causa da hidratação do Next — sem nonce), `X-Frame-Options`, etc. (`apps/web/next.config.ts`).
- Log de acesso em JSON (`financemed.access`): id da requisição, método, caminho, status e duração. **Sem** query string, corpo, cabeçalhos, senhas ou valores financeiros. Auditoria (`audit_logs`) nunca guarda valores.
- `/health` (processo vivo) e `/ready` (confere o banco; 503 se indisponível) para balanceador/monitoramento.

## LGPD
- Cadastro exige aceite dos Termos/Política (versão e data gravadas). Textos em `/termos` e `/privacidade` são **minutas: faltam revisão jurídica, definição de controlador/encarregado e base legal por finalidade**.
- Exportação: pessoal (`GET /me/export`) e da organização (`GET /billing/export`), em qualquer plano.
- Exclusão (`DELETE /me`, com senha + e-mail digitado + 2FA): apaga o conteúdo das organizações em que a pessoa é a única proprietária e anonimiza o usuário (e-mail/nome/segredos). Proprietário com equipe precisa remover a equipe antes. Quem sai de organização alheia perde metas, avisos, conversas e vínculos pessoais; lançamentos e cadastros do negócio permanecem (atribuídos a "Usuário removido"). **Retidos por obrigação fiscal/segurança**: faturas e `audit_logs` (sem dados pessoais).
- Retenção (`python -m app.maintenance purge [--dry-run]`, rodar diariamente): sessões vencidas; avisos lidos há 180+ dias; conversas do consultor excluídas há 30+ dias; relatórios excluídos há 30+ dias.
- Não coletamos CPF/diagnóstico/prontuário/nome de paciente; o histórico do consultor remove e-mail, CPF/CNPJ e telefone digitados (melhor esforço).

## Backup e restauração
- `scripts/backup.sh` gera um dump `pg_dump -Fc`; `scripts/restore_check.sh arquivo.dump` restaura num banco temporário e **compara a contagem de linhas de todas as tabelas** com a origem (rodado na CI e localmente: 35 tabelas idênticas).
- **Fora do código**: agendar o backup, guardá-lo cifrado e fora do servidor do banco, definir RPO/RTO, ativar PITR/backup gerenciado e ensaiar a restauração periodicamente.
- Criptografia em repouso do disco/banco depende do provedor de hospedagem (não configurada aqui).

## Dependências
- CI roda `pip-audit` e `npm audit --audit-level=critical`.
- Estado local (22/10/2026): Python sem vulnerabilidades conhecidas. Web: Next atualizado 15.5.4 → 15.5.28 (corrige uma RCE crítica no protocolo flight e outras). Restam avisos **altos/moderados** de `postcss` (processamento de CSS fornecido por atacante em tempo de build) e `next` que dependem dele; a correção exige Next 16 (mudança incompatível) — risco baixo porque o app não processa CSS de terceiros. **Atualizar para o Next 16 é pendência do beta.**

## O que não foi feito (precisa de decisão ou ambiente real)
- Teste de intrusão/revisão de segurança independente; WAF/CDN; rotação automática de segredos; gestão de segredos em cofre.
- Monitoramento/alertas externos (APM, uptime, erros) e política de resposta a incidentes.
- Revisão jurídica dos Termos/Privacidade, DPO e RIPD (relatório de impacto).
- Leitor de tela testado manualmente (só auditoria automática axe-core).
