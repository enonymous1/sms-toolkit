# `original/` — the pre-refactor sources, frozen

This directory holds the original `ss-sms-br` scripts, preserved (not
reconstructed) so the test suite can prove the refactor behaves identically.
It is **load-bearing, not decorative**: 14 test modules import
`tests/diff_helpers.py`, which AST-loads functions from here. Deleting or
"cleaning up" this directory breaks most of the suite at collection time.

Rules:

- **Do not edit.** Nothing enforces frozen-ness mechanically (a SHA-256
  manifest test is a possible future hardening), so treat any diff here
  as a test-baseline change, not a cleanup. The one exception, already
  applied, is redaction of a real phone number in comments in
  `original/screenshot_analyzer/extract_messages.py` — code behavior is
  untouched.
- **Test-only purpose.** The `sms-backup-viewer` template was never in the
  export, so `original/sms-backup-viewer/` contains only `app.py`; the
  shipped viewer template is new.
- **Not shipped in the wheel.** This directory lives outside `src`, so it
  is in the GitHub repo but not in an installed package (and not in an
  sdist without a `MANIFEST.in` graft). Anyone running the suite from an
  installed package, rather than a source checkout, will fail at
  collection for that reason.
- **Dead weight, kept deliberately.**
  `original/sms-backup-analyzer/tests/test_processor.py` is never
  collected (`testpaths = ["tests"]`); it stays as provenance, not as a
  runnable test.
- **Hygiene only:** `__pycache__` `.pyc` files may appear on disk; they
  are gitignored and untracked, and can be deleted freely.
