"""Command-line interface for sms_toolkit.

Phase 1: the subcommand surface with stubs. Each subcommand is wired to its
real implementation in its refactor phase; stubs exit with code 2.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from sms_toolkit import __version__
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


_RUNNERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "sms": _run_sms,
    "calls": _run_calls,
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
