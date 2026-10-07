import sqlite3

def check_image():
    try:
        conn = sqlite3.connect('sms_db.sqlite')
        cursor = conn.cursor()
        
        query = """
            SELECT m.contact_name, m.readable_date, p.ct, p.name, LENGTH(p.data) 
            FROM messages m 
            JOIN mms_parts p ON m.msg_hash = p.msg_hash 
            WHERE p.ct LIKE 'image/%' AND p.data IS NOT NULL 
            LIMIT 1
        """
        
        cursor.execute(query)
        row = cursor.fetchone()
        
        print("\n--- MMS Image Verification ---")
        if row:
            print(f"Contact:      {row[0]}")
            print(f"Date:         {row[1]}")
            print(f"Mime Type:    {row[2]}")
            print(f"Filename:     {row[3]}")
            print(f"Data Size:    {row[4]:,} bytes (Base64)")
        else:
            print("No image data found in the database.")
            
        conn.close()
    except Exception as e:
        print(f"Error checking database: {e}")

if __name__ == "__main__":
    check_image()
