"""Streaming XML element parser (lxml, constant memory).

Unifies ``stream_sms_elements`` and ``stream_call_elements`` from the two
original pipelines, which were identical apart from the element/root tag names
and the tqdm postfix content.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from lxml import etree as ET
from tqdm import tqdm


def stream_elements(
    file_path: str | Path,
    *,
    tags: Sequence[str],
    parent_tag: str,
    pbar_pos: int = 1,
    progress_every: int = 100,
    stats_fn: Callable[[], Mapping[str, str]] | None = None,
) -> Iterator[Any]:
    """Yield matching elements from a large XML backup file.

    Elements are cleared as they are consumed so memory stays flat. ``stats_fn``
    (optional) supplies the tqdm postfix dict every ``progress_every`` elements.
    Yields lxml elements.
    """
    file_size = Path(file_path).stat().st_size
    wanted = set(tags)

    with open(file_path, "rb") as f:
        pbar = tqdm(
            total=file_size,
            unit="B",
            unit_scale=True,
            desc="└─ Streaming",
            position=pbar_pos,
            leave=False,
            colour="cyan",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}, {rate_fmt}{postfix}]",
        )
        try:
            # Pass huge_tree and recover directly to iterparse
            context = ET.iterparse(f, events=("end",), recover=True, huge_tree=True)

            count = 0
            for _event, elem in context:
                if elem.tag in wanted:
                    yield elem
                    count += 1

                    if count % progress_every == 0:
                        pbar.n = f.tell()
                        if stats_fn is not None:
                            pbar.set_postfix(stats_fn(), refresh=True)

                    elem.clear()
                    parent = elem.getparent()
                    if parent is not None and parent.tag == parent_tag:
                        while elem.getprevious() is not None:
                            del parent[0]

                elif elem.getparent() is not None and elem.getparent().tag == parent_tag:
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]

            pbar.n = file_size
            pbar.refresh()
        finally:
            pbar.close()
