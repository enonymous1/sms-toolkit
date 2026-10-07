"""Generic streaming dedup processor.

Unifies ``sms-backup-analyzer`` ``MessageProcessor`` and
``calls-backup-analyzer`` ``CallProcessor`` (which were identical apart from
the hash-key fields and the per-tag counters). The key function is injected;
per-tag counts are tracked generically in ``tag_counts``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sms_toolkit import hashing


class DedupProcessor:
    """Dedupes streamed XML elements by their content hash.

    Args:
        key_fn: maps an element to its dedup key string (see :mod:`sms_toolkit.hashing`).
        existing_hashes: hashes already in the database; copied, never mutated in place.
    """

    def __init__(
        self, key_fn: Callable[[Any], str], existing_hashes: set[str] | None = None
    ) -> None:
        self._key_fn = key_fn
        self.seen_hashes: set[str] = set(existing_hashes) if existing_hashes else set()
        self.total_processed = 0
        self.unique_count = 0
        self.duplicate_count = 0
        self.tag_counts: dict[str, int] = {}

    def process_element(self, elem: Any) -> bool:
        """Return True if the element is unique; update counters either way."""
        self.total_processed += 1
        tag = str(elem.tag)
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1

        key = hashing.dedup_hash(self._key_fn(elem))
        if key not in self.seen_hashes:
            self.seen_hashes.add(key)
            self.unique_count += 1
            return True
        self.duplicate_count += 1
        return False

    @property
    def duplicate_rate(self) -> float:
        """Fraction of processed elements that were duplicates (0-1)."""
        if self.total_processed == 0:
            return 0.0
        return self.duplicate_count / self.total_processed
