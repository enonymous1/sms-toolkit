#!/usr/bin/env python3
"""Extract and display IFTTT SMS data samples."""

import openpyxl
from datetime import datetime
import re

def parse_ifttt_timestamp(ts_str):
    """Parse IFTTT timestamp format."""
    try:
        # Format: "November 24, 2020 at 08:10AM"
        return datetime.strptime(ts_str, "%B %d, %Y at %I:%M%p")
    except:
        return None

def normalize_phone(phone_str):
    """Normalize phone number."""
    if not phone_str:
        return None
    
    # Convert to string if numeric
    phone = str(int(float(phone_str))) if isinstance(phone_str, (int, float)) else str(phone_str)
    
    # If it looks like a short code (< 5 digits)
    if len(phone) < 5:
        return phone
    
    # If it looks like a full number, format as E.164
    if len(phone) >= 10:
        # Remove non-digits
        digits = re.sub(r'\D', '', phone)
        if len(digits) == 10:
            return f"+1{digits}"
        elif len(digits) == 11 and digits[0] == '1':
            return f"+{digits}"
        return f"+{digits}"
    
    return phone

# Read IFTTT Excel
wb = openpyxl.load_workbook('temp/IFTTT/SMS/SMS received.xlsx')
ws = wb.active

print('=' * 80)
print('IFTTT SMS DATA EXTRACTION & SAMPLES')
print('=' * 80)

# Extract messages in order
messages = []
message_num = 0

for row_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
    # Find timestamp at start of row
    timestamp_col = None
    for col_idx, val in enumerate(row):
        if val and isinstance(val, str) and 'at' in val.lower() and any(c.isdigit() for c in val):
            # Likely a timestamp
            if re.search(r'\d{1,2}:\d{2}', val):
                timestamp_col = col_idx
                break
    
    if timestamp_col is not None:
        try:
            timestamp_str = row[timestamp_col]
            phone_num = row[timestamp_col + 1]
            message_body = row[timestamp_col + 2]
            sender_name = row[timestamp_col + 3] if timestamp_col + 3 < len(row) else None
            
            # Skip if critical fields missing
            if not message_body:
                continue
            
            timestamp_dt = parse_ifttt_timestamp(timestamp_str)
            phone_normalized = normalize_phone(phone_num)
            
            message_num += 1
            messages.append({
                'num': message_num,
                'row': row_idx,
                'timestamp_raw': timestamp_str,
                'timestamp_dt': timestamp_dt,
                'phone_raw': phone_num,
                'phone_normalized': phone_normalized,
                'body': message_body,
                'sender_name': sender_name
            })
        except Exception as e:
            pass

print(f'\nExtracted {len(messages)} messages\n')

# Display samples
print('=' * 80)
print('SAMPLE MESSAGES (First 5)')
print('=' * 80)

for msg in messages[:5]:
    print(f'\n[Message {msg["num"]}]')
    print(f'  Row in Excel: {msg["row"]}')
    print(f'  Timestamp (raw): {msg["timestamp_raw"]}')
    print(f'  Timestamp (parsed): {msg["timestamp_dt"]}')
    print(f'  Phone (raw): {msg["phone_raw"]}')
    print(f'  Phone (normalized): {msg["phone_normalized"]}')
    if msg["sender_name"]:
        print(f'  Sender: {msg["sender_name"]}')
    print(f'  Body: {msg["body"][:80]}...' if len(msg["body"]) > 80 else f'  Body: {msg["body"]}')

# Statistics
print('\n' + '=' * 80)
print('STATISTICS')
print('=' * 80)

phone_numbers = {}
sender_names = {}

for msg in messages:
    phone = msg["phone_normalized"]
    if phone:
        phone_numbers[phone] = phone_numbers.get(phone, 0) + 1
    
    if msg["sender_name"]:
        sender_names[msg["sender_name"]] = sender_names.get(msg["sender_name"], 0) + 1

print(f'\n✓ Total messages: {len(messages)}')
print(f'✓ Date range: {messages[0]["timestamp_raw"]} to {messages[-1]["timestamp_raw"]}')
print(f'✓ Unique phone numbers: {len(phone_numbers)}')
print(f'✓ Messages with sender name: {len([m for m in messages if m["sender_name"]])}')

print('\n✓ Top 10 phone numbers (by message count):')
for phone, count in sorted(phone_numbers.items(), key=lambda x: -x[1])[:10]:
    print(f'  {phone}: {count} messages')

if sender_names:
    print('\n✓ Sender names found:')
    for name, count in sorted(sender_names.items(), key=lambda x: -x[1]):
        print(f'  {name}: {count} messages')

# Database compatibility check
print('\n' + '=' * 80)
print('DATABASE COMPATIBILITY CHECK')
print('=' * 80)

import sqlite3

conn = sqlite3.connect('sms_db.sqlite')
cursor = conn.cursor()

# Check for overlaps
cursor.execute("SELECT COUNT(*) FROM messages WHERE type = 1")  # type 1 = received
existing_received = cursor.fetchone()[0]

print(f'\n✓ Existing received messages in database: {existing_received}')
print(f'✓ New messages from IFTTT: {len(messages)}')
print(f'✓ Total would be: {existing_received + len(messages)}')

# Check date overlap
if messages:
    first_date = messages[0]["timestamp_dt"]
    last_date = messages[-1]["timestamp_dt"]
    
    print(f'\n✓ IFTTT date range: {first_date.strftime("%Y-%m-%d")} to {last_date.strftime("%Y-%m-%d")}')
    
    # Check for overlapping messages
    query = """
    SELECT COUNT(*) FROM messages 
    WHERE date >= ? AND date <= ?
    """
    cursor.execute(query, (first_date.isoformat(), last_date.isoformat()))
    overlapping = cursor.fetchone()[0]
    
    print(f'✓ Messages in database within IFTTT date range: {overlapping}')
    print(f'✓ Potential duplicates to check: {overlapping}')

conn.close()

print('\n' + '=' * 80)
