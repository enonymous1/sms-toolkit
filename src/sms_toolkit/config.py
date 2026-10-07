"""Centralized configuration for sms_toolkit.

Every hardcoded value from the original scripts (database file names, batch
sizes, SQLite PRAGMAs, retry counts, ...) lives here as frozen dataclasses.
Selected values can be overridden with ``SMS_TOOLKIT_*`` environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PREFIX = "SMS_TOOLKIT_"


def _env(name: str, default: str) -> str:
    """Read ``SMS_TOOLKIT_<name>`` or fall back to ``default``."""
    return os.environ.get(ENV_PREFIX + name, default)


def _env_int(name: str, default: int) -> int:
    """Read ``SMS_TOOLKIT_<name>`` as an int, falling back to ``default``."""
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Paths:
    """File names used across the toolkit (resolved against the project root)."""

    sms_db: str = "sms_db.sqlite"
    calls_db: str = "calls_db.sqlite"
    sms_xml: str = "Master_Backup.xml"
    calls_xml: str = "Master_Calls.xml"
    sms_csv: str = "messages.csv"
    calls_csv: str = "calls.csv"
    ifttt_excel: str = "temp/IFTTT/SMS/SMS received.xlsx"
    screenshot: str = "temp/Attachment 1.PNG"
    extracted_samples_dir: str = "extracted_samples"
    sqlite_backup_dir: str = "backups/sqlite"


@dataclass(frozen=True)
class PipelineConfig:
    """Knobs for the streaming de-dup pipeline (shared by the sms and calls variants)."""

    batch_size: int = 5000
    sqlite_journal_mode: str = "OFF"
    sqlite_synchronous: str = "OFF"
    sqlite_cache_size: int = 1_000_000
    sqlite_locking_mode: str = "EXCLUSIVE"
    sqlite_temp_store: str = "MEMORY"
    retries: int = 3
    retry_backoff_base_s: int = 5
    progress_every: int = 100
    slow_network_mbs: float = 5.0


@dataclass(frozen=True)
class ViewerConfig:
    """Flask viewer settings."""

    per_page: int = 50
    port: int = 5000
    debug: bool = False


@dataclass(frozen=True)
class LoggingConfig:
    """Logging settings; see :mod:`sms_toolkit.logging_config`."""

    level: str = "INFO"
    log_file: str | None = None  # root-relative; None -> console only


@dataclass(frozen=True)
class AppConfig:
    """Root configuration object."""

    root: Path
    paths: Paths = field(default_factory=Paths)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    viewer: ViewerConfig = field(default_factory=ViewerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def resolve(self, relative: str | Path) -> Path:
        """Resolve a root-relative path against the project root."""
        return self.root / relative


def resolve_project_root(explicit: str | Path | None = None) -> Path:
    """Resolve the project root: explicit arg > ``SMS_TOOLKIT_ROOT`` env > cwd."""
    if explicit is not None:
        return Path(explicit).resolve()
    env_root = os.environ.get(ENV_PREFIX + "ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def load_config(root: str | Path | None = None) -> AppConfig:
    """Build the :class:`AppConfig`, applying ``SMS_TOOLKIT_*`` environment overrides."""
    resolved_root = resolve_project_root(root)
    paths = Paths(
        sms_db=_env("SMS_DB", Paths.sms_db),
        calls_db=_env("CALLS_DB", Paths.calls_db),
    )
    pipeline = PipelineConfig(
        batch_size=_env_int("BATCH_SIZE", PipelineConfig.batch_size),
        retries=_env_int("RETRIES", PipelineConfig.retries),
    )
    logging_cfg = LoggingConfig(
        level=_env("LOG_LEVEL", LoggingConfig.level).upper(),
        log_file=_env("LOG_FILE", "") or None,
    )
    return AppConfig(root=resolved_root, paths=paths, pipeline=pipeline, logging=logging_cfg)
