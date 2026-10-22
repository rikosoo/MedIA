#!/usr/bin/env bash
# Backup lógico do PostgreSQL (formato custom, comprimido). Uso:
#   DATABASE_URL=postgresql://user:pass@host:5432/db scripts/backup.sh [diretório]
# Guarde o arquivo fora do servidor do banco, criptografado, e teste a restauração (restore_check.sh).
set -euo pipefail
: "${DATABASE_URL:?defina DATABASE_URL}"
dir="${1:-backups}"
mkdir -p "$dir"
file="$dir/financemed-$(date -u +%Y%m%dT%H%M%SZ).dump"
# pg_dump não entende o prefixo "+psycopg" do SQLAlchemy
url="${DATABASE_URL/postgresql+psycopg/postgresql}"
pg_dump --format=custom --no-owner --no-privileges "$url" --file "$file"
echo "$file"
