@echo off
echo Stopping ContextHub services...

taskkill /FI "WINDOWTITLE eq Celery Worker*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq FastAPI Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq React Frontend*" /F >nul 2>&1

docker-compose stop

echo All services stopped.
pause
