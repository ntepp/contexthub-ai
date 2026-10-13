import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

# Correlation id of the HTTP request being served, set by the request-id middleware.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "contexthub") -> logging.Logger:
    """Return a logger that prints to stdout under both uvicorn and Celery.

    Level is driven by LOG_LEVEL (default INFO), format by LOG_FORMAT (text|json).
    propagate=False keeps the output single-lined instead of being duplicated by
    uvicorn's root handler.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        # Imported lazily: config imports this module's logger name.
        from src.config import get_settings

        settings = get_settings()
        handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(_RequestIdFilter())
        if settings.log_format == "json":
            handler.setFormatter(_JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
        logger.addHandler(handler)
        logger.setLevel(settings.log_level.upper())
        logger.propagate = False
    return logger
