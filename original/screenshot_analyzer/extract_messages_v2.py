#!/usr/bin/env python3
"""
Extract and parse SMS messages from conversation screenshots.

Usage:
    python extract_messages.py <image_path>

Dependencies:
    - pytesseract (wrapper for Tesseract OCR)
    - Pillow (image processing)
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set
from PIL import Image

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from easyocr import Reader
except ImportError:
    Reader = None


class ConversationExtractor:
    """Extract SMS conversation from screenshot using OCR."""
    
    def __init__(self, image_path: str, user_name: str = 'Justin', is_group_chat: bool = None):
        """
        Initialize extractor.
        
        Args:
            image_path: Path to screenshot image
            user_name: Name of the user (for inference)
            is_group_chat: Automatically detect if None
        """
        self.image_path = Path(image_path)
        self.user_name = user_name
        self.is_group_chat = is_group_chat
        self.participants = []
        self.messages = []
    
    def extract_text_from_image(self) -> str:
        """Extract text from image using pytesseract with EasyOCR fallback."""
        if not self.image_path.exists():
            raise FileNotFoundError(f"Image not found: {self.image_path}")
        
        # Try pytesseract first
        if pytesseract:
            try:
                image = Image.open(self.image_path)
                text = pytesseract.image_to_string(image)
                if text.strip():
                    return text
            except Exception as e:
                print(f"[⚠] pytesseract failed: {e}")
        
        # Fallback to EasyOCR
        if Reader:
            try:
                reader = Reader(['en'])
                result = reader.readtext(str(self.image_path))
                text = '\n'.join([line[1] for line in result])
                return text
            except Exception as e:
                raise RuntimeError(f"Both OCR methods failed: {e}")
        
        raise RuntimeError("No OCR library available (pytesseract or easyocr)")
    
    def extract_participants_from_header(self, text: str) -> List[str]:
        """Extract participant names from first line of OCR text."""
        lines = text.split('\n')
        if not lines:
            return []
        
        header = lines[0]
        # Remove common OCR artifacts
        header = re.sub(r'[©8O\}]', '', header)  # Remove emoji-like chars
        header = re.sub(r'\.\.\.$', '', header)  # Remove trailing ...
        
        # Split by comma
        names = [n.strip() for n in header.split(',') if n.strip()]
        
        # Filter valid names (at least one capital letter followed by lowercase)
        valid_names = [n for n in names if re.match(r'^[A-Z][a-z]+', n)]
        
        # Determine if group chat
        self.is_group_chat = len(valid_names) > 1
        self.participants = valid_names
        return valid_names
    
    def infer_sender_from_content(self, content: str, known_contacts: Set[str]) -> Optional[str]:
        """Infer sender when not explicitly provided."""
        # Everything without explicit sender metadata is from the user
        return self.user_name
    
    def parse_conversation(self, text: str) -> List[Dict]:
        """Parse OCR text: accumulate content blocks until metadata, then create message."""
        lines = text.strip().split('\n')
        start_idx = 1 if lines else 0
        lines = lines[start_idx:]
        
        messages = []
        accumulated = []  # List of non-empty, non-metadata lines
        known_contacts = set(self.participants) if self.participants else {
            'Carrie Lyons', 'Frances Lyon', 'Crystal Lyon', 'Lyndsay', 'Justin'
        }
        
        def detect_metadata(line):
            """Returns (sender, phone, timestamp) if metadata, else (None, None, None)"""
            # name + phone + timestamp
            m = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})\s+[-:+]\s*([\d:]+\s*(?:AM|PM|am|pm)?.*?)$', line)
            if m: return (m.group(1), m.group(2), m.group(3))
            # name + phone
            m = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})$', line)
            if m: return (m.group(1), m.group(2), None)
            # name + timestamp
            m = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+[+\-]\s*(.*?(?:AM|PM|am|pm|Mon|Tue|Wed|Thu|Fri|Sat|Sun).*?)$', line)
            if m: return (m.group(1), None, m.group(2))
            # known contact name only
            if line in known_contacts: return (line, None, None)
            # timestamp only
            if re.match(r'^\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?$', line): return (None, None, line)
            # phone only
            if re.match(r'^\+?[\d\s\-\(\)]{10,}$', line) or re.match(r'^\d{3}[-.\s]?\d{3}[-.\s]?\d{4}$', line):
                return (None, line, None)
            return (None, None, None)
        
        for line in lines:
            line_stripped = line.strip()
            
            if not line_stripped or line_stripped.lower() == '0} type message':
                # Blank line - skip
                continue
            
            sender, phone, timestamp = detect_metadata(line_stripped)
            
            if sender or phone or timestamp:
                # Metadata line - finalize accumulated content
                if accumulated:
                    # Infer sender from content if not explicitly in metadata
                    if not sender:
                        content_text = '\n'.join(accumulated)
                        sender = self.infer_sender_from_content(content_text, known_contacts)
                    messages.append(self._create_message(sender, phone, timestamp, accumulated))
                    accumulated = []
            else:
                # Content line - accumulate
                accumulated.append(line_stripped)
        
        # Finalize any remaining content
        if accumulated:
            content_text = '\n'.join(accumulated)
            inferred_sender = self.infer_sender_from_content(content_text, known_contacts)
            messages.append(self._create_message(inferred_sender, None, None, accumulated))
        
        self.messages = messages
        return messages
    
    def _create_message(self, sender: Optional[str], phone: Optional[str], 
                       timestamp: Optional[str], content: List[str]) -> Dict:
        """Create a structured message object."""
        return {
            'sender': sender,
            'phone_number': phone,
            'timestamp': timestamp,
            'content': '\n'.join(content) if content else '',
            'line_count': len(content),
            'media_type': 'text',
            'direction': 'sent' if sender and self.user_name and sender.lower().find(self.user_name.lower()) >= 0 else 'received'
        }
    
    def extract(self) -> Dict:
        """Main extraction pipeline."""
        print(f"\n{'='*60}")
        print(f"Extracting conversation from: {self.image_path.name}")
        print(f"{'='*60}\n")
        
        # Step 1: Extract text
        text = self.extract_text_from_image()
        print(f"[✓] Text extraction complete ({len(text)} characters)\n")
        
        # Step 2: Extract participants from header
        participants = self.extract_participants_from_header(text)
        chat_type = "Group Chat" if self.is_group_chat else "1-to-1 Conversation"
        print(f"[✓] Participants detected: {', '.join(participants)} ({chat_type})\n")
        
        # Step 3: Parse into messages
        messages = self.parse_conversation(text)
        print(f"[✓] Parsed {len(messages)} messages\n")
        
        # Step 4: Build output
        output = {
            'source_file': self.image_path.name,
            'extraction_method': 'OCR (pytesseract)',
            'conversation_type': chat_type,
            'participants': participants,
            'is_group_chat': self.is_group_chat,
            'total_messages': len(messages),
            'messages': messages,
            'raw_ocr_text': text
        }
        
        return output
    
    def to_json(self, output_path: Optional[str] = None) -> str:
        """Export extracted conversation to JSON."""
        output = {
            'source_file': self.image_path.name,
            'extraction_method': 'OCR (pytesseract)',
            'conversation_type': "Group Chat" if self.is_group_chat else "1-to-1 Conversation",
            'participants': self.participants,
            'is_group_chat': self.is_group_chat,
            'total_messages': len(self.messages),
            'messages': self.messages,
            'raw_ocr_text': ''  # Don't include raw text in JSON for brevity
        }
        
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            return str(path)
        
        return json.dumps(output, indent=2, ensure_ascii=False)


def main():
    # Process the screenshot
    image_path = 'temp/Attachment 1.PNG'
    extractor = ConversationExtractor(image_path, user_name='Justin')
    
    # Extract and parse
    result = extractor.extract()
    
    # Export to JSON
    output_json_path = 'temp/Attachment 1_extracted.json'
    json_output = extractor.to_json(output_json_path)
    
    # Print summary
    print("="*60)
    print("EXTRACTION SUMMARY")
    print("="*60)
    summary = {
        'source_file': result['source_file'],
        'total_messages': result['total_messages'],
        'output_file': output_json_path,
        'preview': [msg for msg in result['messages'][:3]]
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
