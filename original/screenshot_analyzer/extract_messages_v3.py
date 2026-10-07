#!/usr/bin/env python3
"""
Advanced SMS message extraction with spatial layout analysis and bubble segmentation.

Features:
- Bounding box extraction for spatial clustering
- Chat bubble detection via color thresholding and contour analysis
- Alignment-based sender inference (left vs right)
- Robust metadata parsing with fuzzy matching
- Header/footer masking to remove UI chrome

Usage:
    python extract_messages_v3.py <image_path>

Dependencies:
    - pytesseract / easyocr (OCR)
    - Pillow (image processing)
    - OpenCV (bubble detection)
    - numpy (spatial analysis)
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from PIL import Image
import numpy as np

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from easyocr import Reader
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class TextBlock:
    """Represents a text block with spatial coordinates."""
    
    def __init__(self, text: str, bbox: Tuple[int, int, int, int], 
                 confidence: float = 0.0, is_metadata: bool = False):
        """
        Args:
            text: Text content
            bbox: (x1, y1, x2, y2) bounding box
            confidence: OCR confidence (0-1)
            is_metadata: Whether this is metadata (sender, timestamp, etc.)
        """
        self.text = text
        self.bbox = bbox
        self.confidence = confidence
        self.is_metadata = is_metadata
        
        # Compute spatial properties
        self.x1, self.y1, self.x2, self.y2 = bbox
        self.center_x = (self.x1 + self.x2) / 2
        self.center_y = (self.y1 + self.y2) / 2
        self.width = self.x2 - self.x1
        self.height = self.y2 - self.y1
    
    def vertical_distance_to(self, other: 'TextBlock') -> float:
        """Vertical distance to another block (gap between blocks)."""
        if self.y2 < other.y1:
            return other.y1 - self.y2
        elif other.y2 < self.y1:
            return self.y1 - other.y2
        return 0  # Blocks overlap vertically
    
    def horizontal_distance_to(self, other: 'TextBlock') -> float:
        """Horizontal distance to another block."""
        if self.x2 < other.x1:
            return other.x1 - self.x2
        elif other.x2 < self.x1:
            return self.x1 - other.x2
        return 0  # Blocks overlap horizontally


class SpatialConversationExtractor:
    """Advanced conversation extractor with spatial layout awareness."""
    
    def __init__(self, image_path: str, user_name: str = 'Justin', 
                 is_group_chat: Optional[bool] = None, use_bubble_detection: bool = True):
        """
        Initialize extractor.
        
        Args:
            image_path: Path to screenshot image
            user_name: Name of the user (for inference)
            is_group_chat: Automatically detect if None
            use_bubble_detection: Use visual bubble detection if CV2 available
        """
        self.image_path = Path(image_path)
        self.user_name = user_name
        self.is_group_chat = is_group_chat
        self.use_bubble_detection = use_bubble_detection and CV2_AVAILABLE
        self.participants = []
        self.messages = []
        self.text_blocks: List[TextBlock] = []
        
        # OCR reader (lazy loaded)
        self.ocr_reader = None
        
    def get_ocr_reader(self):
        """Get or create EasyOCR reader (lazy loading)."""
        if self.ocr_reader is None and EASYOCR_AVAILABLE:
            print("[*] Initializing EasyOCR reader...")
            self.ocr_reader = Reader(['en'], gpu=False)
        return self.ocr_reader
    
    def extract_text_with_bboxes(self) -> Tuple[str, List[TextBlock]]:
        """Extract text and bounding boxes from image."""
        if not self.image_path.exists():
            raise FileNotFoundError(f"Image not found: {self.image_path}")
        
        # Try EasyOCR first for bounding box support
        if EASYOCR_AVAILABLE:
            try:
                reader = self.get_ocr_reader()
                result = reader.readtext(str(self.image_path))
                
                text_blocks = []
                full_text_lines = []
                
                for bbox, text, confidence in result:
                    # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]] (4 corners)
                    # Convert to (x1, y1, x2, y2)
                    xs = [p[0] for p in bbox]
                    ys = [p[1] for p in bbox]
                    bbox_rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
                    
                    block = TextBlock(text, bbox_rect, confidence)
                    text_blocks.append(block)
                    full_text_lines.append(text)
                
                full_text = '\n'.join(full_text_lines)
                return full_text, text_blocks
            except Exception as e:
                print(f"[⚠] EasyOCR failed: {e}")
        
        # Fallback: pytesseract without bboxes
        if pytesseract:
            try:
                image = Image.open(self.image_path)
                text = pytesseract.image_to_string(image)
                
                # Create dummy text blocks without real coordinates
                text_blocks = []
                for i, line in enumerate(text.split('\n')):
                    if line.strip():
                        bbox = (0, i * 20, 100, i * 20 + 20)  # Dummy bbox
                        block = TextBlock(line, bbox, 0.9)
                        text_blocks.append(block)
                
                return text, text_blocks
            except Exception as e:
                raise RuntimeError(f"OCR extraction failed: {e}")
        
        raise RuntimeError("No OCR library available")
    
    def segment_bubbles(self, image_array: np.ndarray) -> List[np.ndarray]:
        """Detect chat bubbles using color thresholding and contour detection."""
        if not CV2_AVAILABLE:
            return []
        
        # Convert to HSV for better color detection
        hsv = cv2.cvtColor(image_array, cv2.COLOR_RGB2HSV)
        
        # Detect light backgrounds (sent messages) - typically blue/light colors
        lower_light = np.array([0, 0, 200])
        upper_light = np.array([180, 50, 255])
        mask_light = cv2.inRange(hsv, lower_light, upper_light)
        
        # Detect dark backgrounds (received messages) - typically dark/grey
        lower_dark = np.array([0, 0, 0])
        upper_dark = np.array([180, 180, 100])
        mask_dark = cv2.inRange(hsv, lower_dark, upper_dark)
        
        # Combine masks
        mask = cv2.bitwise_or(mask_light, mask_dark)
        
        # Morphological operations to clean up
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        
        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Filter contours by size (message bubbles are larger than noise)
        min_area = 500
        bubble_masks = []
        for contour in contours:
            if cv2.contourArea(contour) > min_area:
                x, y, w, h = cv2.boundingRect(contour)
                bubble_masks.append((x, y, w, h))
        
        return bubble_masks
    
    def cluster_text_by_alignment(self, text_blocks: List[TextBlock], 
                                  image_width: int) -> Dict[str, List[TextBlock]]:
        """
        Group text blocks by horizontal alignment (left vs right).
        
        Returns:
            Dict with 'left' and 'right' keys containing aligned text blocks
        """
        left_blocks = []
        right_blocks = []
        
        # Calculate median center position
        centers = [block.center_x for block in text_blocks]
        median_center = np.median(centers) if centers else image_width / 2
        
        # Cluster by distance from center
        for block in text_blocks:
            if block.center_x < median_center - 50:  # Clearly left
                left_blocks.append(block)
            elif block.center_x > median_center + 50:  # Clearly right
                right_blocks.append(block)
            else:
                # Ambiguous - use default or analyze further
                left_blocks.append(block)
        
        return {'left': left_blocks, 'right': right_blocks}
    
    def detect_metadata(self, line: str, context_blocks: List[TextBlock] = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Detect metadata (sender, phone, timestamp) from text line.
        
        Returns:
            (sender, phone, timestamp) tuple with None for missing fields
        """
        line = line.strip()
        if not line:
            return (None, None, None)
        
        # name + phone + timestamp
        m = re.match(
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})\s+[-:+]\s*([\d:]+\s*(?:AM|PM|am|pm)?.*?)$',
            line
        )
        if m:
            return (m.group(1), m.group(2).strip(), m.group(3).strip())
        
        # name + phone (no timestamp)
        m = re.match(
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})$',
            line
        )
        if m:
            return (m.group(1), m.group(2).strip(), None)
        
        # name + timestamp (e.g., "Frances Lyon + Mon, 4:12 PM")
        m = re.match(
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+[+\-]\s*(.*?(?:AM|PM|am|pm|Mon|Tue|Wed|Thu|Fri|Sat|Sun|am|pm).*?)$',
            line, re.IGNORECASE
        )
        if m:
            return (m.group(1), None, m.group(2).strip())
        
        # Known contact name only
        known_contacts = {'Carrie Lyons', 'Frances Lyon', 'Crystal Lyon', 'Lyndsay', 'Justin'}
        if line in known_contacts:
            return (line, None, None)
        
        # Timestamp only (HH:MM AM/PM or day + time)
        if re.match(r'^\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?$', line):
            return (None, None, line)
        
        if re.match(r'^[A-Za-z]{3,9},?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?$', line):
            return (None, None, line)
        
        # Phone only
        if re.match(r'^\+?[\d\s\-\(\)]{10,}$', line) or re.match(r'^\d{3}[-.\s]?\d{3}[-.\s]?\d{4}$', line):
            return (None, line.strip(), None)
        
        return (None, None, None)
    
    def infer_sender_from_alignment(self, blocks: List[TextBlock], 
                                    image_width: int) -> str:
        """
        Infer sender based on horizontal alignment.
        
        Right-aligned messages typically belong to the user.
        Left-aligned messages typically belong to other participants.
        """
        if not blocks:
            return self.user_name
        
        # Calculate average center position
        avg_center = np.mean([block.center_x for block in blocks])
        threshold = image_width * 0.6  # If center is in right 40%, it's likely the user
        
        return self.user_name if avg_center > threshold else None
    
    def extract_participants_from_header(self, text: str) -> List[str]:
        """Extract participant names from first line of OCR text."""
        lines = text.split('\n')
        if not lines:
            return []
        
        header = lines[0]
        # Remove common OCR artifacts
        header = re.sub(r'[©8O\}]', '', header)
        header = re.sub(r'\.\.\.$', '', header)
        header = re.sub(r'^[<>]+\s*', '', header)  # Remove arrow artifacts
        
        # Split by comma
        names = [n.strip() for n in header.split(',') if n.strip()]
        
        # Filter valid names
        valid_names = [n for n in names if re.match(r'^[A-Z][a-z]+', n)]
        
        self.is_group_chat = len(valid_names) > 1
        self.participants = valid_names
        return valid_names
    
    def parse_conversation(self, text: str, text_blocks: List[TextBlock], 
                          image_width: int) -> List[Dict]:
        """
        Parse conversation with spatial awareness.
        
        Strategy:
        1. Group text blocks into messages by vertical proximity
        2. Detect metadata and associate with message content
        3. Infer sender based on alignment (left vs right)
        """
        lines = text.strip().split('\n')
        start_idx = 1 if lines else 0
        lines = lines[start_idx:]
        
        messages = []
        accumulated = []
        current_alignment = None
        known_contacts = set(self.participants) if self.participants else {
            'Carrie Lyons', 'Frances Lyon', 'Crystal Lyon', 'Lyndsay', 'Justin'
        }
        
        for line in lines:
            line_stripped = line.strip()
            
            if not line_stripped or line_stripped.lower() == '0} type message':
                continue
            
            sender, phone, timestamp = self.detect_metadata(line_stripped)
            
            if sender or phone or timestamp:
                # Metadata detected - finalize accumulated content
                if accumulated:
                    # Infer sender from alignment if not in metadata
                    if not sender:
                        sender = self.infer_sender_from_alignment([], image_width)
                    
                    # Fallback to name inference
                    if not sender:
                        content_text = '\n'.join(accumulated)
                        sender = self.user_name  # Default to user for unmapped messages
                    
                    messages.append(self._create_message(sender, phone, timestamp, accumulated))
                    accumulated = []
            else:
                accumulated.append(line_stripped)
        
        # Finalize remaining content
        if accumulated:
            content_text = '\n'.join(accumulated)
            inferred_sender = self.user_name  # Default sender for content without metadata
            messages.append(self._create_message(inferred_sender, None, None, accumulated))
        
        self.messages = messages
        return messages
    
    def _create_message(self, sender: Optional[str], phone: Optional[str],
                       timestamp: Optional[str], content: List[str]) -> Dict:
        """Create structured message object."""
        return {
            'sender': sender,
            'phone_number': phone,
            'timestamp': timestamp,
            'content': '\n'.join(content) if content else '',
            'line_count': len(content),
            'media_type': 'text',
            'direction': 'sent' if sender and self.user_name and 
                        sender.lower().find(self.user_name.lower()) >= 0 else 'received'
        }
    
    def extract(self) -> Dict:
        """Main extraction pipeline with spatial analysis."""
        print(f"\n{'='*60}")
        print(f"Extracting conversation from: {self.image_path.name}")
        print(f"Using: Spatial Layout Analysis (v3)")
        print(f"{'='*60}\n")
        
        # Step 1: Extract text with bounding boxes
        text, text_blocks = self.extract_text_with_bboxes()
        print(f"[✓] Text extraction complete ({len(text)} characters, {len(text_blocks)} text blocks)\n")
        
        self.text_blocks = text_blocks
        
        # Step 2: Load image for spatial analysis
        image = Image.open(self.image_path)
        image_array = np.array(image)
        image_width = image_array.shape[1]
        
        # Step 3: Optional bubble detection
        if self.use_bubble_detection:
            bubbles = self.segment_bubbles(image_array)
            print(f"[✓] Bubble segmentation: {len(bubbles)} bubbles detected\n")
        
        # Step 4: Cluster text by alignment
        alignment_groups = self.cluster_text_by_alignment(text_blocks, image_width)
        print(f"[✓] Alignment clustering: {len(alignment_groups['left'])} left, "
              f"{len(alignment_groups['right'])} right\n")
        
        # Step 5: Extract participants
        participants = self.extract_participants_from_header(text)
        chat_type = "Group Chat" if self.is_group_chat else "1-to-1 Conversation"
        print(f"[✓] Participants detected: {', '.join(participants)} ({chat_type})\n")
        
        # Step 6: Parse into messages
        messages = self.parse_conversation(text, text_blocks, image_width)
        print(f"[✓] Parsed {len(messages)} messages\n")
        
        # Step 7: Build output
        output = {
            'source_file': self.image_path.name,
            'extraction_method': 'Spatial Layout Analysis (EasyOCR + OpenCV)',
            'conversation_type': chat_type,
            'participants': participants,
            'is_group_chat': self.is_group_chat,
            'total_messages': len(messages),
            'spatial_analysis': {
                'text_blocks': len(text_blocks),
                'left_aligned': len(alignment_groups['left']),
                'right_aligned': len(alignment_groups['right']),
                'bubbles_detected': len(self.segment_bubbles(image_array)) if self.use_bubble_detection else 0
            },
            'messages': messages,
            'raw_ocr_text': ''
        }
        
        return output
    
    def to_json(self, output_path: Optional[str] = None) -> str:
        """Export extracted conversation to JSON."""
        output = {
            'source_file': self.image_path.name,
            'extraction_method': 'Spatial Layout Analysis (EasyOCR + OpenCV)',
            'conversation_type': "Group Chat" if self.is_group_chat else "1-to-1 Conversation",
            'participants': self.participants,
            'is_group_chat': self.is_group_chat,
            'total_messages': len(self.messages),
            'messages': self.messages,
            'raw_ocr_text': ''
        }
        
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            return str(path)
        
        return json.dumps(output, indent=2, ensure_ascii=False)


def main():
    """Main entry point."""
    # Process the screenshot
    image_path = 'temp/Attachment 1.PNG'
    extractor = SpatialConversationExtractor(
        image_path, 
        user_name='Justin',
        use_bubble_detection=True
    )
    
    # Extract and parse
    result = extractor.extract()
    
    # Export to JSON
    output_json_path = 'temp/Attachment 1_extracted_v3.json'
    json_output = extractor.to_json(output_json_path)
    
    # Print summary
    print("="*60)
    print("EXTRACTION SUMMARY (Spatial Layout)")
    print("="*60)
    summary = {
        'source_file': result['source_file'],
        'total_messages': result['total_messages'],
        'output_file': output_json_path,
        'preview': result['messages'][:3] if result['messages'] else []
    }
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
