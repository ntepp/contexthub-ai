import logging
import os
import sys


def get_logger(name: str = "contexthub") -> logging.Logger:
    """Return a logger that prints to stdout under both uvicorn and Celery.

    Level is driven by LOG_LEVEL (default INFO). propagate=False keeps the
    output single-lined instead of being duplicated by uvicorn's root handler.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        logger.propagate = False
    return logger
