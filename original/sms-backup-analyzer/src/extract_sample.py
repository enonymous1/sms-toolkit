import sqlite3
import base64
import os

def get_extension_from_mime(mime_type):
    """Map MIME type to file extension."""
    mime_map = {
        'image/jpeg': '.jpeg',
        'image/jpg': '.jpg',
        'image/png': '.png',
        'image/gif': '.gif',
        'image/webp': '.webp',
        'image/bmp': '.bmp',
        'image/tiff': '.tiff',
    }
    return mime_map.get(mime_type.lower(), '.bin')

def sanitize_filename(name):
    """Remove invalid filename characters."""
    invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name[:50]  # Limit length

def generate_unique_filename(original_name, mime_type, contact, date_str, msg_hash):
    """Generate a unique, descriptive filename.
    
    Format: {original_or_generic}_{contact}_{date}_{hash_prefix}.{ext}
    Example: Image_JohnCarden_20140523_a1b2c3d4.jpeg
    """
    # Get proper extension from mime type
    ext = get_extension_from_mime(mime_type).lstrip('.')
    
    # Use original filename if available, otherwise generic name
    if original_name and original_name.lower() != 'null':
        base_name = sanitize_filename(original_name.split('.')[0])  # Remove existing ext if present
    else:
        base_name = 'Image'
    
    # Sanitize contact name
    contact_safe = sanitize_filename(contact.replace(' ', '')[:15]) if contact else 'Unknown'
    
    # Format date (extract YYYYMMDD from readable date or use hash date)
    if date_str:
        # Try to extract date from readable_date format like "May 23, 2014"
        parts = date_str.split()
        if len(parts) >= 3:
            try:
                month_map = {'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
                            'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'}
                month = month_map.get(parts[0], '00')
                day = parts[1].rstrip(',').zfill(2)
                year = parts[2][:4]
                date_fmt = f"{year}{month}{day}"
            except:
                date_fmt = 'unkdate'
        else:
            date_fmt = 'unkdate'
    else:
        date_fmt = 'unkdate'
    
    # Use first 8 chars of hash for uniqueness
    hash_prefix = msg_hash[:8] if msg_hash else 'nohash'
    
    # Combine all parts
    filename = f"{base_name}_{contact_safe}_{date_fmt}_{hash_prefix}.{ext}"
    return filename

def extract_sample_image():
    db_path = 'sms_db.sqlite'
    output_dir = 'extracted_samples'
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Query for the first image part that has data
        query = """
            SELECT p.name, p.ct, p.data, m.contact_name, m.readable_date, p.msg_hash
            FROM mms_parts p
            JOIN messages m ON p.msg_hash = m.msg_hash
            WHERE p.ct LIKE 'image/%' AND p.data IS NOT NULL
            LIMIT 1
        """
        
        cursor.execute(query)
        row = cursor.fetchone()
        
        if row:
            original_name = row[0]
            mime_type = row[1]
            b64_data = row[2]
            contact = row[3]
            date_str = row[4]
            msg_hash = row[5]
            
            # Generate unique filename
            filename = generate_unique_filename(original_name, mime_type, contact, date_str, msg_hash)
            output_path = os.path.join(output_dir, filename)
            
            # Rebuild the image from Base64
            print(f"Extracting image from {contact} ({date_str})...")
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(b64_data))
                
            print(f"Success! Image saved to: {os.path.abspath(output_path)}")
            print(f"Filename: {filename}")
            print(f"Mime-type: {mime_type}")
            print(f"File size: {os.path.getsize(output_path):,} bytes")
            print(f"Message hash: {msg_hash}")
        else:
            print("No image data found in the database to extract.")
            
        conn.close()
    except Exception as e:
        print(f"Error extracting image: {e}")

if __name__ == "__main__":
    extract_sample_image()
