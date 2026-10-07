# sms-toolkit

Refactored edition of `ss-sms-br` — Android SMS/call backup (SMS Backup & Restore
XML) de-duplication, SQLite analysis tooling, OCR message extraction, and a Flask
viewer, unified into one installable package with a single `sms-toolkit` CLI.

## Status

**Phase 4 — database stages** (current)

- `sms_toolkit.stages` — the five one-shot DB stages as importable modules,
  wired to CLI subcommands:
  - `normalize` — report/apply the address/date backfill (`normalize_messages_stage`)
  - `build-contacts` — canonical address → contact-name lookup (`build_contacts_table`)
  - `import-ifttt` — IFTTT Excel import with hash + same-day dedupe (`import_ifttt_sms`)
  - `export-attachments` — CSVs + MMS attachment files + thread summary (`export_db_csv_attachments`)
  - `export-year` — one calendar year into a separate SQLite DB (`export_year_subset`)
- Shared helpers deduplicated: phone/date normalization, MD5 dedup hashing,
  SQLite connect/backup/prune/hash-loading all come from `normalize`, `hashing`,
  `db`

**Phase 3 — unified sms+calls pipeline**

- `sms_toolkit.pipeline` — generic streaming de-dup pipeline (`parser`,
  `processor`, context-managed `exporter`, `runner`) plus `sms/variant.py` and
  `calls/variant.py`; `sms`/`calls` CLI subcommands wired

**Phase 2 — shared normalize, hashing, and db modules**

- `normalize` (canonical address/phone/date), `hashing` (byte-identical MD5
  dedup keys), `db` (config-driven SQLite, backups, hash loading)

**Phase 1 — package skeleton, centralized config, standardized logging**

- Installable package (`pip install -e .`) with `sms-toolkit` console script
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
├── cli.py             # sms-toolkit entry point
├── config.py          # Paths / PipelineConfig / ViewerConfig / LoggingConfig / AppConfig
├── logging_config.py  # setup_logging(), get_logger()
├── normalize.py       # canonical address/phone/date normalization
├── hashing.py         # byte-identical MD5 dedup keys
├── db.py              # SQLite connect/schema/backup/hash helpers
├── pipeline/          # generic streaming de-dup pipeline core
├── sms/               # SMS variant (tags, keying, DDL, SQL)
├── calls/             # calls variant
└── stages/            # one-shot DB stages (normalize, contacts, ifttt, exports)
tests/                 # pytest suite (incl. differential tests vs original/)
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
