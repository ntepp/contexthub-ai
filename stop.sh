#!/usr/bin/env bash
echo "Stopping ContextHub services..."

pkill -f "celery -A src.workers.celery_app" 2>/dev/null
pkill -f "uvicorn src.app:app" 2>/dev/null
pkill -f "vite" 2>/dev/null

docker compose -f docker-compose.dev.yaml stop

echo "All services stopped."
