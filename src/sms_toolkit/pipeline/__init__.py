"""Streaming dedup pipeline core (shared by the sms and calls variants)."""

from sms_toolkit.pipeline.exporter import Exporter
from sms_toolkit.pipeline.parser import stream_elements
from sms_toolkit.pipeline.processor import DedupProcessor
from sms_toolkit.pipeline.runner import run_pipeline
from sms_toolkit.pipeline.variants import MappedElement, PipelineVariant

__all__ = [
    "DedupProcessor",
    "Exporter",
    "MappedElement",
    "PipelineVariant",
    "run_pipeline",
    "stream_elements",
]
