# sms-toolkit

Refactored edition of `ss-sms-br` — Android SMS/call backup (SMS Backup & Restore
XML) de-duplication, SQLite analysis tooling, OCR message extraction, and a Flask
viewer, unified into one installable package with a single `sms-toolkit` CLI.

## Status

**Phase 1 — package skeleton, centralized config, standardized logging**

- Installable package (`pip install -e .`) with `sms-toolkit` console script
  (subcommand stubs; each gets wired to its real implementation phase by phase)
- `sms_toolkit.config` — every hardcoded value from the originals as frozen
  dataclasses, with `SMS_TOOLKIT_*` environment-variable overrides
- `sms_toolkit.logging_config` — one idempotent stdlib logging setup
  (console at the configured level, optional rotating DEBUG file)
- Pristine originals preserved under `original/` (reconstructed from the repo's
  Markdown exports; the live sources remain at `F:\Code\ss-sms-br`)

## Layout

```
src/sms_toolkit/
├── __init__.py        # version
├── cli.py             # sms-toolkit entry point (stubs in Phase 1)
├── config.py          # Paths / PipelineConfig / ViewerConfig / LoggingConfig / AppConfig
└── logging_config.py  # setup_logging(), get_logger()
tests/                 # pytest suite
original/              # untouched reconstruction of the pre-refactor sources
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff check src tests
ruff format --check src tests
mypy src tests
pytest
```

## Configuration

`load_config()` builds an `AppConfig` from defaults plus environment overrides:

| Variable                | Default              |
|-------------------------|----------------------|
| `SMS_TOOLKIT_ROOT`      | current directory    |
| `SMS_TOOLKIT_LOG_LEVEL` | `INFO`               |
| `SMS_TOOLKIT_LOG_FILE`  | (console only)       |
| `SMS_TOOLKIT_SMS_DB`    | `sms_db.sqlite`      |
| `SMS_TOOLKIT_CALLS_DB`  | `calls_db.sqlite`    |
| `SMS_TOOLKIT_BATCH_SIZE` | `5000`              |
| `SMS_TOOLKIT_RETRIES`   | `3`                  |
