from flask import Flask, render_template, request, jsonify
import sqlite3
import base64
import os

app = Flask(__name__)

# Assumes the database is in the parent directory
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'sms_db.sqlite'))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/stats')
def stats():
    conn = get_db_connection()
    stats = {}
    stats['total_messages'] = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    stats['total_sms'] = conn.execute('SELECT COUNT(*) FROM messages WHERE tag="sms"').fetchone()[0]
    stats['total_mms'] = conn.execute('SELECT COUNT(*) FROM messages WHERE tag="mms"').fetchone()[0]
    stats['top_contacts'] = [dict(row) for row in conn.execute('SELECT contact_name, address, COUNT(*) as count FROM messages GROUP BY address ORDER BY count DESC LIMIT 5').fetchall()]
    conn.close()
    return jsonify(stats)

@app.route('/api/messages')
def messages():
    page = int(request.args.get('page', 1))
    per_page = 50
    offset = (page - 1) * per_page
    search = request.args.get('search', '')
    
    conn = get_db_connection()
    query = 'SELECT * FROM messages WHERE (body LIKE ? OR contact_name LIKE ? OR address LIKE ?) ORDER BY date DESC LIMIT ? OFFSET ?'
    search_param = f'%{search}%'
    rows = conn.execute(query, (search_param, search_param, search_param, per_page, offset)).fetchall()
    
    messages = []
    for row in rows:
        msg = dict(row)
        if msg['tag'] == 'mms':
            # Check for text in parts if body is generic [MMS]
            parts = conn.execute('SELECT text, ct FROM mms_parts WHERE msg_hash = ? AND ct = "text/plain"', (msg['msg_hash'],)).fetchall()
            if parts:
                msg['body'] = " | ".join([p['text'] for p in parts if p['text']])
            
            # Check for image parts
            images = conn.execute('SELECT seq FROM mms_parts WHERE msg_hash = ? AND ct LIKE "image/%"', (msg['msg_hash'],)).fetchall()
            msg['has_images'] = len(images) > 0
        messages.append(msg)
        
    conn.close()
    return jsonify(messages)

@app.route('/api/message/<msg_hash>')
def message_detail(msg_hash):
    conn = get_db_connection()
    msg = dict(conn.execute('SELECT * FROM messages WHERE msg_hash = ?', (msg_hash,)).fetchone())
    # Include ALL columns from mms_parts and mms_addrs
    parts = [dict(row) for row in conn.execute('SELECT * FROM mms_parts WHERE msg_hash = ?', (msg_hash,)).fetchall()]
    # Ensure blob/binary data is handled or omitted in the detail json if needed, 
    # but we'll include has_data flag instead of raw data to keep json small.
    detail_parts = []
    for p in parts:
        pd = dict(p)
        pd['has_data'] = pd['data'] is not None
        del pd['data'] # Remove raw data from json
        detail_parts.append(pd)
        
    addrs = [dict(row) for row in conn.execute('SELECT * FROM mms_addrs WHERE msg_hash = ?', (msg_hash,)).fetchall()]
    conn.close()
    return jsonify({'message': msg, 'parts': detail_parts, 'addrs': addrs})

@app.route('/api/image/<msg_hash>/<int:seq>')
def get_image(msg_hash, seq):
    conn = get_db_connection()
    part = conn.execute('SELECT data, ct FROM mms_parts WHERE msg_hash = ? AND seq = ?', (msg_hash, seq)).fetchone()
    conn.close()
    if part and part['data']:
        return base64.b64decode(part['data']), 200, {'Content-Type': part['ct']}
    return "Not found", 404

if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
    else:
        app.run(debug=True, port=5000)
