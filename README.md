# sms-toolkit

A toolkit for Android SMS/call history: it de-duplicates [SMS Backup & Restore](https://synctech.com.au/sms-backup-restore/)
XML backups into SQLite, normalizes and enriches the data, and serves it in a
messaging-style web viewer.

The motivating problem: SMS Backup & Restore makes **full** backups on every
schedule run, so a year of daily backups is dozens of multi-GB XML files that
are ~99% duplicates of each other. `sms-toolkit` streams those files, drops the
duplicates by MD5 hash, and keeps one clean database — importing only what's
genuinely new, every time.

## Features

- **Streaming de-dup import** — `sms` and `calls` import XML backups in
  append mode by default; already-imported messages are skipped via
  `INSERT OR IGNORE` on their MD5 hash. Re-importing the same folder twice
  adds nothing. `--fresh` is the only way to wipe and start over.
- **Normalization** — canonical phone/address and epoch-ms date backfill for
  rows imported before those columns existed (report-only by default).
- **Contacts** — builds a `contacts` table mapping canonical addresses to
  contact names, including multi-participant MMS pairing (UPSERT, never a
  rebuild).
- **IFTTT import** — imports IFTTT-generated SMS from Excel with the same
  hash + same-day dedupe rules.
- **Attachment export** — MMS parts out to files with safe names, plus CSVs
  and a thread summary.
- **Year export** — carves one calendar year into a standalone SQLite DB.
- **Screenshot OCR** — extracts conversations from message-app screenshots
  (four strategies, `v1`–`v4`, consolidated from four copy-pasted scripts)
  and fuzzy-matches them against the database.
- **Chat-style viewer** — a Flask app that looks like a messaging app:
  conversation list, sent/received bubbles, day dividers, inline MMS photos
  with lightbox, plus an archive list view with search and pagination.
- **Diagnostics** — `diag` checks DB existence, integrity, row counts, tag
  breakdown, date range, MMS inventory, and contacts; exit code 0/1.

## Installation

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[excel,viewer,ocr]"
```

Extras are optional and independent:

| Extra    | Provides                                     |
|----------|----------------------------------------------|
| `excel`  | IFTTT Excel import (`pandas`, `openpyxl`)    |
| `viewer` | the Flask web viewer (`flask`)               |
| `ocr`    | screenshot OCR (`pillow`, `numpy`, `pytesseract`, `easyocr`, `opencv-python`, `fuzzywuzzy`) |
| `dev`    | `pytest`, `ruff`, `mypy` and type stubs      |

Base install (no extras) covers import, normalize, contacts, exports, and
diagnostics — it only needs `lxml` and `tqdm`.

## Quick start

```bash
# 1. Import new backups (append mode: only new messages land)
sms-toolkit --root D:\data sms F:\backups\xml

# 2. Check the database
sms-toolkit diag --db D:\data\sms_db.sqlite

# 3. Backfill normalized columns, then rebuild contacts (dry-run first)
sms-toolkit --root D:\data normalize --report
sms-toolkit --root D:\data normalize --apply --backup
sms-toolkit --root D:\data build-contacts --dry-run
sms-toolkit --root D:\data build-contacts

# 4. Browse it
sms-toolkit serve --db D:\data\sms_db.sqlite
# → http://127.0.0.1:5000
```

`--root` is the directory holding `sms_db.sqlite` (or set `SMS_TOOLKIT_ROOT`
once and omit it). Every command that takes `--db` also accepts it directly.

## Commands

| Command              | What it does |
|----------------------|--------------|
| `sms <input-dir>`    | Import SMS/MMS XML backups into the DB |
| `calls <input-dir>`  | Import call-log XML backups |
| `normalize`          | Report (`--report`) or apply (`--apply`, with `--backup`) the address/date backfill |
| `build-contacts`     | Build/refresh the contacts table (`--dry-run` to preview) |
| `import-ifttt`       | Import IFTTT SMS from Excel workbooks |
| `export-attachments` | Export MMS attachments + CSVs + thread summary |
| `export-year`        | Export one year to a separate SQLite DB |
| `ocr-extract`        | Extract messages from screenshots (`--strategy v1\|v2\|v3\|v4`) |
| `ocr-match`          | Fuzzy-match extracted messages against the DB |
| `serve`              | Run the web viewer (`--db/--host/--port/--debug/--per-page`) |
| `diag`               | Database + environment health check (exit 0/1) |

Run `sms-toolkit <command> --help` for each command's flags.

## The viewer

`serve` starts a two-pane app: a conversation list on the left (avatar,
snippet, timestamp, message count, search) and a thread view on the right
with iMessage-style bubbles — blue on the right for sent, grey on the left
for received, derived from the backup's `type` field. MMS photos render
inline (click to enlarge); clicking any bubble shows the full message
detail (parts, addresses). A **Chat / Archive** toggle switches to the
classic searchable, paginated list. Dark mode follows the OS; on narrow
screens the list collapses to a back-button flow.

The viewer never writes to the database: a missing DB file exits with
code 1 instead of creating an empty one (the original script's behavior
was to create an empty DB and then 500 on every page).

## Configuration

`load_config()` builds an `AppConfig` from defaults plus environment
overrides:

| Variable                | Default              |
|-------------------------|----------------------|
| `SMS_TOOLKIT_ROOT`      | current directory    |
| `SMS_TOOLKIT_LOG_LEVEL` | `INFO`               |
| `SMS_TOOLKIT_LOG_FILE`  | (console only)       |
| `SMS_TOOLKIT_SMS_DB`    | `sms_db.sqlite`      |
| `SMS_TOOLKIT_CALLS_DB`  | `calls_db.sqlite`    |
| `SMS_TOOLKIT_BATCH_SIZE` | `5000`              |
| `SMS_TOOLKIT_RETRIES`   | `3`                  |

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
├── stages/            # one-shot DB stages (normalize, contacts, ifttt, exports)
├── ocr/               # screenshot OCR (strategies v1-v4, spatial, matcher)
├── viewer/            # Flask viewer (app factory, queries, templates)
├── diag.py            # environment/database diagnostics (diag command)
└── tools/             # relocated dev utilities (export_to_md, byte-identical)
tests/                 # pytest suite (incl. differential tests vs original/)
original/              # reconstruction of the pre-refactor sources
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff check src tests
ruff format --check src tests
mypy --strict src
pytest
```

Current state: **184/184 tests pass; ruff, format, and mypy-strict green.**
The suite includes differential tests that run the refactored code against
the `original/` sources on identical inputs.

## Provenance and behavior notes

Refactored from `ss-sms-br`, a set of one-off scripts grown around SMS
Backup & Restore exports. The refactor (7 phases, Oct 2026) unified them
into one installable package while preserving behavior, verified by
differential tests at each phase. Deliberate, documented deviations from
the originals:

- MD5 dedup hashes are byte-identical — they are primary keys in existing
  databases, so changing them would orphan history.
- Stage modules use plain `sqlite3.connect`, not the pipeline's connection
  helper, whose PRAGMAs are unsafe for one-shot writes to the primary DB.
- The viewer hardens the original's failure modes: unknown message hash →
  404 (was 500), non-integer `page` → 400 (was 500), corrupt base64 image →
  404 (was 500).
- `date` may be stored as TEXT in real databases; date handling coerces
  defensively throughout.
- The original viewer template was missing from the export, so the shipped
  template is new; the original script itself is preserved under
  `original/` for reference.
- One-off exploratory scripts (`extract_sample.py`, `extract_extremes.py`,
  `scripts/maintenance/*_analysis*.py`) stay as originals only — superseded
  by `export-attachments` and `diag`.
- `tools/export_to_md.py` is the original `utils/export_to_markdown.py`
  relocated byte-identical (frozen: excluded from ruff, mypy override),
  because it has a latent str/list type inconsistency in one branch.
