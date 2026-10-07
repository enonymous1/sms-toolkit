"""Shared run loop for the sms/calls streaming dedup pipelines.

Ports the near-identical ``main()`` functions from both original pipelines.
Deliberate behavior changes vs the originals (everything else is preserved):

- real exit codes: ``1`` for an invalid input dir / no XML files found /
  unexpected crash (the originals returned ``None``, i.e. exit 0, in all
  three cases), ``130`` on keyboard interrupt;
- logging goes through :mod:`sms_toolkit.logging_config` (console kept at
  ERROR during the run so INFO logs don't garble the tqdm bars, exactly like
  the originals' hand-rolled setup);
- the ``sys.path.append`` import hack is gone — this is a real package now.
"""

from __future__ import annotations

import glob
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tqdm import tqdm

from sms_toolkit.config import AppConfig, load_config
from sms_toolkit.logging_config import get_logger, setup_logging
from sms_toolkit.pipeline.exporter import Exporter
from sms_toolkit.pipeline.parser import stream_elements
from sms_toolkit.pipeline.processor import DedupProcessor
from sms_toolkit.pipeline.variants import PipelineVariant

logger = get_logger("pipeline.runner")


def _quiet_console_for_tqdm() -> None:
    """Pin the console handler to ERROR so progress logs don't garble tqdm bars."""
    for handler in logging.getLogger("sms_toolkit").handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            handler.setLevel(logging.ERROR)


def run_pipeline(
    variant: PipelineVariant,
    *,
    input_dir: str | Path,
    output_xml: str | Path | None = None,
    limit_files: int = 0,
    test: bool = False,
    retries: int | None = None,
    fresh: bool = False,
    log_level: str | None = None,
    root: str | Path | None = None,
    config: AppConfig | None = None,
) -> int:
    """Run the streaming dedup pipeline for ``variant``. Returns the exit code."""
    cfg = config or load_config(root=root)
    pipeline_cfg = cfg.pipeline
    max_retries = pipeline_cfg.retries if retries is None else retries

    setup_logging(log_level, log_file=cfg.resolve(variant.log_filename))
    _quiet_console_for_tqdm()
    logger.info(variant.starting_message)

    if test and limit_files == 0:
        limit_files = 3

    if not os.path.isdir(input_dir):
        message = f"Error: {input_dir} is not a valid directory."
        print(message)
        logger.error(message)
        return 1

    logger.info("Scanning directory: %s", input_dir)
    files = sorted(glob.glob(os.path.join(str(input_dir), "*.xml")))

    if not files:
        message = f"No XML files found in {input_dir}"
        print(message)
        logger.warning(message)
        return 1

    if limit_files > 0:
        message = f"Sampling mode: limiting to first {limit_files} files."
        print(message)
        logger.info(message)
        files = files[:limit_files]

    xml_path = cfg.resolve(output_xml) if output_xml else cfg.resolve(variant.default_xml_name)

    print("\n[1/3] Initializing master files and SQLite database...")
    init_start = time.time()

    # Initialize exporter first to set up the DB.
    print("  ├─ Initializing database and output files...", end="", flush=True)
    preserve_mode = not fresh
    exit_code = 0
    try:
        with Exporter(
            variant,
            xml_path=xml_path,
            csv_path=cfg.resolve(variant.default_csv_name),
            db_path=cfg.resolve(variant.default_db_name),
            preserve_data=preserve_mode,
            archive_old=True,
            pipeline=pipeline_cfg,
        ) as exporter:
            print(" ✓")

            # Load existing hashes if in preserve mode.
            print("  ├─ Loading existing hashes...", end="", flush=True)
            existing_hashes = exporter.load_hashes() if preserve_mode else set()
            processor = DedupProcessor(variant.key_fn, existing_hashes=existing_hashes)
            print(f" ✓ ({len(existing_hashes)} found)")

            init_duration = time.time() - init_start
            if preserve_mode and existing_hashes:
                mode_str = (
                    f"APPEND mode (will deduplicate against "
                    f"{len(existing_hashes)} existing {variant.item_noun})"
                )
            elif preserve_mode:
                mode_str = "APPEND mode (empty database)"
            else:
                mode_str = "FRESH mode (all data will be overwritten)"
            print(f"  └─ Mode: {mode_str}")
            logger.info("Initialization completed in %.2fs - %s", init_duration, mode_str)

            total_gb = sum(os.path.getsize(f) for f in files) / (1024**3)
            print(f"[2/3] Found {len(files)} files to process. Total data size: {total_gb:.2f} GB")
            print("[3/3] Starting streaming processing (Deduplicating in real-time)...\n")

            file_pbar = tqdm(files, desc="Overall Progress", position=0, leave=True, colour="green")
            try:
                for file_path in file_pbar:
                    file_name = os.path.basename(file_path)
                    file_name_short = file_name[:25]

                    for attempt in range(max_retries + 1):
                        start_time = time.time()
                        start_processed = processor.total_processed
                        start_unique = processor.unique_count

                        if attempt > 0:
                            file_pbar.set_description(f"Retrying ({attempt}): {file_name_short}")
                            logger.info(
                                "Retrying file: %s (Attempt %d/%d)",
                                file_name,
                                attempt + 1,
                                max_retries + 1,
                            )
                        else:
                            file_pbar.set_description(f"Processing: {file_name_short}")
                            logger.info("Processing file: %s", file_name)

                        try:
                            # Pass a stats hook for real-time nested progress.
                            for elem in stream_elements(
                                file_path,
                                tags=variant.tags,
                                parent_tag=variant.parent_tag,
                                pbar_pos=1,
                                progress_every=pipeline_cfg.progress_every,
                                stats_fn=lambda: variant.format_counts(processor),
                            ):
                                if processor.process_element(elem):
                                    exporter.write(elem)

                            duration = time.time() - start_time
                            total_in_file = processor.total_processed - start_processed
                            unique_in_file = processor.unique_count - start_unique

                            file_size_gb = os.path.getsize(file_path) / (1024**3)
                            speed_mbs = (file_size_gb * 1024) / max(0.1, duration)

                            logger.info(
                                "Successfully processed %s: %d %s (%d unique). "
                                "Took %.1fs (%.2f MB/s)",
                                file_name,
                                total_in_file,
                                variant.item_noun,
                                unique_in_file,
                                duration,
                                speed_mbs,
                            )

                            if speed_mbs < pipeline_cfg.slow_network_mbs:
                                logger.warning(
                                    "Speed Alert: %s processed at %.2f MB/s. "
                                    "Network might be bottlenecked.",
                                    file_name,
                                    speed_mbs,
                                )

                            # Force a database commit after every file for safety.
                            exporter.flush()

                            break  # Success! Break the retry loop.

                        except Exception as fe:
                            if attempt < max_retries:
                                wait_time = pipeline_cfg.retry_backoff_base_s * (attempt + 1)
                                logger.warning(
                                    "Error processing %s: %s. Retrying in %ds...",
                                    file_name,
                                    fe,
                                    wait_time,
                                )
                                time.sleep(wait_time)
                            else:
                                logger.error(
                                    "Failed to process %s after %d attempts: %s",
                                    file_name,
                                    max_retries + 1,
                                    fe,
                                )

                    # Update the main bar's stats after each file.
                    file_pbar.set_postfix(
                        {
                            "Uniq": f"{processor.unique_count:,}",
                            "Dups": f"{processor.duplicate_count:,}",
                        }
                    )

            except KeyboardInterrupt:
                print("\nProcess interrupted by user.")
                logger.warning("Process interrupted by user.")
                exit_code = 130
            except Exception as e:
                print(f"\nAn unexpected error occurred: {e}")
                logger.critical("Unexpected error: %s", e, exc_info=True)
                return 1

            _print_summary(variant, processor, exporter, cfg.resolve(variant.log_filename))
    except OSError as e:
        # Exporter setup (files/DB) failed before the run started.
        print(f"\nFailed to initialize outputs: {e}")
        logger.critical("Exporter initialization failed: %s", e, exc_info=True)
        return 1

    return exit_code


def _print_summary(
    variant: PipelineVariant,
    processor: DedupProcessor,
    exporter: Exporter,
    log_path: Path,
) -> None:
    """Print (and log) the end-of-run summary, as in the originals."""
    summary_lines = [
        "\n" + "=" * 50,
        f"          {variant.summary_title}",
        "=" * 50,
        f"{variant.total_label:<30} {processor.total_processed:,}",
        *variant.summary_extra(processor),
        f"{'Unique items found:':<30} {processor.unique_count:,}",
        f"{'Duplicates removed:':<30} {processor.duplicate_count:,}",
    ]

    if processor.total_processed > 0:
        dup_percent = processor.duplicate_rate * 100
        summary_lines.append(f"{'Deduplication Rate:':<30} {dup_percent:.2f}%")

    summary_lines.extend(
        [
            "-" * 50,
            "Outputs generated:",
            f"  [XML] {exporter.xml_path}",
            f"  [DB]  {exporter.db_path}",
            f"  [CSV] {exporter.csv_path}",
            f"  [LOG] {log_path}",
            "=" * 50,
        ]
    )

    for line in summary_lines:
        print(line)
        logger.info(line.strip())
