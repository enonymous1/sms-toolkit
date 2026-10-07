import sqlite3
import csv
import xml.etree.ElementTree as ET
import os
import hashlib
import logging
from datetime import datetime

class CallDataExporter:
    def __init__(self, xml_file="Master_Calls.xml", db_file="calls_db.sqlite", csv_file="calls.csv", archive_old=False, preserve_data=True):
        """Initialize exporter with optional data preservation mode.
        
        Args:
            preserve_data: If True, append to existing files and preserve DB data.
                          If False, create fresh files and drop existing DB tables.
        """
        self.xml_file = xml_file
        self.db_file = db_file
        self.csv_file = csv_file
        self.preserve_data = preserve_data
        
        # Batch lists for SQL performance
        self.call_batch = []
        self.batch_size = 5000
        
        # Archive old output files only if not preserving data
        if archive_old and not preserve_data:
            self._archive_old_files()
        
        # Handle XML file (append or fresh)
        self._init_xml_file()
        
        # Handle CSV file (append or fresh)
        self._init_csv_file()
        
        # Open SQLite
        self.conn = sqlite3.connect(self.db_file)
        self.conn.execute("PRAGMA journal_mode = OFF")
        self.conn.execute("PRAGMA synchronous = OFF")
        self.conn.execute("PRAGMA cache_size = 1000000") 
        self.conn.execute("PRAGMA locking_mode = EXCLUSIVE")
        self.conn.execute("PRAGMA temp_store = MEMORY")
        
        self.cursor = self.conn.cursor()
        self._setup_db()

    def _archive_old_files(self):
        """Archive old output files with timestamp instead of truncating."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for filepath in [self.xml_file, self.db_file, self.csv_file]:
            if os.path.exists(filepath):
                archive_name = f"{filepath}.backup_{timestamp}"
                try:
                    os.rename(filepath, archive_name)
                    logging.info(f"Archived old file: {filepath} -> {archive_name}")
                except Exception as e:
                    logging.warning(f"Could not archive {filepath}: {e}")
    
    def _init_xml_file(self):
        """Initialize XML file in append or fresh mode."""
        if self.preserve_data and os.path.exists(self.xml_file):
            # Append mode: remove closing tag, prepare for appending
            with open(self.xml_file, 'rb') as f:
                content = f.read()
            if content.endswith(b'</calls>\n'):
                content = content[:-9]  # Remove closing tag
            elif content.endswith(b'</calls>'):
                content = content[:-8]  # Remove closing tag
            self.xml_handle = open(self.xml_file, 'wb')
            self.xml_handle.write(content)
            logging.info(f"Appending to existing XML file: {self.xml_file}")
        else:
            # Fresh mode: create new file
            self.xml_handle = open(self.xml_file, 'wb')
            self.xml_handle.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n')
            self.xml_handle.write(b'<calls>\n')
            logging.info(f"Creating fresh XML file: {self.xml_file}")
    
    def _init_csv_file(self):
        """Initialize CSV file in append or fresh mode."""
        if self.preserve_data and os.path.exists(self.csv_file):
            # Append mode: open in append, don't rewrite header
            self.csv_handle = open(self.csv_file, 'a', newline='', encoding='utf-8')
            self.csv_writer = csv.writer(self.csv_handle)
            logging.info(f"Appending to existing CSV file: {self.csv_file}")
        else:
            # Fresh mode: create new file with header
            self.csv_handle = open(self.csv_file, 'w', newline='', encoding='utf-8')
            self.csv_writer = csv.writer(self.csv_handle)
            self.csv_writer.writerow(['number', 'duration', 'date', 'type', 'presentation', 'readable_date', 'contact_name'])
            logging.info(f"Creating fresh CSV file: {self.csv_file}")

    def _setup_db(self):
        """Set up database tables, preserving data if in preserve mode."""
        if self.preserve_data:
            # Create table only if it doesn't exist (preserve existing data)
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS calls (
                    call_hash TEXT PRIMARY KEY,
                    number TEXT,
                    duration INTEGER,
                    date TEXT,
                    type TEXT,
                    presentation TEXT,
                    readable_date TEXT,
                    contact_name TEXT
                )
            ''')
            logging.info("Database table ready (existing data preserved)")
        else:
            # Fresh mode: drop and recreate table
            self.cursor.execute("DROP TABLE IF EXISTS calls")
            self.cursor.execute('''
                CREATE TABLE calls (
                    call_hash TEXT PRIMARY KEY,
                    number TEXT,
                    duration INTEGER,
                    date TEXT,
                    type TEXT,
                    presentation TEXT,
                    readable_date TEXT,
                    contact_name TEXT
                )
            ''')
            logging.info("Database table initialized (fresh)")
        
        self.conn.commit()
    
    def get_existing_hashes(self):
        """Load all existing call hashes from database."""
        if not os.path.exists(self.db_file):
            return set()
        
        try:
            self.cursor.execute("SELECT call_hash FROM calls")
            hashes = {row[0] for row in self.cursor.fetchall()}
            logging.info(f"Loaded {len(hashes)} existing call hashes from database")
            return hashes
        except Exception as e:
            logging.warning(f"Could not load existing hashes: {e}")
            return set()

    def _get_call_hash(self, elem):
        """Re-generates the same unique hash used by the processor to link tables."""
        attrib = elem.attrib
        number = attrib.get('number', '')
        date = attrib.get('date', '')
        duration = attrib.get('duration', '')
        call_type = attrib.get('type', '')
        
        key = f"call|{number}|{date}|{duration}|{call_type}"
        return hashlib.md5(key.encode('utf-8')).hexdigest()

    def write_call(self, elem):
        # 1. XML output (Direct streaming)
        self.xml_handle.write(b'  ' + ET.tostring(elem, encoding='utf-8', short_empty_elements=True).strip() + b'\n')
        
        # 2. Shared Attributes
        attr = elem.attrib
        call_hash = self._get_call_hash(elem)
        
        number = attr.get('number', '')
        duration = attr.get('duration', '0')
        date = attr.get('date', '')
        call_type = attr.get('type', '')
        presentation = attr.get('presentation', '')
        readable_date = attr.get('readable_date', '')
        contact_name = attr.get('contact_name', '')

        # 3. CSV output
        self.csv_writer.writerow([number, duration, date, call_type, presentation, readable_date, contact_name])
        
        # 4. SQLite Batching
        try:
            duration_int = int(duration)
        except (ValueError, TypeError):
            duration_int = 0
            
        self.call_batch.append((call_hash, number, duration_int, date, call_type, presentation, readable_date, contact_name))
        
        if len(self.call_batch) >= self.batch_size:
            self.flush_db()

    def flush_db(self):
        if self.call_batch:
            self.cursor.executemany('INSERT OR IGNORE INTO calls VALUES (?,?,?,?,?,?,?,?)', self.call_batch)
            self.call_batch = []
        self.conn.commit()

    def close(self):
        self.flush_db()
        self.xml_handle.write(b'</calls>\n')
        self.xml_handle.close()
        self.csv_handle.close()
        self.conn.commit()
        self.conn.close()
