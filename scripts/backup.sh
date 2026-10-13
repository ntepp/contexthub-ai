#!/usr/bin/env bash
# Consistent backup of a ContextHub instance: database (pg_dump custom format) and
# original documents. Run from the directory holding docker-compose.yaml.
#   ./scripts/backup.sh [destination-dir]
# Schedule it (cron/systemd timer) and copy the result off the host.
set -euo pipefail

DEST="${1:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

echo "[1/2] Base de données…"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DEST/contexthub-db-$STAMP.dump"

echo "[2/2] Documents originaux…"
docker compose exec -T api tar -C /data -czf - uploads > "$DEST/contexthub-uploads-$STAMP.tgz"

( cd "$DEST" && sha256sum "contexthub-db-$STAMP.dump" "contexthub-uploads-$STAMP.tgz" > "contexthub-$STAMP.sha256" )
echo "Sauvegarde terminée : $DEST (horodatage $STAMP)"
