import sqlite3
import pandas as pd
import os
import sys

def browse_database(db_path="sms_db.sqlite"):
    if not os.path.exists(db_path):
        print(f"Error: Database '{db_path}' not found. Please run the analyzer first.")
        return

    try:
        conn = sqlite3.connect(db_path)
        
        # Get total count
        count = pd.read_sql_query("SELECT COUNT(*) as count FROM messages", conn).iloc[0]['count']
        print(f"\n--- SMS Database Browser ---")
        print(f"Total Unique Messages: {count:,}")
        print(f"File: {os.path.abspath(db_path)}\n")

        # Basic Stats: Messages per Contact
        print("Top 10 Contacts by Message Count:")
        query_contacts = """
            SELECT contact_name, address, COUNT(*) as msg_count 
            FROM messages 
            GROUP BY address 
            ORDER BY msg_count DESC 
            LIMIT 10
        """
        top_contacts = pd.read_sql_query(query_contacts, conn)
        print(top_contacts.to_string(index=False))
        
        print("\nLast 5 Messages Received:")
        query_recent = """
            SELECT tag, readable_date, contact_name, address, SUBSTR(body, 1, 50) as body_preview
            FROM messages 
            ORDER BY date DESC 
            LIMIT 5
        """
        recent = pd.read_sql_query(query_recent, conn)
        print(recent.to_string(index=False))

        conn.close()
        print("\nNote: For full interactive browsing, use 'DB Browser for SQLite' or similar GUI tools.")
        
    except Exception as e:
        print(f"An error occurred while reading the database: {e}")

if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "sms_db.sqlite"
    browse_database(db_file)
