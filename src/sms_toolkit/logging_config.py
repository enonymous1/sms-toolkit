"""Standardized logging configuration for sms_toolkit (stdlib logging).

Replaces the ad-hoc ``print()`` progress reporting and the per-script
``logging.basicConfig`` calls in the originals. One named logger
(``sms_toolkit``): console at the configured level, plus an optional rotating
file handler that always captures DEBUG.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "sms_toolkit"
_LOG_LEVEL_ENV = "SMS_TOOLKIT_LOG_LEVEL"
_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_FILE_MAX_BYTES = 5_000_000
_FILE_BACKUP_COUNT = 3

_configured = False


def _coerce_level(level: str | int | None) -> int:
    """Resolve a level name/int/env var to a numeric logging level."""
    if level is None:
        level = os.environ.get(_LOG_LEVEL_ENV, "INFO")
    if isinstance(level, int):
        return level
    numeric = logging.getLevelName(level.upper())
    return numeric if isinstance(numeric, int) else logging.INFO


def setup_logging(
    level: str | int | None = None,
    log_file: str | Path | None = None,
) -> logging.Logger:
    """Configure the ``sms_toolkit`` logger.

    Idempotent: calling it again only adjusts levels and never duplicates
    handlers. When ``log_file`` is given, the logger itself drops to DEBUG so
    the file captures everything while the console keeps the configured level.
    """
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    numeric_level = _coerce_level(level)

    if not _configured:
        formatter = logging.Formatter(_FORMAT)
        console = logging.StreamHandler()
        console.setLevel(numeric_level)
        console.setFormatter(formatter)
        logger.addHandler(console)
        logger.propagate = False
        _configured = True
    else:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, RotatingFileHandler
            ):
                handler.setLevel(numeric_level)

    # The logger must accept DEBUG records for the file handler to see them.
    logger.setLevel(min(numeric_level, logging.DEBUG) if log_file else numeric_level)

    if log_file is not None:
        log_path = Path(log_file)
        already = any(
            isinstance(handler, RotatingFileHandler)
            and getattr(handler, "baseFilename", None) == str(log_path.resolve())
            for handler in logger.handlers
        )
        if not already:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=_FILE_MAX_BYTES,
                backupCount=_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(_FORMAT))
            logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``sms_toolkit`` namespace."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
