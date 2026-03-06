@echo off
setlocal

echo [1/5] Starting Docker services (Redis + PostgreSQL)...
docker-compose up -d
if errorlevel 1 ( echo ERROR: docker-compose failed & pause & exit /b 1 )

echo [2/5] Waiting for PostgreSQL to be ready...
timeout /t 4 /nobreak >nul

echo [3/5] Installing psycopg in venv (if missing)...
venv\Scripts\pip install "psycopg[binary]" -q

echo [4/5] Running database migrations...
venv\Scripts\alembic upgrade head
if errorlevel 1 ( echo WARNING: alembic reported an error - tables may already exist, continuing... )

echo [5/5] Starting services in separate windows...

start "Celery Worker" cmd /k "venv\Scripts\celery -A src.workers.celery_app worker --loglevel=info"
timeout /t 2 /nobreak >nul

start "FastAPI Backend" cmd /k "venv\Scripts\uvicorn src.app:app --reload"
timeout /t 2 /nobreak >nul

start "React Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo All services started:
echo   Backend  ->  http://localhost:8000/docs
echo   Frontend ->  http://localhost:5173
echo.
pause
