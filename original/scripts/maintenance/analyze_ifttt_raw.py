#!/usr/bin/env python3
"""Deep analysis of IFTTT Excel structure."""

import openpyxl
from pathlib import Path

# Read the raw Excel file
wb = openpyxl.load_workbook('temp/IFTTT/SMS/SMS received.xlsx')
ws = wb.active

print('=' * 70)
print('RAW EXCEL STRUCTURE')
print('=' * 70)
print(f'\nSheet name: {ws.title}')
print(f'Dimensions: {ws.max_row} rows × {ws.max_column} columns')

print('\n' + '=' * 70)
print('FIRST 3 ROWS (RAW DATA)')
print('=' * 70)

for row_idx, row in enumerate(ws.iter_rows(max_row=3, values_only=False), 1):
    print(f'\nRow {row_idx}:')
    for col_idx, cell in enumerate(row, 1):
        val = cell.value
        if val:
            val_str = str(val)[:60]
            print(f'  Col {col_idx}: {val_str}')

print('\n' + '=' * 70)
print('DATA PATTERN DETECTION')
print('=' * 70)

# Sample more rows to understand pattern
print('\nAnalyzing 10 rows to find pattern...')
for row_idx, row in enumerate(ws.iter_rows(max_row=10, values_only=True), 1):
    non_empty = [v for v in row if v is not None]
    if non_empty:
        print(f'Row {row_idx}: {len(non_empty)} values: {[str(v)[:40] for v in non_empty[:3]]}...')

# Check if data is vertical (multiple columns) or horizontal (multiple rows)
print('\n' + '=' * 70)
print('STRUCTURE HYPOTHESIS')
print('=' * 70)

# Get first 5 non-empty rows/cols
data_found = 0
for row in ws.iter_rows(values_only=True):
    if any(v for v in row if v):
        data_found += 1
        if data_found == 1:
            first_row = row
        elif data_found == 5:
            break

print(f'\nFirst non-empty row has {sum(1 for v in first_row if v)} values')
print(f'Pattern appears to be: IFTTT exported data in a wide format')
print(f'Each message might occupy one row with timestamp, phone, content')

print('\n' + '=' * 70)
print('EXTRACTING SAMPLE DATA')
print('=' * 70)

samples = []
for row_idx, row in enumerate(ws.iter_rows(max_row=5, values_only=True), 1):
    non_empty = [v for v in row if v is not None]
    if non_empty:
        samples.append({
            'row': row_idx,
            'values': non_empty,
            'count': len(non_empty)
        })

for sample in samples:
    print(f'\nRow {sample["row"]} ({sample["count"]} values):')
    for i, val in enumerate(sample['values'][:3]):
        print(f'  Value {i+1}: {str(val)[:70]}')
