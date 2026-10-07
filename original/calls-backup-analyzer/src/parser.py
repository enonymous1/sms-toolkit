from lxml import etree as ET
import os
from tqdm import tqdm

def stream_call_elements(file_path, pbar_pos=1, processor_ref=None):
    """Streams <call> elements with high-performance lxml and stable memory management."""
    file_size = os.path.getsize(file_path)
    
    with open(file_path, 'rb') as f:
        pbar = tqdm(total=file_size, unit='B', unit_scale=True, 
                    desc=f"└─ Streaming", 
                    position=pbar_pos, leave=False, colour='cyan',
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}, {rate_fmt}{postfix}]')
        
        try:
            # Pass huge_tree and recover directly to iterparse
            context = ET.iterparse(f, events=('end',), recover=True, huge_tree=True)
            
            count = 0
            for event, elem in context:
                if elem.tag == 'call':
                    yield elem
                    count += 1
                    
                    if count % 100 == 0:
                        pbar.n = f.tell()
                        if processor_ref:
                            pbar.set_postfix({
                                "calls": f"{processor_ref.call_count:,}",
                                "dup": f"{(processor_ref.duplicate_count/max(1,processor_ref.total_processed)*100):.1f}%"
                            }, refresh=True)

                    elem.clear()
                    parent = elem.getparent()
                    if parent is not None and parent.tag == 'calls':
                        while elem.getprevious() is not None:
                            del parent[0]
                
                elif elem.getparent() is not None and elem.getparent().tag == 'calls':
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
            
            pbar.n = file_size
            pbar.refresh()
        finally:
            pbar.close()
