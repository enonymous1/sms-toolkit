"""Standalone Python to Markdown documentation generator and project exporter.

Fully self-contained script that prompts for source and output directories, 
automatically filters out virtual environments, cache directories, and VCS metadata, 
and mirrors or rolls up Python scripts into Markdown documentation.
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Common deep or auxiliary directories to automatically skip during traversal
EXCLUDED_DIRS: set[str] = {
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".idea",
    ".vscode",
}


def generate_tree_markdown(py_files: list[Path], src_dir: Path) -> str:
    """Generate a hierarchical markdown tree representation of the codebase.

    Args:
        py_files: List of filtered Python file paths.
        src_dir: Root source directory.

    Returns:
        Formatted markdown code block representing directory tree hierarchy.
    """
    tree_lines = [
        "# Project File Structure (Python Scripts)\n",
        f"**Source Directory:** `{src_dir}`\n",
        "```text",
    ]

    tree: dict[str, Any] = {}
    for py_file in py_files:
        rel_path = py_file.relative_to(src_dir)
        current = tree
        for part in rel_path.parts:
            current = current.setdefault(part, {})

    def _add_to_tree(node: dict[str, Any], prefix: str = "") -> None:
        # Sort directories first (bool(node) is True for dirs, False for files), 
        # then alphabetically
        items = sorted(node.keys(), key=lambda k: (not bool(node[k]), k.lower()))
        
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            sub_node = node[item]

            if not sub_node:
                tree_lines.append(f"{prefix}{connector}{item}")
            else:
                tree_lines.append(f"{prefix}{connector}📁 {item}")
                extension = "    " if is_last else "│   "
                _add_to_tree(sub_node, prefix + extension)

    tree_lines.append(f"📁 {src_dir.name}")
    _add_to_tree(tree)
    tree_lines.append("```")
    return "\n".join(tree_lines)


def mirror_py_to_md(
    src_path: str | Path | None = None,
    dest_path: str | Path | None = None,
    rollup_dirs: bool = True,
) -> None:
    """Convert Python source files into Markdown documentation files, skipping junk paths.

    Args:
        src_path: Source code root directory.
        dest_path: Target directory for mirrored markdown files.
        rollup_dirs: If True, combines all .py files in a directory into a single 
            Markdown file named after the directory.
    """
    src_dir = Path(src_path).resolve() if src_path else Path.cwd()
    dest_dir = Path(dest_path).resolve() if dest_path else src_dir / "reports" / "markdown_src_directory"

    if not src_dir.exists():
        LOGGER.warning("Source directory %s does not exist.", src_dir)
        return

    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Recursively collect .py files while filtering out excluded directories
    raw_files = sorted(src_dir.rglob("*.py"))
    py_files: list[Path] = []
    
    for file_path in raw_files:
        # Check if any parent folder component matches our exclusion list
        if any(part in EXCLUDED_DIRS for part in file_path.parts):
            continue
        py_files.append(file_path)

    if not py_files:
        LOGGER.warning("No valid .py files discovered in %s after filtering.", src_dir)
        return

    # 1. Generate and persist project layout tree
    structure_content = generate_tree_markdown(py_files, src_dir)
    structure_file = dest_dir / "project_structure.md"
    structure_file.write_text(structure_content, encoding="utf-8")
    LOGGER.info("Created structure file: %s", structure_file)

    # 2. Mirror Python scripts to Markdown
    if rollup_dirs:
        # Group files by their parent directory
        grouped_files: dict[Path, list[Path]] = defaultdict(list)
        for py_file in py_files:
            grouped_files[py_file.parent].append(py_file)

        for directory, files in grouped_files.items():
            rel_dir = directory.relative_to(src_dir)
            
            # If it's the root source directory, name it after the root. 
            # Otherwise, name it after the subdirectory.
            if rel_dir == Path("."):
                md_filename = f"{src_dir.name}.md"
                md_file = dest_dir / md_filename
            else:
                md_filename = f"{directory.name}.md"
                md_file = dest_dir / rel_dir.parent / md_filename
                
            md_file.parent.mkdir(parents=True, exist_ok=True)

            md_content = [f"# Directory: `{directory.name}`\n", f"**Path:** `{rel_dir}`\n", "---\n"]
            
            for file_path in files:
                try:
                    code = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    LOGGER.error("Skipping %s due to decode error.", file_path.name)
                    continue
                
                # Partition each file into its own section
                md_content.append(f"## `📄 {file_path.name}`\n")
                md_content.append(f"```python\n{code}\n```\n")
                md_content.append("---\n")
                
            md_file.write_text("\n".join(md_content), encoding="utf-8")
            
    else:
        # Standard 1-to-1 mirroring
        for py_file in py_files:
            rel_path = py_file.relative_to(src_dir)
            md_file = dest_dir / rel_path.with_suffix(".md")
            md_file.parent.mkdir(parents=True, exist_ok=True)

            try:
                code = py_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                LOGGER.error("Skipping %s due to decode error.", py_file.name)
                continue

            md_content = f"# `{py_file.name}`\n\n**Project Path:** `{rel_path}`\n\n---\n\n```python\n{code}\n```\n"
            md_file.write_text(md_content, encoding="utf-8")

    mode = "rolled up" if rollup_dirs else "mirrored"
    LOGGER.info("Successfully %s %d filtered Python files into %s", mode, len(py_files), dest_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    
    print("=== Standalone Python to Markdown Exporter ===")
    
    # Prompt for source directory path
    default_src = Path.cwd()
    src_input = input(f"Enter top-level directory path to export [default: {default_src}]: ").strip()
    src_path = Path(src_input) if src_input else default_src

    # Prompt for output directory path
    default_dest = src_path / "reports" / "markdown_src_directory"
    dest_input = input(f"Enter output directory path [default: {default_dest}]: ").strip()
    dest_path = Path(dest_input) if dest_input else default_dest

    # Prompt for rollup preference
    rollup_input = input("Roll up directory files into single summary files? (y/n) [default: y]: ").strip().lower()
    rollup_dirs = rollup_input != "n"

    LOGGER.info("Starting export from %s to %s...", src_path, dest_path)
    mirror_py_to_md(src_path=src_path, dest_path=dest_path, rollup_dirs=rollup_dirs)
    LOGGER.info("Export completed successfully!")