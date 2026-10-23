# Deploy na AWS (beta)

Guia para colocar o FinanceMed AI no ar numa instância **EC2** com **RDS PostgreSQL**, HTTPS automático e uma única origem. **Não foi testado numa conta AWS real nem com `docker compose` completo** (sem Docker no ambiente de desenvolvimento): espere pequenos ajustes na primeira subida.

> É um ambiente de **beta/teste**. Não cobre usuários reais: a cobrança é simulada, os Termos são minutas sem revisão jurídica e não há monitoramento externo. Veja `docs/LAUNCH_CHECKLIST.md`.

## Desenho

```
Internet ──443──> Caddy (EC2) ──/api/*, /ready──> api (FastAPI) ──> RDS PostgreSQL 16
                      └─ tudo o mais ──────────> web (Next.js)
```

- Mesma origem: sem CORS e sem problema de cookie. O site é compilado com `NEXT_PUBLIC_API_URL` vazio (chamadas relativas a `/api`).
- Só o Caddy publica portas. API e site ficam na rede interna do Docker.
- HTTPS é obrigatório: fora de `ENVIRONMENT=dev` a API exige `SECRET_KEY` próprio e `COOKIE_SECURE=true`.
- Região sugerida: **sa-east-1 (São Paulo)**.

## 1. Conta e custos
1. Ative **MFA** no usuário raiz e use um usuário administrador (IAM) no dia a dia.
2. Em *Billing → Budgets*, crie um orçamento com alerta (ex.: US$ 50/mês).

## 2. Grupos de segurança (EC2 → Security Groups, VPC padrão)
- **`sg-app`**, entrada: `22` só do **seu IP**; `80` e `443` de qualquer lugar.
- **`sg-db`**, entrada: `5432` **somente de `sg-app`** (origem = o grupo, não um IP).

## 3. Banco (RDS)
1. RDS → *Create database* → **PostgreSQL 16**, modelo *Dev/Test*.
2. Identificador `financemed`; usuário `financemed`; senha forte **só com letras e números**.
3. Classe `db.t4g.micro` (ou `small`); 20 GB gp3; **Storage encrypted** ligado.
4. *Public access*: **No**; security group `sg-db`; nome do banco inicial `financemed`; backups automáticos de **7 dias**; *Deletion protection* ligado.
5. Quando ficar disponível, copie o **Endpoint**.

## 4. Servidor (EC2)
1. *Launch instance*: **Ubuntu 24.04**, **t3.small** (2 GB), 20 GB gp3, par de chaves novo, grupo `sg-app`.
2. Associe um **Elastic IP** à instância.
3. **DNS:** registro **A** `app.seudominio.com` → Elastic IP. Sem domínio, use `SEU-ELASTIC-IP.sslip.io` como nome (o Caddy consegue certificado para ele).

## 5. Preparar o servidor
```bash
ssh -i sua-chave.pem ubuntu@SEU-ELASTIC-IP
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git postgresql-client
sudo usermod -aG docker ubuntu && exit          # saia e entre de novo

# swap de 2 GB: o build do site e a instalação do OR-Tools usam bastante memória
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 6. Código e configuração
```bash
git clone https://github.com/rikosoo/MedIA.git && cd MedIA
git checkout main        # ou a branch que você usa como principal

cp .env.prod.example .env.prod
nano .env.prod           # DOMAIN, DATABASE_URL (RDS), CORS_ORIGINS
sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/" .env.prod
chmod 600 .env.prod
```
**Guarde o `SECRET_KEY` num cofre.** Ele cifra os segredos do 2FA: se for perdido ou trocado, quem ativou 2FA não consegue mais entrar com o código.

## 7. Subir
```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml logs -f api     # espere "Application startup complete"
```
O primeiro build leva alguns minutos. A API aplica as migrações do banco sozinha ao iniciar.

## 8. Abrir e conferir
1. `curl https://app.seudominio.com/ready` → `{"status":"ready"}`.
2. Abra `https://app.seudominio.com`, **Criar conta**, aceite os termos.
3. Em *Planos & Cobrança*, confira o aviso "cobrança simulada".
4. `curl -sI https://app.seudominio.com | grep -i "strict-transport\|content-security"` deve mostrar os dois cabeçalhos.

## 9. Operação
- **Atualizar:** `git pull && docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build`
- **Retenção (cron diário):** `crontab -e` →
  `0 4 * * * cd /home/ubuntu/MedIA && docker compose -f docker-compose.prod.yml exec -T api python -m app.maintenance purge`
- **Backup:** o RDS faz o automático. Extra e com prova de restauração:
  `set -a; . ./.env.prod; set +a; scripts/backup.sh ~/backups && scripts/restore_check.sh ~/backups/ARQUIVO.dump`
  (o `restore_check.sh` cria um banco temporário: confirme que o usuário do RDS pode criar bancos).
- **Monitoramento mínimo:** alarme do UptimeRobot (ou CloudWatch) em `https://app.seudominio.com/ready`.
- **Logs:** `docker compose -f docker-compose.prod.yml logs --tail=100 api`.

## Custo e como parar
Uma `t3.small` + `db.t4g.micro` + IP elástico + armazenamento custa algumas dezenas de dólares por mês; confira no *AWS Pricing Calculator* para São Paulo. Para pausar: pare a EC2 e o RDS (o RDS religa sozinho após 7 dias). Para apagar: desligue a *Deletion protection*, apague o RDS (guarde um snapshot final), libere o Elastic IP e termine a EC2.

## Problemas comuns
- **Página presa em "Carregando…" (sem estilo):** o Caddy desliga o HTTP/3 (UDP 443 não está aberto no `sg-app`). Se persistir, abra F12 → *Network*, recarregue com Ctrl+Shift+R e procure linhas vermelhas ou *pending*; teste numa janela anônima.
- **Certificado não sai:** o DNS ainda não aponta para o Elastic IP, ou 80/443 não estão abertas no `sg-app`. Veja `logs caddy`.
- **API não conecta no banco:** `sg-db` precisa aceitar `sg-app` na 5432; a senha na URL não pode ter caracteres especiais.
- **API reinicia em loop pedindo `SECRET_KEY`:** falta `SECRET_KEY` ou `COOKIE_SECURE=true` no `.env.prod`.
- **Build do site morre por memória:** confirme o swap (`free -h`) ou use uma `t3.medium` só no primeiro build.

## Antes de abrir ao público
- Revisão jurídica dos Termos/Privacidade e um responsável (DPO).
- Provedor de pagamento real, nota fiscal e repetição dos testes de cobrança.
- Alertas e monitoramento além do ping; plano de resposta a incidentes.
- SSH só por SSM (ou IP fixo), WAF e rotação de segredos.
- Atualizar para o Next 16 (restam avisos do `npm audit`).
