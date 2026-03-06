#!/usr/bin/env bash
set -e

# Activate virtualenv if it exists
if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
fi

echo "[1/4] Starting Docker services (Redis + PostgreSQL)..."
docker-compose up -d

echo "[2/4] Waiting for PostgreSQL to be ready..."
sleep 4

echo "[3/4] Running database migrations..."
alembic upgrade head

echo "[4/4] Starting services..."

# Celery worker
celery -A src.workers.celery_app worker --loglevel=info &
CELERY_PID=$!

# FastAPI backend
uvicorn src.app:app --reload &
UVICORN_PID=$!

# React frontend
(cd frontend && npm run dev) &
FRONTEND_PID=$!

echo ""
echo "All services started:"
echo "  Backend  ->  http://localhost:8000/docs"
echo "  Frontend ->  http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop all services."

# Trap Ctrl+C and kill all background processes
trap "echo 'Stopping...'; kill $CELERY_PID $UVICORN_PID $FRONTEND_PID 2>/dev/null; docker-compose stop; exit 0" INT

wait
