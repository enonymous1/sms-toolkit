"""
Match extracted screenshot messages against SMS database records.
Focuses on sender identification (name/phone) and text content similarity.
De-emphasizes timestamp matching due to OCR unreliability.
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
from fuzzywuzzy import fuzz

class MessageMatcher:
    def __init__(self, db_path: str = 'sms_db.sqlite'):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        
    def get_db_schema(self):
        """Inspect the messages table schema."""
        self.cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'")
        result = self.cursor.fetchone()
        if result:
            print("Messages table schema:")
            print(result[0])
            print()
        
        # Get sample data
        self.cursor.execute("SELECT * FROM messages LIMIT 1")
        columns = [description[0] for description in self.cursor.description]
        print(f"Columns: {columns}\n")
        return columns
    
    def find_matches_for_message(self, extracted_msg: Dict) -> List[Dict]:
        """
        Find potential matches for an extracted message.
        Strategy: Match by sender (name/phone), then score by content similarity.
        Timestamp matching is optional/low-priority due to OCR unreliability.
        Returns list of matching database records with confidence scores.
        """
        matches = []
        
        sender = extracted_msg.get('sender')
        phone = extracted_msg.get('phone_number')
        content = extracted_msg.get('content', '').strip()
        
        # Build query focused on sender identification
        query = "SELECT * FROM messages WHERE 1=1"
        params = []
        
        # PRIMARY: Match by phone number if available (most reliable)
        if phone:
            clean_phone = ''.join(c for c in phone if c.isdigit())
            query += " AND address LIKE ?"
            params.append(f"%{clean_phone}%")
        # FALLBACK: Match by sender name if phone not available
        elif sender:
            query += " AND contact_name LIKE ?"
            params.append(f"%{sender}%")
        else:
            # No sender info - cannot reliably match, return empty
            return []
        
        # Limit results for performance
        query += " LIMIT 50"
        
        try:
            self.cursor.execute(query, params)
            potential_matches = self.cursor.fetchall()
            
            # Score each match based on content similarity
            for db_record in potential_matches:
                db_msg = db_record['body'] if 'body' in db_record.keys() else ''
                
                # Use fuzzy string matching for better OCR tolerance
                similarity = self._compute_fuzzy_similarity(content, db_msg)
                
                # Lower threshold since OCR text may have errors
                if similarity > 0.25:  # 25% similarity minimum
                    matches.append({
                        'confidence': similarity,
                        'db_record': dict(db_record),
                        'msg_hash': db_record['msg_hash'],
                        'contact_name': db_record['contact_name'],
                        'phone': db_record['address'],
                        'timestamp': db_record['readable_date'],
                        'body_preview': db_msg[:100],
                        'body_full': db_msg
                    })
            
            # Sort by confidence (highest first)
            matches.sort(key=lambda x: x['confidence'], reverse=True)
            return matches[:5]  # Return top 5 matches
            
        except sqlite3.OperationalError as e:
            print(f"Query error: {e}")
            return []
    
    def _compute_fuzzy_similarity(self, text1: str, text2: str) -> float:
        """
        Compute fuzzy similarity score between two text strings (0-1).
        More tolerant of OCR errors than exact matching.
        Uses token_set_ratio which handles partial/subset matches well.
        """
        if not text1 or not text2:
            return 0.0
        
        # Normalize text
        t1 = text1.lower().strip()
        t2 = text2.lower().strip()
        
        # Use fuzzy matching - token_set_ratio is good for OCR text with errors
        # It breaks text into tokens and compares (more flexible than ratio)
        fuzzy_score = fuzz.token_set_ratio(t1, t2) / 100.0
        
        return fuzzy_score
    
    def enrich_extracted_messages(self, extracted_json_path: str) -> Dict:
        """
        Load extracted messages and match them against database.
        Strategy: Match by sender name/phone, score by content similarity.
        Returns enhanced JSON with match information.
        """
        # Load extracted messages
        with open(extracted_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"Processing {len(data['messages'])} extracted messages...\n")
        
        # Attempt to match each message
        for i, msg in enumerate(data['messages'], 1):
            sender = msg.get('sender') or '[Unknown]'
            phone = msg.get('phone_number') or '[No phone]'
            content_preview = msg.get('content', '')[:60].replace('\n', ' ')
            
            print(f"[{i}/{len(data['messages'])}] {sender} {phone}")
            print(f"           Content: {content_preview}...")
            
            matches = self.find_matches_for_message(msg)
            
            if matches:
                print(f"           ✓ Found {len(matches)} match(es):")
                for j, match in enumerate(matches, 1):
                    conf = match['confidence']
                    contact = match['contact_name']
                    preview = match['body_preview'].replace('\n', ' ')
                    print(f"             [{j}] {contact} ({conf*100:.0f}%) - {preview}...")
            else:
                print(f"           ✗ No matches")
            
            # Add matches to message
            msg['database_matches'] = matches
            print()
        
        # Add summary
        total_with_matches = sum(1 for msg in data['messages'] if msg.get('database_matches'))
        data['match_summary'] = {
            'total_messages': len(data['messages']),
            'messages_with_matches': total_with_matches,
            'match_rate': f"{(total_with_matches/len(data['messages'])*100):.1f}%",
            'matching_strategy': 'Sender name/phone (primary) + content similarity (fuzzy)',
            'note': 'Timestamp matching excluded due to OCR unreliability'
        }
        
        return data
    
    def close(self):
        """Close database connection."""
        self.conn.close()


def main():
    # Initialize matcher
    matcher = MessageMatcher('sms_db.sqlite')
    
    # Check schema
    print("="*60)
    print("DATABASE SCHEMA")
    print("="*60 + "\n")
    matcher.get_db_schema()
    
    # Enrich extracted messages
    print("="*60)
    print("MATCHING MESSAGES")
    print("="*60 + "\n")
    
    extracted_json = 'temp/Attachment 1_extracted.json'
    enriched_data = matcher.enrich_extracted_messages(extracted_json)
    
    # Save enriched JSON
    output_path = 'temp/Attachment 1_matched.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(enriched_data, f, indent=2, ensure_ascii=False)
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(json.dumps(enriched_data['match_summary'], indent=2))
    print(f"\n[✓] Enriched JSON saved to: {output_path}\n")
    
    matcher.close()


if __name__ == "__main__":
    main()
