"""Helpers for differential tests against the pristine originals.

Several originals execute work at import time (e.g. the IFTTT maintenance
scripts open their Excel file on import), so these helpers extract just the
function definitions via AST and exec them in a controlled namespace instead
of importing the modules.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

ORIGINAL_ROOT = Path(__file__).resolve().parent.parent / "original"

SMS_FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<smses>
  <sms address="5716435424" date="1685712000000" body="hello" type="1" readable_date="May 23, 2014" contact_name="Justin" />
  <sms address="5716435424" date="1685712001000" body="world" type="2" readable_date="May 23, 2014" contact_name="Justin" />
  <sms address="5716435424" date="1685712000000" body="hello" type="1" readable_date="May 23, 2014" contact_name="Justin" />
  <mms address="5716435424" date="1685712002000" m_type="132" readable_date="May 23, 2014" contact_name="Justin">
    <parts>
      <part seq="0" ct="text/plain" name="text_0.txt" text="mms text" />
      <part seq="1" ct="image/jpeg" name="img.jpg" data="aGVsbG8=" />
    </parts>
    <addrs>
      <addr address="5716435424" type="137" charset="106" />
      <addr address="15551234567" type="151" charset="106" />
    </addrs>
  </mms>
  <note>not a message</note>
</smses>
"""

CALLS_FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<calls>
  <call number="5716435424" duration="61" date="1685712000000" type="1" presentation="1" readable_date="May 23, 2014" contact_name="Justin" />
  <call number="5716435424" duration="61" date="1685712000000" type="1" presentation="1" readable_date="May 23, 2014" contact_name="Justin" />
  <call number="15551234567" duration="abc" date="1685712001000" type="2" presentation="1" readable_date="May 23, 2014" contact_name="Sara" />
</calls>
"""

_BASE_GLOBALS: dict[str, Any] = {"re": re, "hashlib": hashlib}


def load_original_function(path: str | Path, name: str, **namespace: Any) -> Any:
    """Return the function ``name`` defined in the file at ``path``.

    Only the single function definition is executed — never module-level code.
    """
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            code = compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec")
            globals_: dict[str, Any] = dict(_BASE_GLOBALS)
            globals_.update(namespace)
            locals_: dict[str, Any] = {}
            exec(code, globals_, locals_)  # noqa: S102 - test-only, controlled input
            return locals_[name]
    raise AssertionError(f"function {name!r} not found in {path}")


def import_original_module(name: str, path: str | Path) -> Any:
    """Import a module from ``path`` under a synthetic ``name`` (import-safe originals only)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations via sys.modules at decoration time.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
