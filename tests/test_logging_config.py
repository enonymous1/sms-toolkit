"""Tests for sms_toolkit.logging_config."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from sms_toolkit import logging_config
from sms_toolkit.logging_config import LOGGER_NAME


@pytest.fixture
def clean_logger() -> Iterator[logging.Logger]:
    """Isolate the global sms_toolkit logger for a test."""
    logger = logging.getLogger(LOGGER_NAME)
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    saved_configured = logging_config._configured
    logger.handlers.clear()
    logging_config._configured = False
    try:
        yield logger
    finally:
        logger.handlers.clear()
        logger.handlers.extend(saved_handlers)
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate
        logging_config._configured = saved_configured


def _file_handlers(logger: logging.Logger) -> list[RotatingFileHandler]:
    return [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]


def test_setup_is_idempotent(clean_logger: logging.Logger) -> None:
    logging_config.setup_logging("INFO")
    first_count = len(clean_logger.handlers)
    assert first_count >= 1
    logging_config.setup_logging("INFO")
    assert len(clean_logger.handlers) == first_count


def test_level_is_applied(clean_logger: logging.Logger) -> None:
    logging_config.setup_logging("DEBUG")
    assert clean_logger.level == logging.DEBUG
    assert clean_logger.isEnabledFor(logging.DEBUG)


def test_env_level_override(clean_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_TOOLKIT_LOG_LEVEL", "WARNING")
    logging_config.setup_logging()
    assert clean_logger.level == logging.WARNING


def test_file_handler_captures_debug_while_console_stays(
    clean_logger: logging.Logger, tmp_path: Path
) -> None:
    log_file = tmp_path / "logs" / "toolkit.log"
    logging_config.setup_logging("INFO", log_file=log_file)
    # Second call with the same file must not duplicate the handler.
    logging_config.setup_logging("INFO", log_file=log_file)
    assert len(_file_handlers(clean_logger)) == 1

    clean_logger.debug("debug-marker-123")
    clean_logger.info("info-marker-456")
    assert log_file.exists()
    text = log_file.read_text(encoding="utf-8")
    assert "debug-marker-123" in text
    assert "info-marker-456" in text


def test_get_logger_namespaced() -> None:
    assert logging_config.get_logger("pipeline").name == "sms_toolkit.pipeline"
