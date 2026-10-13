#!/bin/sh
set -e

case "$1" in
  api)
    # Migrations are idempotent; running them on every start keeps upgrades to
    # "pull the new image, restart".
    alembic upgrade head
    # --proxy-headers: request.client is the real client when the request comes
    # through nginx. Only nginx can reach this port (no published port).
    exec uvicorn src.app:app \
      --host 0.0.0.0 --port 8000 \
      --proxy-headers --forwarded-allow-ips="*" \
      --workers "${API_WORKERS:-1}" \
      --timeout-keep-alive 75
    ;;
  worker)
    exec celery -A src.workers.celery_app worker \
      --loglevel="${LOG_LEVEL:-INFO}" \
      --concurrency="${WORKER_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A src.workers.celery_app beat --loglevel=INFO --schedule /tmp/celerybeat-schedule
    ;;
  cli)
    shift
    exec python -m src.cli "$@"
    ;;
  eval)
    shift
    exec python -m eval.run_eval "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
