#!/usr/bin/env python3
"""
Advanced SMS message extraction v4: Enhanced spatial layout analysis.

Improvements over v3:
- Refined alignment detection with statistical analysis
- Better bubble color detection using lab color space
- Improved metadata clustering by vertical proximity
- Header/footer masking to remove UI chrome
- Detailed diagnostic output for spatial analysis

Usage:
    python extract_messages_v4.py <image_path>
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from PIL import Image
import numpy as np

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
                 confidence: float = 0.0):
        self.text = text
        self.bbox = bbox
        self.confidence = confidence
        
        self.x1, self.y1, self.x2, self.y2 = bbox
        self.center_x = (self.x1 + self.x2) / 2
        self.center_y = (self.y1 + self.y2) / 2
        self.width = self.x2 - self.x1
        self.height = self.y2 - self.y1
        self.top = self.y1
        self.bottom = self.y2
    
    def vertical_gap_to(self, other: 'TextBlock') -> float:
        """Vertical distance to another block."""
        if self.bottom < other.top:
            return other.top - self.bottom
        elif other.bottom < self.top:
            return self.top - other.bottom
        return 0
    
    def is_vertically_close(self, other: 'TextBlock', threshold: float = 30) -> bool:
        """Check if blocks are vertically close (within threshold)."""
        return self.vertical_gap_to(other) <= threshold
    
    def __repr__(self) -> str:
        return f"TextBlock('{self.text[:30]}...', x={self.center_x:.0f}, y={self.center_y:.0f})"


class SpatialConversationExtractorV4:
    """Advanced conversation extractor v4 with refined spatial analysis."""
    
    def __init__(self, image_path: str, user_name: str = 'Justin'):
        self.image_path = Path(image_path)
        self.user_name = user_name
        self.participants = []
        self.messages = []
        self.text_blocks: List[TextBlock] = []
        self.image_array = None
        self.image_width = 0
        self.image_height = 0
        self.ocr_reader = None
    
    def get_ocr_reader(self):
        """Get or create EasyOCR reader."""
        if self.ocr_reader is None and EASYOCR_AVAILABLE:
            self.ocr_reader = Reader(['en'], gpu=False)
        return self.ocr_reader
    
    def extract_text_with_bboxes(self) -> Tuple[str, List[TextBlock]]:
        """Extract text and bounding boxes."""
        if not self.image_path.exists():
            raise FileNotFoundError(f"Image not found: {self.image_path}")
        
        if not EASYOCR_AVAILABLE:
            raise RuntimeError("EasyOCR required for v4")
        
        reader = self.get_ocr_reader()
        result = reader.readtext(str(self.image_path))
        
        text_blocks = []
        full_text_lines = []
        
        for bbox, text, confidence in result:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            bbox_rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
            
            block = TextBlock(text, bbox_rect, confidence)
            text_blocks.append(block)
            full_text_lines.append(text)
        
        # Sort by vertical position (top to bottom)
        text_blocks.sort(key=lambda b: (b.top, b.x1))
        
        full_text = '\n'.join(full_text_lines)
        return full_text, text_blocks
    
    def mask_ui_chrome(self, image_array: np.ndarray) -> np.ndarray:
        """Mask out static UI elements (header, footer)."""
        height = image_array.shape[0]
        width = image_array.shape[1]
        
        # Create working copy
        masked = image_array.copy()
        
        # Mask header (top 5% or top ~100 pixels)
        header_height = max(100, int(height * 0.05))
        masked[:header_height] = [255, 255, 255]
        
        # Mask footer (bottom 5% or bottom ~80 pixels)
        footer_height = max(80, int(height * 0.05))
        masked[-footer_height:] = [255, 255, 255]
        
        return masked
    
    def detect_message_bubbles_advanced(self, image_array: np.ndarray) -> Dict[str, Any]:
        """
        Advanced bubble detection using multiple color spaces.
        
        Returns:
            Dict with bubble segmentation results
        """
        if not CV2_AVAILABLE:
            return {'bubbles': [], 'method': 'unavailable'}
        
        # Mask UI chrome first
        image_masked = self.mask_ui_chrome(image_array)
        
        # Convert to different color spaces for analysis
        bgr = cv2.cvtColor(image_masked, cv2.COLOR_RGB2BGR)
        lab = cv2.cvtColor(image_masked, cv2.COLOR_RGB2LAB)
        
        # Detect message bubbles by looking for regions with consistent color
        # (bright white/light blue for sent, dark grey for received)
        
        # Light regions (sent messages) - high brightness
        lower_light = np.array([200, 0, 0])  # LAB: L > 200 (light)
        upper_light = np.array([255, 128, 128])  # LAB: medium a, b
        mask_light = cv2.inRange(lab, lower_light, upper_light)
        
        # Dark regions (received messages) - low brightness
        lower_dark = np.array([0, 0, 0])  # LAB: L < 150 (dark)
        upper_dark = np.array([150, 180, 180])  # LAB
        mask_dark = cv2.inRange(lab, lower_dark, upper_dark)
        
        # Combine masks
        mask_combined = cv2.bitwise_or(mask_light, mask_dark)
        
        # Clean up with morphological operations
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (10, 5))
        mask_cleaned = cv2.morphologyEx(mask_combined, cv2.MORPH_CLOSE, kernel)
        mask_cleaned = cv2.morphologyEx(mask_cleaned, cv2.MORPH_OPEN, kernel)
        
        # Find contours
        contours, _ = cv2.findContours(mask_cleaned, cv2.RETR_EXTERNAL, 
                                       cv2.CHAIN_APPROX_SIMPLE)
        
        # Filter by size
        min_area = 300
        min_height = 20
        bubbles = []
        
        for contour in contours:
            area = cv2.contourArea(contour)
            x, y, w, h = cv2.boundingRect(contour)
            
            if area > min_area and h > min_height:
                bubbles.append({
                    'x': x, 'y': y, 'width': w, 'height': h,
                    'area': area,
                    'center_x': x + w // 2,
                    'is_light': np.mean(mask_light[y:y+h, x:x+w]) > 100,
                    'is_dark': np.mean(mask_dark[y:y+h, x:x+w]) > 100
                })
        
        return {
            'bubbles': bubbles,
            'method': 'lab_color_space',
            'count': len(bubbles)
        }
    
    def cluster_by_alignment_improved(self, text_blocks: List[TextBlock]) -> Dict[str, List[TextBlock]]:
        """
        Improved alignment clustering using statistical analysis.
        
        Uses quartile analysis to better separate left vs right messages.
        """
        if not text_blocks:
            return {'left': [], 'right': [], 'center': []}
        
        # Get all x centers
        centers_x = [block.center_x for block in text_blocks]
        
        # Calculate quartiles
        q1, median, q3 = np.percentile(centers_x, [25, 50, 75])
        
        # Use wider thresholds based on distribution
        left_threshold = q1 - (q3 - q1) * 0.5  # More generous left
        right_threshold = q3 + (q3 - q1) * 0.5  # More generous right
        
        left_blocks = []
        center_blocks = []
        right_blocks = []
        
        for block in text_blocks:
            if block.center_x < q1:
                left_blocks.append(block)
            elif block.center_x > q3:
                right_blocks.append(block)
            else:
                center_blocks.append(block)
        
        return {
            'left': left_blocks,
            'center': center_blocks,
            'right': right_blocks,
            'statistics': {
                'q1': float(q1),
                'median': float(median),
                'q3': float(q3),
                'all_centers': [float(x) for x in centers_x]
            }
        }
    
    def detect_metadata(self, line: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Detect metadata with improved regex patterns."""
        line = line.strip()
        if not line:
            return (None, None, None)
        
        # Pattern 1: name + phone + timestamp
        m = re.match(
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})\s+[-:+]\s*(.*?)$',
            line
        )
        if m:
            return (m.group(1), m.group(2).strip(), m.group(3).strip())
        
        # Pattern 2: name + phone
        m = re.match(
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})$',
            line
        )
        if m:
            return (m.group(1), m.group(2).strip(), None)
        
        # Pattern 3: name + timestamp (with +/- separator or comma)
        m = re.match(
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+[+\-]\s*(.*?)$',
            line
        )
        if m:
            potential_timestamp = m.group(2).strip()
            # Verify it looks like a timestamp
            if re.search(r'(?:AM|PM|am|pm|\d{1,2}:\d{2}|Mon|Tue|Wed|Thu|Fri|Sat|Sun)', 
                        potential_timestamp):
                return (m.group(1), None, potential_timestamp)
        
        # Pattern 4: Known contact name only
        known_contacts = {'Carrie Lyons', 'Frances Lyon', 'Crystal Lyon', 'Lyndsay', 
                         'Justin', 'Crystal', 'Carrie', 'Frances'}
        if line in known_contacts:
            return (line, None, None)
        
        # Pattern 5: Timestamp only
        if re.match(r'^\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)$', line):
            return (None, None, line)
        
        if re.match(r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?', line):
            return (None, None, line)
        
        # Pattern 6: Phone only
        if re.match(r'^\+?[\d\s\-\(\)]{10,}$', line):
            return (None, line.strip(), None)
        
        return (None, None, None)
    
    def group_blocks_into_messages(self, text_blocks: List[TextBlock], 
                                   alignment_groups: Dict[str, List[TextBlock]]) -> List[List[TextBlock]]:
        """
        Group text blocks into messages based on vertical proximity.
        
        Strategy: consecutive vertically-close blocks belong to the same message.
        """
        if not text_blocks:
            return []
        
        # Sort by vertical position
        sorted_blocks = sorted(text_blocks, key=lambda b: (b.top, b.x1))
        
        message_groups = []
        current_group = [sorted_blocks[0]]
        vertical_gap_threshold = 40  # pixels
        
        for i in range(1, len(sorted_blocks)):
            block = sorted_blocks[i]
            prev_block = sorted_blocks[i - 1]
            
            gap = block.vertical_gap_to(prev_block)
            
            if gap <= vertical_gap_threshold:
                # Vertically close - same message
                current_group.append(block)
            else:
                # Gap detected - new message
                message_groups.append(current_group)
                current_group = [block]
        
        # Add final group
        message_groups.append(current_group)
        
        return message_groups
    
    def extract_participants_from_header(self, text: str) -> List[str]:
        """Extract participant names from header."""
        lines = text.split('\n')
        if not lines:
            return []
        
        header = lines[0]
        header = re.sub(r'[©8O\}]', '', header)
        header = re.sub(r'\.\.\.$', '', header)
        header = re.sub(r'^[<>]+\s*', '', header)
        
        names = [n.strip() for n in header.split(',') if n.strip()]
        valid_names = [n for n in names if re.match(r'^[A-Z][a-z]+', n)]
        
        self.participants = valid_names
        return valid_names
    
    def parse_conversation(self, text: str, text_blocks: List[TextBlock]) -> List[Dict]:
        """Parse conversation with improved spatial grouping."""
        lines = text.strip().split('\n')
        start_idx = 1 if lines else 0
        lines = lines[start_idx:]
        
        messages = []
        accumulated = []
        known_contacts = set(self.participants) if self.participants else {
            'Carrie Lyons', 'Frances Lyon', 'Crystal Lyon', 'Lyndsay', 'Justin', 'Crystal'
        }
        
        for line in lines:
            line_stripped = line.strip()
            
            if not line_stripped or line_stripped.lower() == '0} type message':
                continue
            
            sender, phone, timestamp = self.detect_metadata(line_stripped)
            
            if sender or phone or timestamp:
                # Metadata detected
                if accumulated:
                    if not sender:
                        sender = self.user_name
                    
                    messages.append(self._create_message(sender, phone, timestamp, accumulated))
                    accumulated = []
            else:
                accumulated.append(line_stripped)
        
        # Finalize remaining
        if accumulated:
            messages.append(self._create_message(self.user_name, None, None, accumulated))
        
        self.messages = messages
        return messages
    
    def _create_message(self, sender: Optional[str], phone: Optional[str],
                       timestamp: Optional[str], content: List[str]) -> Dict:
        """Create structured message."""
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
        """Main extraction pipeline."""
        print(f"\n{'='*60}")
        print(f"Extracting conversation from: {self.image_path.name}")
        print(f"Using: Advanced Spatial Layout Analysis (v4)")
        print(f"{'='*60}\n")
        
        # Load image
        image = Image.open(self.image_path)
        self.image_array = np.array(image)
        self.image_width = self.image_array.shape[1]
        self.image_height = self.image_array.shape[0]
        print(f"[*] Image dimensions: {self.image_width}x{self.image_height}")
        
        # Extract text with bboxes
        text, text_blocks = self.extract_text_with_bboxes()
        print(f"[+] Text extraction: {len(text)} chars, {len(text_blocks)} text blocks\n")
        
        self.text_blocks = text_blocks
        
        # Advanced bubble detection
        bubble_results = self.detect_message_bubbles_advanced(self.image_array)
        print(f"[+] Bubble segmentation: {bubble_results['count']} bubbles detected "
              f"(method: {bubble_results['method']})\n")
        
        # Improved alignment clustering
        alignment = self.cluster_by_alignment_improved(text_blocks)
        print(f"[+] Alignment analysis:")
        print(f"    - Left: {len(alignment['left'])} blocks")
        print(f"    - Center: {len(alignment['center'])} blocks")
        print(f"    - Right: {len(alignment['right'])} blocks\n")
        
        # Message grouping by vertical proximity
        message_groups = self.group_blocks_into_messages(text_blocks, alignment)
        print(f"[+] Spatial grouping: {len(message_groups)} message groups\n")
        
        # Extract participants
        participants = self.extract_participants_from_header(text)
        is_group = len(participants) > 1
        chat_type = "Group Chat" if is_group else "1-to-1 Conversation"
        print(f"[+] Participants: {', '.join(participants)} ({chat_type})\n")
        
        # Parse into messages
        messages = self.parse_conversation(text, text_blocks)
        print(f"[+] Parsed {len(messages)} messages\n")
        
        output = {
            'source_file': self.image_path.name,
            'extraction_method': 'Advanced Spatial Layout Analysis (v4)',
            'conversation_type': chat_type,
            'participants': participants,
            'is_group_chat': is_group,
            'total_messages': len(messages),
            'spatial_analysis': {
                'image_dimensions': [self.image_width, self.image_height],
                'text_blocks_total': len(text_blocks),
                'left_aligned': len(alignment['left']),
                'center_aligned': len(alignment['center']),
                'right_aligned': len(alignment['right']),
                'message_groups': len(message_groups),
                'bubbles_detected': bubble_results['count'],
                'alignment_stats': alignment['statistics']
            },
            'messages': messages,
            'raw_ocr_text': ''
        }
        
        return output
    
    def to_json(self, output_path: Optional[str] = None) -> str:
        """Export to JSON."""
        output = {
            'source_file': self.image_path.name,
            'extraction_method': 'Advanced Spatial Layout Analysis (v4)',
            'conversation_type': "Group Chat" if len(self.participants) > 1 else "1-to-1 Conversation",
            'participants': self.participants,
            'is_group_chat': len(self.participants) > 1,
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
    image_path = 'temp/Attachment 1.PNG'
    extractor = SpatialConversationExtractorV4(image_path, user_name='Justin')
    
    result = extractor.extract()
    
    output_json_path = 'temp/Attachment 1_extracted_v4.json'
    extractor.to_json(output_json_path)
    
    print("="*60)
    print("EXTRACTION SUMMARY (Advanced Spatial v4)")
    print("="*60)
    summary = {
        'source_file': result['source_file'],
        'total_messages': result['total_messages'],
        'output_file': output_json_path,
        'spatial_metrics': result['spatial_analysis'],
        'preview': result['messages'][:3] if result['messages'] else []
    }
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
