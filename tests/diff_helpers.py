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
from pathlib import Path
from typing import Any

ORIGINAL_ROOT = Path(__file__).resolve().parent.parent / "original"

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
    spec.loader.exec_module(module)
    return module
