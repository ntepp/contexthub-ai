import sys

from celery import Celery
from celery.schedules import crontab

from src.config import get_settings

settings = get_settings()

celery_app = Celery(
    "contexthub",
    broker=settings.redis_db_url(0),
    backend=settings.redis_db_url(1),
    include=["src.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=24 * 3600,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "purge-expired-data": {
            "task": "src.workers.tasks.purge_expired",
            "schedule": crontab(hour=3, minute=15),
        }
    },
)

# Windows: prefork uses shared semaphores that trigger PermissionError (WinError 5).
# Use 'solo' by default; override with --pool=gevent for production concurrency.
if sys.platform == "win32":
    celery_app.conf.worker_pool = "solo"
