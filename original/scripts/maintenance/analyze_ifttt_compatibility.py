#!/usr/bin/env python3
"""Analyze IFTTT Excel data compatibility with SMS database."""

import sqlite3
import pandas as pd
import json
from pathlib import Path

def analyze_sms_db():
    """Get SMS database schema and sample data."""
    conn = sqlite3.connect('sms_db.sqlite')
    cursor = conn.cursor()
    
    print('=' * 60)
    print('SMS DATABASE SCHEMA & SAMPLE DATA')
    print('=' * 60)
    
    # Get table names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    
    for table in tables:
        table_name = table[0]
        print(f'\n✓ Table: {table_name}')
        print('  Columns:')
        
        cursor.execute(f'PRAGMA table_info({table_name})')
        columns = cursor.fetchall()
        for col in columns:
            col_id, col_name, col_type, notnull, default, pk = col
            print(f'    - {col_name}: {col_type} {"[PK]" if pk else ""}')
        
        # Get count and sample
        cursor.execute(f'SELECT COUNT(*) FROM {table_name}')
        count = cursor.fetchone()[0]
        print(f'  Total rows: {count}')
        
        if count > 0:
            cursor.execute(f'SELECT * FROM {table_name} LIMIT 1')
            sample = cursor.fetchone()
            print(f'  Sample row: {sample[:3]}...')
    
    conn.close()

def analyze_ifttt_excel():
    """Get IFTTT Excel structure and sample data."""
    print('\n' + '=' * 60)
    print('IFTTT EXCEL FILE STRUCTURE')
    print('=' * 60)
    
    xl_file = Path('temp/IFTTT/SMS/SMS received.xlsx')
    
    if not xl_file.exists():
        print(f"File not found: {xl_file}")
        return None
    
    # Get sheet info
    xls = pd.ExcelFile(xl_file)
    print(f'\n✓ File: {xl_file.name}')
    print(f'  Sheet names: {xls.sheet_names}')
    
    # Read main sheet
    df = pd.read_excel(xl_file, sheet_name=0)
    
    print(f'\n✓ Sheet: {xls.sheet_names[0]}')
    print(f'  Columns: {list(df.columns)}')
    print(f'  Shape: {df.shape[0]} rows × {df.shape[1]} columns')
    print(f'  Data types:\n{df.dtypes.to_string()}')
    
    print('\n✓ First 3 rows:')
    print(df.head(3).to_string())
    
    print('\n✓ Data sample:')
    for idx, row in df.head(1).iterrows():
        for col, val in row.items():
            print(f'    {col}: {val}')
    
    return df

def compare_and_recommend(df_ifttt):
    """Provide recommendations for data integration."""
    print('\n' + '=' * 60)
    print('COMPATIBILITY ANALYSIS & RECOMMENDATIONS')
    print('=' * 60)
    
    if df_ifttt is None:
        print("Could not analyze IFTTT data.")
        return
    
    # Connect to DB to get column names
    conn = sqlite3.connect('sms_db.sqlite')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(sms)")
    db_columns = {col[1]: col[2] for col in cursor.fetchall()}
    conn.close()
    
    ifttt_cols = set(df_ifttt.columns)
    db_cols = set(db_columns.keys())
    
    print(f'\n✓ IFTTT columns: {ifttt_cols}')
    print(f'✓ Database columns: {db_cols}')
    
    # Find matches
    matching = ifttt_cols & db_cols
    ifttt_only = ifttt_cols - db_cols
    db_only = db_cols - ifttt_cols
    
    print(f'\n✓ Matching columns: {matching if matching else "None"}')
    print(f'✓ IFTTT-only columns: {ifttt_only if ifttt_only else "None"}')
    print(f'✓ Database-only columns: {db_only if db_only else "None"}')
    
    # Recommendations
    print('\n✓ INTEGRATION STRATEGIES:')
    
    if 'body' in ifttt_cols and 'address' in ifttt_cols:
        print('\n  1. DIRECT IMPORT (if timestamps match):')
        print('     - IFTTT has: body (content), address (sender)')
        print('     - Map IFTTT → SMS table')
        print('     - Check for created_at or similar timestamp field')
    
    if 'created_at' in ifttt_cols or 'date' in ifttt_cols:
        print('\n  2. TIMESTAMP MATCHING (recommended):')
        print('     - Use timestamp as key for deduplication')
        print('     - Cross-reference with existing SMS messages')
        print('     - Identify gaps and new messages')
    
    print('\n  3. COLUMN MAPPING:')
    for ifttt_col in ifttt_cols:
        potential_matches = [db_col for db_col in db_cols 
                           if ifttt_col.lower() in db_col.lower() or 
                           db_col.lower() in ifttt_col.lower()]
        if potential_matches:
            print(f'     - {ifttt_col} → {potential_matches}')

if __name__ == '__main__':
    analyze_sms_db()
    df = analyze_ifttt_excel()
    compare_and_recommend(df)
