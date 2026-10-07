"""One-shot database stages: normalize, contacts, IFTTT import, exports.

Each module exposes a ``run_*`` entry point (explicit parameters, returns an
exit code) plus the testable worker functions it is built from.
"""
