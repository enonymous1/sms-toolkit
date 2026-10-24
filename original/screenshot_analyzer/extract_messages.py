"""
Screenshot conversation extractor - extracts message conversations from screenshots into structured JSON.
Handles left-aligned (received) and right-aligned (sent) messages with metadata parsing.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional
from PIL import Image
import pytesseract

class ConversationExtractor:
    def __init__(self, image_path: str, user_name: str = 'Justin'):
        self.image_path = Path(image_path)
        self.image = Image.open(self.image_path)
        self.raw_text = None
        self.messages = []
        self.participants = []
        self.is_group_chat = False
        self.user_name = user_name  # Name of the user sending messages (for right-side msgs)
        
    def extract_text_from_image(self) -> str:
        """Extract raw text from image using OCR."""
        try:
            print(f"Attempting OCR extraction from {self.image_path.name}...")
            text = pytesseract.image_to_string(self.image)
            self.raw_text = text
            return text
        except Exception as e:
            print(f"Error during OCR: {e}")
            print("Attempting alternative method with EasyOCR...")
            try:
                import easyocr
                reader = easyocr.Reader(['en'])
                result = reader.readtext(str(self.image_path))
                text = '\n'.join([line[1] for line in result])
                self.raw_text = text
                return text
            except Exception as e2:
                print(f"EasyOCR also failed: {e2}")
                raise
    
    def extract_participants_from_header(self, text: str) -> List[str]:
        """
        Extract participant names from the conversation header.
        Header format: [arrow] Name1, Name2, ... [emoji] [menu]
        Returns list of participant names.
        """
        first_line = text.split('\n')[0].strip() if text else ""
        
        # Remove common OCR artifacts and UI elements
        # Remove leading arrow/caret (often parsed as Y, >, v, etc.)
        header = re.sub(r'^[Y>v\^↓]|\s+$', '', first_line)
        
        # Remove trailing emoji/copyright/menu symbols (© 8 0} etc.)
        header = re.sub(r'©|8|0\}|☰|⋮|\s+\.\.\.\s+$', '', header)
        
        # Extract names (capitalized words)
        # Handle truncation like "Lyndsay M..."
        names = []
        parts = header.split(',')
        
        for part in parts:
            part = part.strip()
            # Remove ellipsis and emoji remnants
            part = re.sub(r'\s*\.{3}\s*|©|8|0\}', '', part)
            part = re.sub(r'\s+', ' ', part).strip()  # Clean whitespace
            
            if part and len(part) > 1:  # Must be longer than 1 char
                names.append(part)
        
        self.participants = names
        self.is_group_chat = len(names) > 1
        return names
    
    def infer_sender_from_content(self, content: str, known_contacts: set) -> Optional[str]:
        """
        Infer sender if not explicitly provided.
        Messages without metadata are likely from the user (right-aligned in UI).
        Heuristics:
        - If message starts with known contact name + comma (addressing someone), it's from user
        - If message has no metadata and no addressing, it's probably from user
        """
        content_start = content.split('\n')[0].lower() if content else ''
        
        # Check if addressing a known contact (user responding to them)
        for contact in known_contacts:
            if content_start.startswith(contact.lower()):
                return self.user_name
        
        # Messages without sender metadata are typically user's messages (right-aligned)
        return self.user_name
    
    def parse_conversation(self, text: str) -> List[Dict]:
        """
        Parse OCR text into structured message objects.
        Enhanced to handle multi-speaker message bubbles with embedded metadata.
        
        Key insight: Metadata (sender, phone, timestamp) BELOW/WITHIN content marks message boundaries.
        Blank lines in OCR separate distinct message bubbles.
        
        Message structure in screenshot:
        [Message content in bubble]
        [Metadata text on thread background]
        
        Parsing flow:
        1. Split text into blocks by blank lines (each block is a potential message)
        2. Accumulate content lines within each block
        3. When metadata is found -> create message from preceding block content
        4. Reset for next message
        """
        lines = text.strip().split('\n')
        
        # Skip header line (first line with participant names)
        start_idx = 1 if lines else 0
        lines = lines[start_idx:]
        
        messages = []
        current_content = []
        
        # Use extracted participants as known contacts
        known_contacts = set(self.participants) if self.participants else {
            'Carrie Lyons', 'Frances Lyon', 'Crystal Lyon', 'Lyndsay', 'Justin'
        }
        
        def _detect_and_parse_metadata(line: str, known_contacts: set) -> tuple:
            """
            Detect metadata pattern in a line.
            Returns: (match_type, sender, phone, timestamp) or (None, None, None, None)
            """
            # Pattern 1: name + phone + timestamp
            # e.g., "Carrie Lyons 555-010-1234 - 8:35 PM"
            name_phone_time_match = re.match(
                r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})\s+[-:+]\s*([\d:]+\s*(?:AM|PM|am|pm)?.*?)$',
                line
            )
            if name_phone_time_match:
                return ('name_phone_time', name_phone_time_match.group(1), 
                       name_phone_time_match.group(2), name_phone_time_match.group(3))
            
            # Pattern 2: name + phone (without timestamp)
            # e.g., "Carrie Lyons 555-010-1234"
            name_phone_match = re.match(
                r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})$',
                line
            )
            if name_phone_match:
                return ('name_phone', name_phone_match.group(1), 
                       name_phone_match.group(2), None)
            
            # Pattern 3: name + timestamp
            # e.g., "Frances Lyon + Mon, 4:12 PM"
            name_time_match = re.match(
                r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+[+\-]\s*(.*?(?:AM|PM|am|pm|Mon|Tue|Wed|Thu|Fri|Sat|Sun).*?)$',
                line
            )
            if name_time_match:
                return ('name_time', name_time_match.group(1), None, name_time_match.group(2))
            
            # Pattern 4: just a known contact name (metadata without time/phone)
            # e.g., "Crystal Lyon"
            if line in known_contacts:
                return ('name_only', line, None, None)
            
            # Pattern 5: standalone timestamp only
            # e.g., "9:41 PM"
            if re.match(r'^\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?$', line):
                return ('timestamp_only', None, None, line)
            
            # Pattern 6: phone number alone
            if (re.match(r'^\+?[\d\s\-\(\)]{10,}$', line) or 
                re.match(r'^\d{3}[-.\s]?\d{3}[-.\s]?\d{4}$', line)):
                return ('phone_only', None, line, None)
            
            return (None, None, None, None)
        
        # Process lines, tracking content blocks separated by blank lines
        # Then group blocks based on metadata patterns
        content_blocks = []  # List of (content_lines, has_metadata, sender, phone, timestamp)
        current_block = []
        
        for line in lines:
            line = line.strip()
            
            if not line or line.lower() == '0} type message':
                # Blank line - mark end of current content block
                if current_block:
                    content_blocks.append(current_block)
                    current_block = []
                continue
            
            current_block.append(line)
        
        # Don't forget the last block
        if current_block:
            content_blocks.append(current_block)
        
        # Now group blocks and metadata together
        # Accumulate content blocks until we hit one with metadata
        accumulated_blocks = []  # List of blocks (each block is a list of lines)
        
        for block in content_blocks:
            # Check if last line of block is metadata
            last_line = block[-1]
            match_type, sender, phone, timestamp = _detect_and_parse_metadata(last_line, known_contacts)
            
            if match_type:
                # This block ends with metadata
                # The metadata applies to accumulated content + this block's content
                # But if we have very long accumulated content + very short current content,
                # the short content likely belongs with the metadata, not earlier blocks
                
                content_lines_in_block = len(block) - 1  # -1 for the metadata line itself
                
                # Check if we should split long earlier content from short current content
                if len(accumulated_blocks) > 0 and content_lines_in_block <= 2:
                    # Count lines in accumulated vs current
                    accumulated_lines = sum(len(b) for b in accumulated_blocks)
                    
                    # If accumulated is much longer (3+ lines) than current (0-2 lines),
                    # likely they're separate messages
                    # BUT only if the accumulated content is not very short itself
                    if accumulated_lines >= 3 and accumulated_lines > content_lines_in_block * 3:
                        # Significant length difference - likely separate messages
                        # Check if earlier accumulated makes sense as a complete message
                        # by looking for known_contacts in content (indicates addressing them)
                        all_earlier = []
                        for earlier_block in accumulated_blocks:
                            all_earlier.extend(earlier_block)
                        
                        earlier_text = '\n'.join(all_earlier)
                        
                        # If earlier content addresses known contacts, it's likely from user (Justin)
                        # and should be separated from following Carrie/Frances content
                        earlier_inferred = self.infer_sender_from_content(earlier_text, known_contacts)
                        
                        if earlier_inferred and earlier_inferred != sender:
                            # Different inferred sender - create separate message
                            messages.append(self._create_message(earlier_inferred, None, None, all_earlier))
                            
                            # Create message from current block with metadata
                            current_content = block[:-1]
                            if current_content:
                                if match_type in ('timestamp_only', 'phone_only'):
                                    content_text = '\n'.join(current_content)
                                    inferred_sender = self.infer_sender_from_content(content_text, known_contacts)
                                    messages.append(self._create_message(inferred_sender, phone, timestamp, current_content))
                                else:
                                    messages.append(self._create_message(sender, phone, timestamp, current_content))
                            
                            accumulated_blocks = []
                            continue
                
                # Standard case: accumulated blocks + current block belong together
                all_content = []
                for b in accumulated_blocks:
                    all_content.extend(b)
                all_content.extend(block[:-1])  # Add current block without metadata line
                
                if all_content:  # Only create message if there's content
                    if match_type in ('timestamp_only', 'phone_only'):
                        content_text = '\n'.join(all_content)
                        inferred_sender = self.infer_sender_from_content(content_text, known_contacts)
                        messages.append(self._create_message(inferred_sender, phone, timestamp, all_content))
                    else:
                        messages.append(self._create_message(sender, phone, timestamp, all_content))
                
                accumulated_blocks = []
            else:
                # No metadata, accumulate this block
                accumulated_blocks.append(block)
        
        # Handle any remaining accumulated blocks (messages without metadata)
        if accumulated_blocks:
            all_remaining = []
            for b in accumulated_blocks:
                all_remaining.extend(b)
            
            if all_remaining:
                content_text = '\n'.join(all_remaining)
                inferred_sender = self.infer_sender_from_content(content_text, known_contacts)
                messages.append(self._create_message(inferred_sender, None, None, all_remaining))
        
        self.messages = messages
        return messages
        
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
            'media_type': 'text',  # Could be enhanced to detect images
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
            'raw_ocr_text': self.raw_text
        }
        
        json_str = json.dumps(output, indent=2, ensure_ascii=False)
        
        if output_path:
            Path(output_path).write_text(json_str, encoding='utf-8')
            print(f"[✓] JSON exported to: {output_path}")
        
        return json_str


def main():
    # Process the screenshot
    image_path = 'temp/Attachment 1.PNG'
    extractor = ConversationExtractor(image_path, user_name='Justin')  # Specify the user's name
    
    # Extract and parse
    result = extractor.extract()
    
    # Export to JSON
    output_json_path = 'temp/Attachment 1_extracted.json'
    json_output = extractor.to_json(output_json_path)
    
    # Also print summary
    print("\n" + "="*60)
    print("EXTRACTION SUMMARY")
    print("="*60)
    print(json.dumps({
        'source_file': result['source_file'],
        'total_messages': result['total_messages'],
        'output_file': output_json_path,
        'preview': result['messages'][:3] if result['messages'] else []
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
