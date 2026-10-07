import os
import sys
import glob
import logging
import argparse
import time
from tqdm import tqdm

# Add current directory to path for local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from parser import stream_sms_elements
from processor import MessageProcessor
from analyzer import DataExporter

def setup_logging():
    # Configure logging to file
    logging.basicConfig(
        filename='processing_log.txt',
        filemode='w',
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    # Add a console handler for critical errors only to avoid messing with tqdm
    console = logging.StreamHandler()
    console.setLevel(logging.ERROR)
    logging.getLogger('').addHandler(console)

def main():
    setup_logging()
    logging.info("Starting SMS Backup Analyzer")
    
    parser = argparse.ArgumentParser(description="Senior Android SMS Backup De-duplicator")
    parser.add_argument("input_dir", help="Directory containing .xml backup files")
    parser.add_argument("--output", default="Master_Backup.xml", help="Path for the output XML")
    parser.add_argument("--limit-files", type=int, default=0, help="Limit processing to the first N files (for testing)")
    parser.add_argument("--test", action="store_true", help="Quick test mode (processes first 3 files only)")
    parser.add_argument("--retries", type=int, default=3, help="Number of times to retry a file on read failure (network drops)")
    parser.add_argument("--fresh", action="store_true", help="Start fresh: delete existing data and output files. Default is to append/preserve.")
    
    args = parser.parse_args()
    
    if args.test and args.limit_files == 0:
        args.limit_files = 3

    if not os.path.isdir(args.input_dir):
        error_msg = f"Error: {args.input_dir} is not a valid directory."
        print(error_msg)
        logging.error(error_msg)
        return

    logging.info(f"Scanning directory: {args.input_dir}")
    input_pattern = os.path.join(args.input_dir, "*.xml")
    files = sorted(glob.glob(input_pattern))
    
    if not files:
        msg = f"No XML files found in {args.input_dir}"
        print(msg)
        logging.warning(msg)
        return

    if args.limit_files > 0:
        msg = f"Sampling mode: limiting to first {args.limit_files} files."
        print(msg)
        logging.info(msg)
        files = files[:args.limit_files]

    print(f"\n[1/3] Initializing Master Backup files and SQLite database...")
    init_start = time.time()
    
    # Initialize exporter first to set up DB
    print(f"  ├─ Initializing database and output files...", end='', flush=True)
    preserve_mode = not args.fresh
    exporter = DataExporter(xml_file=args.output, archive_old=True, preserve_data=preserve_mode)
    print(f" ✓")
    
    # Load existing hashes if in preserve mode
    print(f"  ├─ Loading existing message hashes...", end='', flush=True)
    existing_hashes = exporter.get_existing_hashes() if preserve_mode else set()
    processor = MessageProcessor(existing_hashes=existing_hashes)
    print(f" ✓ ({len(existing_hashes)} found)")
    
    init_duration = time.time() - init_start
    if preserve_mode and existing_hashes:
        mode_str = f"APPEND mode (will deduplicate against {len(existing_hashes)} existing messages)"
    elif preserve_mode:
        mode_str = "APPEND mode (empty database)"
    else:
        mode_str = "FRESH mode (all data will be overwritten)"
    print(f"  └─ Mode: {mode_str}")
    logging.info(f"Initialization completed in {init_duration:.2f}s - {mode_str}")

    print(f"[2/3] Found {len(files)} files to process. Total data size: {sum(os.path.getsize(f) for f in files) / (1024**3):.2f} GB")
    print(f"[3/3] Starting streaming processing (Deduplicating in real-time)...\n")
    
    # Combined file progress
    file_pbar = tqdm(files, desc="Overall Progress", position=0, leave=True, colour='green')
    
    try:
        for file_path in file_pbar:
            file_name = os.path.basename(file_path)
            file_name_short = file_name[:25]
            
            for attempt in range(args.retries + 1):
                start_time = time.time()
                start_processed = processor.total_processed
                start_unique = processor.unique_count
                
                if attempt > 0:
                    file_pbar.set_description(f"Retrying ({attempt}): {file_name_short}")
                    logging.info(f"Retrying file: {file_name} (Attempt {attempt + 1}/{args.retries + 1})")
                else:
                    file_pbar.set_description(f"Processing: {file_name_short}")
                    logging.info(f"Processing file: {file_name}")

                try:
                    # Pass processor reference for real-time nested stats
                    for elem in stream_sms_elements(file_path, pbar_pos=1, processor_ref=processor):
                        if processor.process_element(elem):
                            exporter.write_message(elem)
                    
                    duration = time.time() - start_time
                    unique_in_file = processor.unique_count - start_unique
                    total_in_file = processor.total_processed - start_processed
                    
                    file_size_gb = os.path.getsize(file_path) / (1024**3)
                    speed_mbs = (file_size_gb * 1024) / max(0.1, duration)
                    
                    logging.info(f"Successfully processed {file_name}: {total_in_file} messages. Took {duration:.1f}s ({speed_mbs:.2f} MB/s)")
                    
                    if speed_mbs < 5:  # Arbitrary threshold for "slow" network
                        logging.warning(f"Speed Alert: {file_name} processed at {speed_mbs:.2f} MB/s. Network might be bottlenecked.")
                    
                    # Force a database commit after every file for safety
                    exporter.flush_db()
                    
                    break # Success! Break the retry loop
                    
                except Exception as fe:
                    if attempt < args.retries:
                        wait_time = 5 * (attempt + 1)
                        logging.warning(f"Error processing {file_name}: {fe}. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        logging.error(f"Failed to process {file_name} after {args.retries + 1} attempts: {fe}")
            
            # Update the main bar's stats after each file
            file_pbar.set_postfix({
                "Uniq": f"{processor.unique_count:,}", 
                "Dups": f"{processor.duplicate_count:,}"
            })
                    
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
        logging.warning("Process interrupted by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        logging.critical(f"Unexpected error: {e}", exc_info=True)
    finally:
        exporter.close()
        logging.info("Closing exporter and finishing.")

    # Print Summary
    summary_lines = [
        "\n" + "="*50,
        "          SMS PROCESSING SUMMARY",
        "="*50,
        f"{'Total items processed:':<30} {processor.total_processed:,}",
        f"  - SMS: {processor.sms_count:,}",
        f"  - MMS: {processor.mms_count:,}",
        f"{'Unique items found:':<30} {processor.unique_count:,}",
        f"{'Duplicates removed:':<30} {processor.duplicate_count:,}"
    ]
    
    if processor.total_processed > 0:
        dup_percent = (processor.duplicate_count / processor.total_processed) * 100
        summary_lines.append(f"{'Deduplication Rate:':<30} {dup_percent:.2f}%")
        
    summary_lines.extend([
        "-" * 50,
        f"Outputs generated:",
        f"  [XML] {os.path.abspath(args.output)}",
        f"  [DB]  {os.path.abspath(exporter.db_file)}",
        f"  [CSV] {os.path.abspath(exporter.csv_file)}",
        f"  [LOG] {os.path.abspath('processing_log.txt')}",
        "="*50
    ])

    for line in summary_lines:
        print(line)
        logging.info(line.strip())

if __name__ == "__main__":
    main()
