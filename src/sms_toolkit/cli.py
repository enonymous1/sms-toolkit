"""Command-line interface for sms_toolkit.

Subcommands are wired to their real implementations as each refactor phase
lands; the remaining stubs exit with code 2.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from sms_toolkit import __version__
from sms_toolkit.config import AppConfig
from sms_toolkit.logging_config import setup_logging

EXIT_NOT_IMPLEMENTED = 2

# Maps subcommand -> refactor phase that will implement it.
SUBCOMMANDS: dict[str, str] = {
    "sms": "pipeline",
    "calls": "pipeline",
    "normalize": "stages",
    "build-contacts": "stages",
    "import-ifttt": "stages",
    "export-attachments": "stages",
    "export-year": "stages",
    "ocr-extract": "ocr",
    "ocr-match": "ocr",
    "serve": "viewer",
    "diag": "diag",
}


def _stub(command: str) -> int:
    print(f"sms-toolkit: '{command}' is not implemented yet", file=sys.stderr)
    return EXIT_NOT_IMPLEMENTED


def _add_pipeline_args(parser: argparse.ArgumentParser, *, default_output: str) -> None:
    """Shared arguments for the sms/calls streaming pipelines."""
    parser.add_argument("input_dir", help="Directory containing .xml backup files")
    parser.add_argument("--output", default=default_output, help="Path for the output XML")
    parser.add_argument(
        "--limit-files",
        type=int,
        default=0,
        help="Limit processing to the first N files (for testing)",
    )
    parser.add_argument(
        "--test", action="store_true", help="Quick test mode (processes first 3 files only)"
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Times to retry a file on read failure (default: config value)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Start fresh: delete existing data and output files. Default is to append/preserve.",
    )


def _run_sms(args: argparse.Namespace) -> int:
    from sms_toolkit.pipeline.runner import run_pipeline
    from sms_toolkit.sms.variant import SMS_VARIANT

    return run_pipeline(
        SMS_VARIANT,
        input_dir=args.input_dir,
        output_xml=args.output,
        limit_files=args.limit_files,
        test=args.test,
        retries=args.retries,
        fresh=args.fresh,
        log_level=args.log_level,
        root=args.root,
    )


def _run_calls(args: argparse.Namespace) -> int:
    from sms_toolkit.calls.variant import CALLS_VARIANT
    from sms_toolkit.pipeline.runner import run_pipeline

    return run_pipeline(
        CALLS_VARIANT,
        input_dir=args.input_dir,
        output_xml=args.output,
        limit_files=args.limit_files,
        test=args.test,
        retries=args.retries,
        fresh=args.fresh,
        log_level=args.log_level,
        root=args.root,
    )


def _config_for(args: argparse.Namespace) -> AppConfig:
    from sms_toolkit.config import load_config

    return load_config(root=args.root)


def _resolve_db(args: argparse.Namespace) -> Path:
    cfg = _config_for(args)
    return Path(args.db) if args.db else cfg.resolve(cfg.paths.sms_db)


def _resolve_backup_dir(args: argparse.Namespace) -> Path:
    cfg = _config_for(args)
    if args.backup_dir:
        return Path(args.backup_dir)
    return cfg.resolve(cfg.paths.sqlite_backup_dir)


def _run_normalize(args: argparse.Namespace) -> int:
    from sms_toolkit.stages.normalize_messages import run_normalize

    return run_normalize(
        _resolve_db(args),
        show_report=args.report,
        apply=args.apply,
        backup=args.backup,
        backup_dir=_resolve_backup_dir(args),
        backup_label=args.backup_label,
    )


def _run_build_contacts(args: argparse.Namespace) -> int:
    from sms_toolkit.stages.build_contacts import run_build_contacts

    return run_build_contacts(_resolve_db(args), dry_run=args.dry_run)


def _run_import_ifttt(args: argparse.Namespace) -> int:
    from sms_toolkit.stages.import_ifttt import run_import

    return run_import(
        _resolve_db(args),
        args.files,
        execute=args.execute,
        backup_only=args.backup_only,
        no_backup=args.no_backup,
        backup_dir=_resolve_backup_dir(args),
        backup_label=args.backup_label,
        keep_backups=args.keep_backups,
        preview=args.preview,
    )


def _run_export_attachments(args: argparse.Namespace) -> int:
    from sms_toolkit.stages.export_attachments import run_export_attachments

    cfg = _config_for(args)
    db_path = Path(args.db)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = cfg.resolve(cfg.paths.exports_dir) / f"csv_{db_path.stem}"
    if args.contacts_db:
        contacts_db_path: Path | None = Path(args.contacts_db)
    elif args.contacts_db is None:
        contacts_db_path = cfg.resolve(cfg.paths.sms_db)
    else:  # --contacts-db "" disables the lookup
        contacts_db_path = None
    return run_export_attachments(
        db_path,
        output_dir,
        include_text_parts=args.include_text_parts,
        summary_group_by=args.summary_group_by,
        apply_contact_merge=args.apply_contact_merge,
        write_merge_review=(args.write_merge_review == "yes"),
        dry_run=args.dry_run,
        contacts_db_path=contacts_db_path,
    )


def _run_export_year(args: argparse.Namespace) -> int:
    from sms_toolkit.stages.export_year import run_export_year

    cfg = _config_for(args)
    source_db = _resolve_db(args)
    if args.output:
        output_db = Path(args.output)
    else:
        output_db = cfg.resolve(cfg.paths.exports_dir) / f"sms_db_{args.year}.sqlite"
    return run_export_year(source_db, output_db, args.year, dry_run=args.dry_run)


def _run_ocr_extract(args: argparse.Namespace) -> int:
    from sms_toolkit.ocr.extractor import run_ocr_extract

    cfg = _config_for(args)
    image = Path(args.image) if args.image else cfg.resolve(cfg.paths.screenshot)
    output = Path(args.output) if args.output else None
    return run_ocr_extract(image, output, strategy=args.strategy, user_name=args.user_name)


def _run_ocr_match(args: argparse.Namespace) -> int:
    from sms_toolkit.ocr.extractor import run_ocr_match

    output = Path(args.output) if args.output else None
    return run_ocr_match(Path(args.json), _resolve_db(args), output)


_RUNNERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "sms": _run_sms,
    "calls": _run_calls,
    "normalize": _run_normalize,
    "build-contacts": _run_build_contacts,
    "import-ifttt": _run_import_ifttt,
    "export-attachments": _run_export_attachments,
    "export-year": _run_export_year,
    "ocr-extract": _run_ocr_extract,
    "ocr-match": _run_ocr_match,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the sms-toolkit CLI."""
    parser = argparse.ArgumentParser(
        prog="sms-toolkit",
        description="Android SMS/call backup de-duplication, analysis and viewing toolkit.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--root",
        default=None,
        help="Project root (default: $SMS_TOOLKIT_ROOT or current directory)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level (default: $SMS_TOOLKIT_LOG_LEVEL or INFO)",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers: dict[str, argparse.ArgumentParser] = {}
    for name, phase in SUBCOMMANDS.items():
        subparsers[name] = sub.add_parser(name, help=f"[{phase} phase] ...")
    _add_pipeline_args(subparsers["sms"], default_output="Master_Backup.xml")
    _add_pipeline_args(subparsers["calls"], default_output="Master_Calls.xml")

    norm = subparsers["normalize"]
    norm.add_argument("--db", default=None, help="Path to sms_db.sqlite")
    norm.add_argument("--report", action="store_true", help="Show what would change")
    norm.add_argument("--apply", action="store_true", help="Apply the backfill")
    norm.add_argument("--backup", action="store_true", help="Back up the DB before --apply")
    norm.add_argument("--backup-dir", default=None, help="Backup directory")
    norm.add_argument("--backup-label", default="pre-normalize", help="Backup label")

    contacts = subparsers["build-contacts"]
    contacts.add_argument("--db", default=None, help="Path to sms_db.sqlite")
    contacts.add_argument("--dry-run", action="store_true", help="Report only, no writes")

    ifttt = subparsers["import-ifttt"]
    ifttt.add_argument("--db", default=None, help="Target SQLite database")
    ifttt.add_argument("--files", nargs="+", default=["temp/IFTTT/SMS/SMS received*.xlsx"])
    ifttt.add_argument("--execute", action="store_true", help="Write to DB (default: dry-run)")
    ifttt.add_argument("--backup-only", action="store_true", help="Create a backup and exit")
    ifttt.add_argument("--no-backup", action="store_true", help="Skip the pre-import backup")
    ifttt.add_argument("--backup-dir", default=None, help="Backup directory")
    ifttt.add_argument("--backup-label", default="pre-import", help="Backup label")
    ifttt.add_argument("--keep-backups", type=int, default=20, help="Keep newest N backups")
    ifttt.add_argument("--preview", type=int, default=5, help="Sample messages to print")

    attach = subparsers["export-attachments"]
    attach.add_argument("--db", required=True, help="Source sqlite DB path")
    attach.add_argument("--output-dir", default=None, help="Output directory")
    attach.add_argument(
        "--include-text-parts", action="store_true", help="Also extract text-like MMS parts"
    )
    attach.add_argument(
        "--summary-group-by",
        choices=["raw", "normalized"],
        default="normalized",
        help="Thread summary grouping key",
    )
    attach.add_argument(
        "--apply-contact-merge", choices=["no", "yes"], default="no", help="Apply contact merges"
    )
    attach.add_argument(
        "--write-merge-review",
        choices=["no", "yes"],
        default="yes",
        help="Write contact_merge_review.csv",
    )
    attach.add_argument("--dry-run", action="store_true", help="Analyze only, no writes")
    attach.add_argument(
        "--contacts-db",
        default=None,
        help="DB with the contacts lookup table (default: sms_db.sqlite; '' disables)",
    )

    year = subparsers["export-year"]
    year.add_argument("--year", type=int, required=True, help="Year to export (e.g. 2021)")
    year.add_argument("--db", default=None, help="Source DB path")
    year.add_argument("--output", default=None, help="Output DB path")
    year.add_argument("--dry-run", action="store_true", help="Show counts only, no writes")

    ocrx = subparsers["ocr-extract"]
    ocrx.add_argument("--image", default=None, help="Screenshot image path")
    ocrx.add_argument(
        "--strategy", choices=["v1", "v2", "v3", "v4"], default="v2", help="Extraction algorithm"
    )
    ocrx.add_argument("--user-name", default="Justin", help="Name of the user (sent messages)")
    ocrx.add_argument("--output", default=None, help="Output JSON path")

    ocrm = subparsers["ocr-match"]
    ocrm.add_argument("--json", required=True, help="Extracted messages JSON path")
    ocrm.add_argument("--db", default=None, help="Path to sms_db.sqlite")
    ocrm.add_argument("--output", default=None, help="Output matched-JSON path")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    if args.command is None:
        parser.print_help()
        return 0
    runner = _RUNNERS.get(args.command)
    if runner is not None:
        return runner(args)
    return _stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
