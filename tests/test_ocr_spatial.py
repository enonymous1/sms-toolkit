"""Tests for spatial helpers: TextBlock geometry, clustering, grouping."""

from __future__ import annotations

from sms_toolkit.ocr import spatial
from sms_toolkit.ocr.models import TextBlock
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module

OCR_ORIG = ORIGINAL_ROOT / "screenshot_analyzer"

V3 = import_original_module("orig_ocr_v3s", OCR_ORIG / "extract_messages_v3.py")
V4 = import_original_module("orig_ocr_v4s", OCR_ORIG / "extract_messages_v4.py")


def _blocks() -> list[TextBlock]:
    # Two left-column blocks, one right-column block, one far below.
    return [
        TextBlock("hello", (10, 10, 100, 30), 0.9),
        TextBlock("world", (10, 40, 100, 60), 0.9),
        TextBlock("right side", (400, 10, 500, 30), 0.9),
        TextBlock("far down", (10, 500, 100, 520), 0.9),
    ]


def test_text_block_geometry() -> None:
    block = TextBlock("hi", (10, 20, 110, 40), 0.75)
    assert (block.x1, block.y1, block.x2, block.y2) == (10, 20, 110, 40)
    assert block.center_x == 60
    assert block.center_y == 30
    assert (block.width, block.height) == (100, 20)
    assert (block.top, block.bottom) == (20, 40)

    other = TextBlock("yo", (10, 100, 110, 120))
    assert block.vertical_gap_to(other) == 60
    assert block.vertical_distance_to(other) == 60  # v3 alias
    assert block.is_vertically_close(other) is False
    assert block.is_vertically_close(other, threshold=60) is True
    assert block.horizontal_distance_to(other) == 0  # same x-range overlaps


def test_cluster_by_alignment_matches_v3() -> None:
    blocks = _blocks()
    ours = spatial.cluster_by_alignment(blocks, image_width=520)

    v3_ext = object.__new__(V3.SpatialConversationExtractor)
    theirs = V3.SpatialConversationExtractor.cluster_text_by_alignment(v3_ext, blocks, 520)

    assert [b.text for b in ours["left"]] == [b.text for b in theirs["left"]]
    assert [b.text for b in ours["right"]] == [b.text for b in theirs["right"]]


def test_cluster_by_alignment_v4_matches_original() -> None:
    blocks = _blocks()
    ours = spatial.cluster_by_alignment_v4(blocks)

    v4_ext = object.__new__(V4.SpatialConversationExtractorV4)
    theirs = V4.SpatialConversationExtractorV4.cluster_by_alignment_improved(v4_ext, blocks)

    for key in ("left", "center", "right"):
        assert [b.text for b in ours[key]] == [b.text for b in theirs[key]]
    assert ours["statistics"] == theirs["statistics"]


def test_cluster_empty() -> None:
    assert spatial.cluster_by_alignment_v4([]) == {"left": [], "right": [], "center": []}


def test_group_blocks_into_messages_matches_v4() -> None:
    blocks = _blocks()
    ours = spatial.group_blocks_into_messages(blocks)

    v4_ext = object.__new__(V4.SpatialConversationExtractorV4)
    theirs = V4.SpatialConversationExtractorV4.group_blocks_into_messages(v4_ext, blocks, {})

    assert [[b.text for b in group] for group in ours] == [
        [b.text for b in group] for group in theirs
    ]
    # The far-down block is its own message; the top three group together.
    assert len(ours) == 2
