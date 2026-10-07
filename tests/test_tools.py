"""Tests for the relocated export_to_md developer tool."""

from __future__ import annotations

import logging
from pathlib import Path

from sms_toolkit.tools import export_to_md


def _make_tree(root: Path) -> Path:
    src = root / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    (src / "pkg" / "b.py").write_text("y = 2\n", encoding="utf-8")
    return src


def test_mirror_mode(tmp_path: Path) -> None:
    src = _make_tree(tmp_path)
    dest = tmp_path / "out"
    export_to_md.mirror_py_to_md(src_path=src, dest_path=dest, rollup_dirs=False)
    assert (dest / "project_structure.md").exists()
    assert (dest / "a.md").exists()
    assert (dest / "pkg" / "b.md").exists()
    assert "x = 1" in (dest / "a.md").read_text(encoding="utf-8")


def test_rollup_mode(tmp_path: Path) -> None:
    src = _make_tree(tmp_path)
    dest = tmp_path / "out"
    export_to_md.mirror_py_to_md(src_path=src, dest_path=dest, rollup_dirs=True)
    assert (dest / "project_structure.md").exists()
    assert (dest / "src.md").exists()  # root rolls up under the source dir name
    assert (dest / "pkg.md").exists()
    content = (dest / "src.md").read_text(encoding="utf-8")
    assert "a.py" in content and "x = 1" in content


def test_missing_source_dir_logs_warning(tmp_path: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level(logging.WARNING, logger="sms_toolkit.tools.export_to_md"):
        export_to_md.mirror_py_to_md(src_path=tmp_path / "nope", dest_path=tmp_path / "out")
    assert "does not exist" in caplog.text
    assert not (tmp_path / "out").exists()
