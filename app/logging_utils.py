from __future__ import annotations

import logging
from pathlib import Path

from app.constants import APP_NAME
from app.paths import LOG_FILE, ensure_project_dirs

_LOGGING_INITIALIZED = False


def init_logging(log_file: Path = LOG_FILE) -> logging.Logger:
    global _LOGGING_INITIALIZED

    ensure_project_dirs()
    logger = logging.getLogger(APP_NAME)
    if _LOGGING_INITIALIZED and logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)

    _LOGGING_INITIALIZED = True
    return logger


def get_logger(name: str) -> logging.Logger:
    init_logging()
    if not name or name == APP_NAME:
        return logging.getLogger(APP_NAME)
    if name.startswith(f"{APP_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{APP_NAME}.{name}")
