import hashlib

class MessageProcessor:
    def __init__(self, existing_hashes=None):
        """Initialize processor, optionally with existing message hashes.
        
        Args:
            existing_hashes: Set of MD5 hashes from previously imported messages.
                           If provided, duplicates against these will be counted.
        """
        self.seen_hashes = existing_hashes if existing_hashes else set()
        self.existing_hash_count = len(self.seen_hashes)
        self.total_processed = 0
        self.unique_count = 0
        self.duplicate_count = 0
        self.sms_count = 0
        self.mms_count = 0

    def get_hash(self, elem):
        """Creates a composite key hash from SMS or MMS message attributes."""
        attrib = elem.attrib
        if elem.tag == 'sms':
            self.sms_count += 1
            address = attrib.get('address', '')
            date = attrib.get('date', '')
            body = attrib.get('body', '')
            msg_type = attrib.get('type', '')
            key = f"sms|{address}|{date}|{body}|{msg_type}"
        else:  # mms
            self.mms_count += 1
            address = attrib.get('address', '')
            date = attrib.get('date', '')
            m_type = attrib.get('m_type', '')
            key = f"mms|{address}|{date}|{m_type}"
            
        return hashlib.md5(key.encode('utf-8')).hexdigest()

    def process_element(self, elem):
        """Returns True if message is unique, updates counters."""
        self.total_processed += 1
        msg_hash = self.get_hash(elem)
        
        if msg_hash not in self.seen_hashes:
            self.seen_hashes.add(msg_hash)
            self.unique_count += 1
            return True
        else:
            self.duplicate_count += 1
            return False
