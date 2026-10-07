import hashlib

class CallProcessor:
    def __init__(self, existing_hashes=None):
        """Initialize processor, optionally with existing call hashes.
        
        Args:
            existing_hashes: Set of MD5 hashes from previously imported calls.
                           If provided, duplicates against these will be counted.
        """
        self.seen_hashes = existing_hashes if existing_hashes else set()
        self.existing_hash_count = len(self.seen_hashes)
        self.total_processed = 0
        self.unique_count = 0
        self.duplicate_count = 0
        self.call_count = 0

    def get_hash(self, elem):
        """Creates a composite key hash from call attributes."""
        attrib = elem.attrib
        self.call_count += 1
        
        number = attrib.get('number', '')
        date = attrib.get('date', '')
        duration = attrib.get('duration', '')
        call_type = attrib.get('type', '')
        
        key = f"call|{number}|{date}|{duration}|{call_type}"
        return hashlib.md5(key.encode('utf-8')).hexdigest()

    def process_element(self, elem):
        """Returns True if call is unique, updates counters."""
        self.total_processed += 1
        call_hash = self.get_hash(elem)
        
        if call_hash not in self.seen_hashes:
            self.seen_hashes.add(call_hash)
            self.unique_count += 1
            return True
        else:
            self.duplicate_count += 1
            return False
