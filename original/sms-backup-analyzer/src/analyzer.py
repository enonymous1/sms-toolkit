import sqlite3
import csv
import xml.etree.ElementTree as ET
import os
import hashlib
import logging
from datetime import datetime


def normalize_address(value):
    """Normalize phone-like values to digit-only form for stable grouping."""
    if value is None:
        return ""
    text = str(value).strip()
    digits = ''.join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith('1'):
        return digits[1:]
    return digits


def normalize_date_ms(value):
    """Return epoch milliseconds as integer when available."""
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    return None

class DataExporter:
    def __init__(self, xml_file="Master_Backup.xml", db_file="sms_db.sqlite", csv_file="messages.csv", archive_old=False, preserve_data=True):
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
        self.msg_batch = []
        self.part_batch = []
        self.addr_batch = []
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
            if content.endswith(b'</smses>\n'):
                content = content[:-9]  # Remove closing tag
            elif content.endswith(b'</smses>'):
                content = content[:-8]  # Remove closing tag
            self.xml_handle = open(self.xml_file, 'wb')
            self.xml_handle.write(content)
            logging.info(f"Appending to existing XML file: {self.xml_file}")
        else:
            # Fresh mode: create new file
            self.xml_handle = open(self.xml_file, 'wb')
            self.xml_handle.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n')
            self.xml_handle.write(b'<smses>\n')
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
            self.csv_writer.writerow(['tag', 'address', 'date', 'body', 'type', 'readable_date', 'contact_name'])
            logging.info(f"Creating fresh CSV file: {self.csv_file}")
    
    def _setup_db(self):
        """Set up database tables, preserving data if in preserve mode."""
        if self.preserve_data:
            # Create tables only if they don't exist (preserve existing data)
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    msg_hash TEXT PRIMARY KEY,
                    tag TEXT,
                    address TEXT,
                    normalized_address TEXT,
                    date TEXT,
                    normalized_date_ms INTEGER,
                    body TEXT,
                    type TEXT,
                    readable_date TEXT,
                    contact_name TEXT
                )
            ''')
            self._ensure_normalized_columns()
            
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS mms_parts (
                    msg_hash TEXT,
                    seq INTEGER,
                    ct TEXT,
                    name TEXT,
                    text TEXT,
                    data TEXT,
                    FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash)
                )
            ''')
            
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS mms_addrs (
                    msg_hash TEXT,
                    address TEXT,
                    type TEXT,
                    charset TEXT,
                    FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash)
                )
            ''')
            logging.info("Database tables ready (existing data preserved)")
        else:
            # Fresh mode: drop and recreate tables
            self.cursor.execute("DROP TABLE IF EXISTS messages")
            self.cursor.execute('''
                CREATE TABLE messages (
                    msg_hash TEXT PRIMARY KEY,
                    tag TEXT,
                    address TEXT,
                    normalized_address TEXT,
                    date TEXT,
                    normalized_date_ms INTEGER,
                    body TEXT,
                    type TEXT,
                    readable_date TEXT,
                    contact_name TEXT
                )
            ''')
            
            self.cursor.execute("DROP TABLE IF EXISTS mms_parts")
            self.cursor.execute('''
                CREATE TABLE mms_parts (
                    msg_hash TEXT,
                    seq INTEGER,
                    ct TEXT,
                    name TEXT,
                    text TEXT,
                    data TEXT,
                    FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash)
                )
            ''')
            
            self.cursor.execute("DROP TABLE IF EXISTS mms_addrs")
            self.cursor.execute('''
                CREATE TABLE mms_addrs (
                    msg_hash TEXT,
                    address TEXT,
                    type TEXT,
                    charset TEXT,
                    FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash)
                )
            ''')
            logging.info("Database schema initialized (fresh)")
        
        self.conn.commit()

    def _ensure_normalized_columns(self):
        """Add normalization columns if DB was created before this feature."""
        self.cursor.execute("PRAGMA table_info(messages)")
        existing = {row[1] for row in self.cursor.fetchall()}

        if 'normalized_address' not in existing:
            self.cursor.execute("ALTER TABLE messages ADD COLUMN normalized_address TEXT")
        if 'normalized_date_ms' not in existing:
            self.cursor.execute("ALTER TABLE messages ADD COLUMN normalized_date_ms INTEGER")
    
    def get_existing_hashes(self):
        """Load all existing message hashes from database."""
        if not os.path.exists(self.db_file):
            return set()
        
        try:
            self.cursor.execute("SELECT msg_hash FROM messages")
            hashes = {row[0] for row in self.cursor.fetchall()}
            logging.info(f"Loaded {len(hashes)} existing message hashes from database")
            return hashes
        except Exception as e:
            logging.warning(f"Could not load existing hashes: {e}")
            return set()

    def _get_msg_hash(self, elem):
        """Re-generates the same unique hash used by the processor to link tables."""
        attrib = elem.attrib
        if elem.tag == 'sms':
            key = f"sms|{attrib.get('address', '')}|{attrib.get('date', '')}|{attrib.get('body', '')}|{attrib.get('type', '')}"
        else:
            key = f"mms|{attrib.get('address', '')}|{attrib.get('date', '')}|{attrib.get('m_type', '')}"
        return hashlib.md5(key.encode('utf-8')).hexdigest()

    def write_message(self, elem):
        # 1. XML output (Direct streaming)
        self.xml_handle.write(b'  ' + ET.tostring(elem, encoding='utf-8', short_empty_elements=True).strip() + b'\n')
        
        # 2. Shared Attributes
        attr = elem.attrib
        tag = elem.tag
        msg_hash = self._get_msg_hash(elem)
        
        address = attr.get('address', '')
        date = attr.get('date', '')
        readable_date = attr.get('readable_date', '')
        contact_name = attr.get('contact_name', '')
        
        # SMS has body, MMS content is in parts
        body = attr.get('body', '[MMS]' if tag == 'mms' else '')
        msg_type = attr.get('type' if tag == 'sms' else 'm_type', '')
        normalized_address = normalize_address(address)
        normalized_date = normalize_date_ms(date)

        # 3. CSV output
        self.csv_writer.writerow([tag, address, date, body, msg_type, readable_date, contact_name])
        
        # 4. SQLite Batching
        self.msg_batch.append((
            msg_hash,
            tag,
            address,
            normalized_address,
            date,
            normalized_date,
            body,
            msg_type,
            readable_date,
            contact_name,
        ))
        
        # Handle MMS child elements (parts and addrs)
        if tag == 'mms':
            # Parts (attachments/text fragments)
            for part in elem.find('parts') if elem.find('parts') is not None else []:
                p_attr = part.attrib
                self.part_batch.append((
                    msg_hash,
                    p_attr.get('seq'),
                    p_attr.get('ct'),
                    p_attr.get('name'),
                    p_attr.get('text'),
                    p_attr.get('data') # Base64 data if present
                ))
            
            # Addresses (recipients)
            for addr in elem.find('addrs') if elem.find('addrs') is not None else []:
                a_attr = addr.attrib
                self.addr_batch.append((
                    msg_hash,
                    a_attr.get('address'),
                    a_attr.get('type'),
                    a_attr.get('charset')
                ))
        
        if len(self.msg_batch) >= self.batch_size:
            self.flush_db()

    def flush_db(self):
        if self.msg_batch:
            self.cursor.executemany(
                '''
                INSERT OR IGNORE INTO messages (
                    msg_hash, tag, address, normalized_address, date, normalized_date_ms,
                    body, type, readable_date, contact_name
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ''',
                self.msg_batch,
            )
            self.msg_batch = []
        if self.part_batch:
            self.cursor.executemany('INSERT INTO mms_parts VALUES (?,?,?,?,?,?)', self.part_batch)
            self.part_batch = []
        if self.addr_batch:
            self.cursor.executemany('INSERT INTO mms_addrs VALUES (?,?,?,?)', self.addr_batch)
            self.addr_batch = []
        self.conn.commit()

    def close(self):
        self.flush_db()
        self.xml_handle.write(b'</smses>\n')
        self.xml_handle.close()
        self.csv_handle.close()
        self.conn.commit()
        self.conn.close()
