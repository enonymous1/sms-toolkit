"""Tests for sms_toolkit.config."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sms_toolkit import config


def test_pipeline_defaults_match_originals() -> None:
    p = config.PipelineConfig()
    assert p.batch_size == 5000
    assert p.retries == 3
    assert p.retry_backoff_base_s == 5
    assert p.progress_every == 100
    assert p.slow_network_mbs == 5.0
    assert p.sqlite_journal_mode == "OFF"
    assert p.sqlite_synchronous == "OFF"
    assert p.sqlite_cache_size == 1_000_000
    assert p.sqlite_locking_mode == "EXCLUSIVE"
    assert p.sqlite_temp_store == "MEMORY"


def test_paths_defaults_match_originals() -> None:
    paths = config.Paths()
    assert paths.sms_db == "sms_db.sqlite"
    assert paths.calls_db == "calls_db.sqlite"
    assert paths.sms_xml == "Master_Backup.xml"
    assert paths.calls_xml == "Master_Calls.xml"
    assert paths.sms_csv == "messages.csv"
    assert paths.calls_csv == "calls.csv"


def test_config_is_frozen() -> None:
    cfg = config.load_config(root="/tmp")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.pipeline.batch_size = 10  # type: ignore[misc]


def test_resolve_project_root_prefers_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMS_TOOLKIT_ROOT", str(tmp_path / "env-root"))
    resolved = config.resolve_project_root(explicit="/tmp/explicit-root")
    assert resolved == Path("/tmp/explicit-root").resolve()


def test_resolve_project_root_env_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_TOOLKIT_ROOT", str(tmp_path))
    assert config.resolve_project_root() == tmp_path.resolve()


def test_load_config_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SMS_TOOLKIT_LOG_LEVEL", "debug")
    monkeypatch.setenv("SMS_TOOLKIT_BATCH_SIZE", "123")
    monkeypatch.setenv("SMS_TOOLKIT_SMS_DB", "custom.sqlite")
    cfg = config.load_config(root=tmp_path)
    assert cfg.logging.level == "DEBUG"
    assert cfg.pipeline.batch_size == 123
    assert cfg.paths.sms_db == "custom.sqlite"
    assert cfg.root == tmp_path.resolve()


def test_resolve_helper(tmp_path: Path) -> None:
    cfg = config.load_config(root=tmp_path)
    assert cfg.resolve("sms_db.sqlite") == tmp_path.resolve() / "sms_db.sqlite"
