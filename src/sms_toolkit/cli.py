"""Command-line interface for sms_toolkit.

Phase 1: the subcommand surface with stubs. Each subcommand is wired to its
real implementation in its refactor phase; stubs exit with code 2.
"""

from __future__ import annotations

import argparse
import sys

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
    for name, phase in SUBCOMMANDS.items():
        sub.add_parser(name, help=f"[{phase} phase] ...")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    if args.command is None:
        parser.print_help()
        return 0
    return _stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
