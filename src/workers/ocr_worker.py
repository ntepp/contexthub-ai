"""Dedicated OCR worker: consumes only the "ocr" queue, OCR_WORKER_CONCURRENCY processes.

    python -m src.workers.ocr_worker [extra celery worker options]

The default worker (``celery -A src.workers.celery_app worker``) consumes only the
default queue, so text documents are never stuck behind a long scan.
"""

from __future__ import annotations

import sys

from src.config import get_settings
from src.workers.celery_app import OCR_QUEUE, celery_app


def worker_argv(extra: list[str]) -> list[str]:
    settings = get_settings()
    return [
        "worker",
        f"--queues={OCR_QUEUE}",
        f"--hostname={OCR_QUEUE}@%h",
        f"--concurrency={max(1, settings.ocr_worker_concurrency)}",
        f"--loglevel={settings.log_level}",
        *extra,
    ]


def main() -> None:
    celery_app.worker_main(worker_argv(sys.argv[1:]))


if __name__ == "__main__":
    main()
