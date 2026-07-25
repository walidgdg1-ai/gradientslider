#!/usr/bin/env python3
from pathlib import Path

path = Path("tools/harvest_commons_fast.py")
source = path.read_text(encoding="utf-8")

source = source.replace("WORKERS=12", "WORKERS=1")
source = source.replace("len(raw)<20000", "len(raw)<5000")
source = source.replace(
    "batch=pool[pos:pos+120]; pos+=120",
    "batch=pool[pos:pos+60]; pos+=60",
)

old = '''for attempt in range(4):
        try:
            r=requests.get(c['source_url'],headers={'User-Agent':UA},timeout=40); r.raise_for_status(); raw=r.content
            if len(raw)<5000:return None
            with Image.open(io.BytesIO(raw)) as src:
                src.load(); im=ImageOps.exif_transpose(src).convert('RGB')
            break
        except Exception:
            if attempt==3:return None
            time.sleep(1.5**attempt)'''

new = '''for attempt in range(12):
        try:
            time.sleep(2.4)
            headers={
                'User-Agent':'WalidVisualDatasetHarvester/3.1 (https://github.com/walidgdg1-ai/gradientslider)',
                'Referer':'https://commons.wikimedia.org/',
                'Accept':'image/avif,image/webp,image/png,image/jpeg,*/*',
            }
            r=requests.get(c['source_url'],headers=headers,timeout=50)
            if r.status_code == 429:
                delay=float(r.headers.get('Retry-After','11'))+1.0
                print(f'RATE_LIMIT wait={delay}s title={c["title"]}',flush=True)
                time.sleep(delay)
                continue
            r.raise_for_status(); raw=r.content
            if len(raw)<5000:return None
            with Image.open(io.BytesIO(raw)) as src:
                src.load(); im=ImageOps.exif_transpose(src).convert('RGB')
            break
        except Exception as exc:
            if attempt==11:
                print(f'DOWNLOAD_FAILED {type(exc).__name__} {c["title"]}',flush=True)
                return None
            time.sleep(min(15,2**attempt))
    else:
        return None'''

if old not in source:
    raise SystemExit("fetch_process patch target not found")

source = source.replace(old, new)
path.write_text(source, encoding="utf-8")
print("Patched harvester with explicit Wikimedia 429 Retry-After handling.")
