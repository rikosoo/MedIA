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

## Revisão OWASP Top 10 (2021)

Revisão do código (leitura + buscas por padrões perigosos) e testes automatizados de regressão (`services/api/tests/test_owasp.py`, `test_route_guards.py`, `apps/web/lib/security.test.ts`) mais um teste de navegador (Chromium) com payloads de XSS em todas as telas. **Não substitui teste de intrusão independente.**

| Categoria | Situação | Controles e evidências | Risco residual |
|---|---|---|---|
| **A01 Controle de acesso** | ✅ | RBAC por papel; vínculo consultado a cada request; objeto/organização alheios = 404; varredura de todas as 145 rotas (401 sem sessão, 404 entre organizações); dados pessoais (metas, planos, relatórios, conversas) só do dono, mesmo entre colegas da mesma organização; campos de atribuição em massa (`organization_id`, `owner_user_id`, `role`…) ignorados; cobrança só do OWNER | Papéis granulares extras (`permissions_json`) não têm tela de gestão |
| **A02 Falhas criptográficas** | 🟡 | Senhas Argon2; tokens de sessão/compartilhamento só como hash SHA-256; TOTP cifrado (Fernet); cookie `httpOnly`, `SameSite=lax`, `Secure` e HSTS por configuração; segredos fora do repositório; respostas nunca trazem hash/segredo (teste) | TLS e criptografia em repouso dependem da hospedagem; chave de cifragem única derivada de `SECRET_KEY` (sem rotação/KMS) |
| **A03 Injeção** | ✅ | **SQL**: 100% ORM/parâmetros; único SQL textual é `SELECT 1`; teste de código proíbe SQL por f-string/concatenação; 7 payloads clássicos testados em campos de texto, query, caminho, login, cadastro e importação CSV (guardados como texto, sem erro 500, sem vazamento entre clientes). **XSS**: React escapa tudo; nenhum `dangerouslySetInnerHTML`/`innerHTML`/`eval` (teste na CI); API só devolve JSON (`nosniff`); PDF escapa marcação (`<`, `&`); CSV neutraliza fórmulas (`= + - @`); 14 telas × 4 payloads no navegador: 0 execuções. **Cabeçalho/CRLF**: períodos e meses validados por regex, nome de arquivo sanitizado. **Comando/SSRF**: sem `subprocess`/`eval`; teste proíbe clientes HTTP no backend | CSP usa `unsafe-inline` em scripts (Next sem nonce): se um dia entrar `dangerouslySetInnerHTML`, a CSP não segura sozinha — por isso o teste que o proíbe |
| **A04 Design inseguro** | 🟡 | Aceite de termos; confirmações para ações destrutivas; limites por plano e cota diária do consultor; dinheiro em centavos inteiros; idempotência na cobrança; consultor IA somente leitura e sem LLM | Sem recuperação de senha (não há e-mail); modelagem formal de ameaças (STRIDE) não feita |
| **A05 Configuração incorreta** | ✅ | `/docs`, `/redoc` e `/openapi.json` desligados fora de dev; CORS só com a origem configurada e **recusa `*`**; fora de dev a API não sobe sem `SECRET_KEY` próprio e `COOKIE_SECURE`; cabeçalhos de segurança e CSP; `X-Powered-By` removido; teto de corpo (3 MB por `Content-Length`; CSV 2 MB de texto); erros 500 sem stack trace (teste) | Corpo em `chunked` sem `Content-Length` não é limitado pela API (limitar no proxy); sem WAF |
| **A06 Componentes vulneráveis** | 🟡 | `pip-audit` limpo; Next 15.5.4 → 15.5.28 (RCE crítica corrigida); lockfile do npm; auditorias rodam na CI (`npm audit --audit-level=critical`, `pip-audit`) | Restam avisos altos/moderados de `postcss` via Next (build); dependências Python por faixa (sem lockfile) e Actions por tag, não por SHA |
| **A07 Falhas de autenticação** | ✅ | Política de senha; bloqueio por conta (5 falhas/15 min) e limite por IP (X-Forwarded-For só com `TRUST_PROXY_HEADERS=true`); 2FA TOTP + recuperação; sessão nova a cada login, revogação ao sair/trocar senha, lista de dispositivos; falhas e bloqueios auditados sem a senha; reautenticação em ações sensíveis | Cadastro responde 409 "e-mail já cadastrado" (enumeração; mitigada pelo limite por IP); limite por IP é por processo |
| **A08 Integridade de software e dados** | 🟡 | Webhook com HMAC e comparação em tempo constante, eventos idempotentes (adulteração = 401); sem desserialização insegura (`pickle`/`yaml.load`); migrations versionadas | Sem assinatura de artefatos/imagens nem SBOM; Actions por tag |
| **A09 Falhas de log e monitoramento** | 🟡 | Auditoria de login, falha, bloqueio, 2FA, exportação, exclusão, cobrança, compartilhamento; log de acesso JSON com id de requisição, sem query/corpo/valores; `/ready` | Sem coleta centralizada, alertas nem retenção de logs definidos |
| **A10 SSRF** | ✅ | O backend não faz requisições HTTP de saída (teste de código); webhook é só de entrada | Revisar ao integrar provedor de pagamento/LLM (lista de destinos permitidos) |
