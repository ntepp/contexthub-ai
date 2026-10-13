#!/usr/bin/env bash
# Consistent backup of a ContextHub instance: database (pg_dump custom format) and
# original documents. Run from the directory holding docker-compose.yaml.
#   ./scripts/backup.sh [destination-dir]
# Schedule it (cron/systemd timer) and copy the result off the host.
set -euo pipefail

DEST="${1:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
# CONTEXTHUB_ENV_FILE: use another file than ./.env (several instances on one host).
COMPOSE=(docker compose ${CONTEXTHUB_ENV_FILE:+--env-file "$CONTEXTHUB_ENV_FILE"})
mkdir -p "$DEST"
# Never leave a truncated archive that looks like a valid backup.
trap 'rm -f "$DEST/contexthub-db-$STAMP.dump" "$DEST/contexthub-uploads-$STAMP.tgz"; echo "ÉCHEC de la sauvegarde" >&2' ERR

echo "[1/2] Base de données…"
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DEST/contexthub-db-$STAMP.dump"

echo "[2/2] Documents originaux…"
"${COMPOSE[@]}" exec -T api tar -C /data -czf - uploads > "$DEST/contexthub-uploads-$STAMP.tgz"

( cd "$DEST" && sha256sum "contexthub-db-$STAMP.dump" "contexthub-uploads-$STAMP.tgz" > "contexthub-$STAMP.sha256" )
echo "Sauvegarde terminée : $DEST (horodatage $STAMP)"
