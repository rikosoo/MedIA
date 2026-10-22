#!/usr/bin/env bash
# Prova que um backup restaura: restaura num banco temporário e compara a contagem de linhas
# de cada tabela com o banco de origem. Não toca no banco de origem. Uso:
#   DATABASE_URL=postgresql://user:pass@host:5432/db scripts/restore_check.sh arquivo.dump
set -euo pipefail
: "${DATABASE_URL:?defina DATABASE_URL}"
dump="${1:?informe o arquivo .dump}"
src="${DATABASE_URL/postgresql+psycopg/postgresql}"
tmp="restore_check_$(date -u +%s)"
admin="${src%/*}/postgres"
cleanup() { psql "$admin" -qc "DROP DATABASE IF EXISTS $tmp" >/dev/null 2>&1 || true; }
trap cleanup EXIT
psql "$admin" -qc "CREATE DATABASE $tmp"
target="${src%/*}/$tmp"
pg_restore --no-owner --no-privileges --dbname "$target" "$dump"
counts() {  # "tabela contagem" ordenado, para todas as tabelas do schema public
  psql "$1" -Atc "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1" |
    while read -r t; do echo "$t $(psql "$1" -Atc "SELECT count(*) FROM \"$t\"")"; done
}
diff <(counts "$src") <(counts "$target") && echo "OK: backup restaurado e contagens idênticas ($(counts "$target" | wc -l) tabelas)"
