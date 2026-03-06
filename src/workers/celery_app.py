import os

from celery import Celery

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"

celery_app = Celery(
    "contexthub",
    broker=f"{REDIS_URL}/0",
    backend=f"{REDIS_URL}/1",
    include=["src.workers.tasks"],
)

celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]

# Windows: prefork uses shared semaphores that trigger PermissionError (WinError 5).
# Use 'solo' by default; override with --pool=gevent for production concurrency.
import sys
if sys.platform == "win32":
    celery_app.conf.worker_pool = "solo"
