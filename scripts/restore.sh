#!/usr/bin/env bash
# Restore a backup produced by backup.sh. DESTRUCTIVE: replaces the current data.
#   ./scripts/restore.sh ./backups 20261013-020000
set -euo pipefail

DIR="${1:?usage: restore.sh <backup-dir> <stamp>}"
STAMP="${2:?usage: restore.sh <backup-dir> <stamp>}"
DB="$DIR/contexthub-db-$STAMP.dump"
FILES="$DIR/contexthub-uploads-$STAMP.tgz"
COMPOSE=(docker compose ${CONTEXTHUB_ENV_FILE:+--env-file "$CONTEXTHUB_ENV_FILE"})

( cd "$DIR" && sha256sum -c "contexthub-$STAMP.sha256" )
read -r -p "Remplacer TOUTES les données actuelles par la sauvegarde $STAMP ? (oui/non) " answer
[ "$answer" = "oui" ] || { echo "Annulé."; exit 1; }

"${COMPOSE[@]}" stop api worker beat
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < "$DB"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh api -c 'rm -rf /data/uploads/* && tar -C /data -xzf -' < "$FILES"
"${COMPOSE[@]}" start api worker beat
echo "Restauration terminée. Vérifiez l'intégrité du journal : docker compose exec api entrypoint.sh cli verify-audit"
