#!/usr/bin/env python3
from __future__ import annotations

import csv, hashlib, html, io, json, math, random, re, shutil, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import imagehash, requests
from PIL import Image, ImageFilter, ImageOps, ImageStat

API='https://commons.wikimedia.org/w/api.php'
UA='WalidVisualDatasetHarvesterFast/2.0 (GitHub Actions; Commons attribution preserved)'
ROOT=Path('visual-harvest-400')
TARGET=200
MAX_SIDE=1400
QUALITY=84
MIN_W,MIN_H=700,450
PHASH_DIST=5
WORKERS=12
random.seed(20260725)

LUXURY=[
('superyacht','superyacht'),('yacht-deck','luxury yacht deck'),('marina','marina sunset'),('monaco','Monaco harbour night'),
('private-jet','business jet interior'),('first-class','first class aircraft cabin'),('helicopter','helicopter city'),
('supercar','supercar city night'),('sports-car','sports car mountain road'),('car-interior','luxury automobile interior'),
('watch','luxury wristwatch macro'),('tailoring','business suit fashion'),('executive','executive office skyline'),
('boardroom','modern boardroom skyline'),('penthouse','penthouse interior city view'),('villa','modern villa architecture'),
('mansion','mansion interior'),('hotel','luxury hotel lobby'),('resort','resort infinity pool'),('rooftop','rooftop skyline sunset'),
('skyscraper','skyscraper night aerial'),('dubai','Dubai skyline night'),('new-york','New York skyline night'),
('architecture','modern architecture night'),('fine-dining','fine dining interior'),('celebration','champagne celebration'),
('summit','mountain summit sunrise person'),('runner','runner sunrise silhouette'),('work','professional working office night'),
('travel','luxury travel aerial')]

THREED=[
('blender','Blender 3D render'),('cgi','computer generated image render'),('abstract','abstract 3D rendering'),
('low-poly','low poly 3D art'),('voxel','voxel art 3D'),('isometric','isometric 3D illustration'),
('typography','3D typography render'),('surreal','surreal 3D digital art'),('scifi-city','science fiction city 3D render'),
('cyberpunk','cyberpunk 3D render'),('fantasy','fantasy landscape 3D render'),('game-environment','video game environment 3D'),
('architecture','architectural visualization 3D'),('interior','3D interior visualization'),('character','3D character render'),
('robot','robot 3D render'),('creature','creature 3D model render'),('spaceship','spaceship 3D render'),
('vehicle','concept vehicle 3D render'),('product','product visualization 3D'),('procedural','procedural 3D art'),
('fractal','3D fractal render'),('raytrace','ray tracing render'),('landscape','digital landscape 3D'),
('retro-game','retro 3D game screenshot'),('stylized','stylized 3D scene'),('clay','clay render 3D'),
('neon','neon 3D render'),('space','space station 3D render'),('cinematic','cinematic 3D scene render')]

BLOCKED=('map','diagram','chart','coat of arms','flag','logo','icon','signature','scan','document','poster','book cover','stamp','coin','banknote','qr code','barcode')

@dataclass
class Item:
    collection:str; index:int; category:str; filename:str; commons_title:str; commons_page:str; source_url:str
    author:str; license:str; license_url:str; description:str; original_width:int; original_height:int
    saved_width:int; saved_height:int; sha256:str; phash:str; quality_score:float

def clean(v:str|None)->str:
    if not v:return ''
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',v))).strip()

def mv(ext:dict[str,Any],key:str)->str:
    v=ext.get(key,{})
    return clean(str(v.get('value','') if isinstance(v,dict) else v))

def allowed_license(name:str)->bool:
    n=name.lower(); return any(x in n for x in ('public domain','cc0','cc by','creative commons','gfdl'))

def api_page(query:str, offset:int=0)->tuple[list[dict[str,Any]],int|None]:
    params={'action':'query','format':'json','formatversion':2,'generator':'search','gsrsearch':query+' filetype:bitmap',
            'gsrnamespace':6,'gsrlimit':50,'gsroffset':offset,'prop':'imageinfo','iiprop':'url|size|mime|extmetadata','iiurlwidth':1800}
    for attempt in range(4):
        try:
            r=requests.get(API,params=params,headers={'User-Agent':UA},timeout=30); r.raise_for_status(); data=r.json(); break
        except Exception:
            if attempt==3:return [],None
            time.sleep(1.5**attempt)
    pages=data.get('query',{}).get('pages',[])
    nxt=data.get('continue',{}).get('gsroffset')
    return pages, int(nxt) if nxt is not None else None

def candidate(page:dict[str,Any], category:str, collection:str)->dict[str,Any]|None:
    title=str(page.get('title','')); low=title.lower()
    if any(x in low for x in BLOCKED):return None
    infos=page.get('imageinfo') or []
    if not infos:return None
    info=infos[0]; mime=str(info.get('mime',''))
    if mime not in {'image/jpeg','image/png','image/webp'}:return None
    w,h=int(info.get('width',0)),int(info.get('height',0))
    if w<MIN_W or h<MIN_H:return None
    ext=info.get('extmetadata',{}) or {}
    lic=mv(ext,'LicenseShortName') or mv(ext,'UsageTerms')
    if not allowed_license(lic):return None
    url=info.get('thumburl') or info.get('url')
    if not url:return None
    return {'collection':collection,'category':category,'title':title,'page_url':info.get('descriptionurl') or f"https://commons.wikimedia.org/wiki/{quote(title.replace(' ','_'))}",
            'source_url':url,'author':mv(ext,'Artist') or 'See Commons page','license':lic,'license_url':mv(ext,'LicenseUrl'),
            'description':mv(ext,'ImageDescription') or mv(ext,'ObjectName'),'original_width':w,'original_height':h}

def gather(collection:str, queries:list[tuple[str,str]], pages_each:int=2)->list[dict[str,Any]]:
    out=[]; seen=set()
    for category,q in queries:
        offset=0
        for _ in range(pages_each):
            pages,nxt=api_page(q,offset)
            random.shuffle(pages)
            for p in pages:
                c=candidate(p,category,collection)
                if c and c['page_url'] not in seen:
                    seen.add(c['page_url']); out.append(c)
            if nxt is None:break
            offset=nxt
    random.shuffle(out)
    return out

def fetch_process(c:dict[str,Any]):
    for attempt in range(4):
        try:
            r=requests.get(c['source_url'],headers={'User-Agent':UA},timeout=40); r.raise_for_status(); raw=r.content
            if len(raw)<20000:return None
            with Image.open(io.BytesIO(raw)) as src:
                src.load(); im=ImageOps.exif_transpose(src).convert('RGB')
            break
        except Exception:
            if attempt==3:return None
            time.sleep(1.5**attempt)
    if im.width<MIN_W or im.height<MIN_H:return None
    ratio=im.width/im.height
    if ratio<0.45 or ratio>2.4:return None
    if max(im.size)>MAX_SIDE:im.thumbnail((MAX_SIDE,MAX_SIDE),Image.Resampling.LANCZOS)
    sample=im.copy(); sample.thumbnail((400,400),Image.Resampling.BILINEAR)
    gray=sample.convert('L'); stat=ImageStat.Stat(gray)
    contrast=stat.stddev[0]; sharp=ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    rgb=ImageStat.Stat(sample); sat=max(rgb.mean)-min(rgb.mean); mp=im.width*im.height/1e6
    score=round(contrast*.48+sharp*.30+sat*.12+min(mp,3)*4,3)
    if score<10:return None
    return c,im,imagehash.phash(im),score

def safe(title:str)->str:
    title=re.sub(r'^File:','',title,flags=re.I); title=re.sub(r'\.[A-Za-z0-9]{2,5}$','',title)
    return (re.sub(r'[^A-Za-z0-9]+','-',title).strip('-').lower()[:70] or 'image')

def harvest(collection:str, queries:list[tuple[str,str]], global_hashes:list[imagehash.ImageHash])->list[Item]:
    pool=gather(collection,queries,2)
    broad='luxury OR success OR skyline OR yacht OR supercar OR architecture OR travel' if collection=='luxury_motivation' else '"3D render" OR CGI OR Blender OR "computer generated" OR "digital art"'
    offset=0
    for _ in range(12):
        pages,nxt=api_page(broad,offset)
        for p in pages:
            c=candidate(p,'fallback',collection)
            if c:pool.append(c)
        if nxt is None:break
        offset=nxt
    pool=list({c['page_url']:c for c in pool}.values()); random.shuffle(pool)
    items=[]; cat_counts={}; pos=0
    while len(items)<TARGET and pos<len(pool):
        batch=pool[pos:pos+120]; pos+=120
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures=[ex.submit(fetch_process,c) for c in batch]
            for f in as_completed(futures):
                res=f.result()
                if not res:continue
                c,im,ph,score=res
                if len(items)<180 and cat_counts.get(c['category'],0)>=12:continue
                if any(ph-h<=PHASH_DIST for h in global_hashes):continue
                idx=len(items)+1; name=f"{idx:03d}_{c['category']}_{safe(c['title'])}.jpg"; path=ROOT/collection/name
                path.parent.mkdir(parents=True,exist_ok=True); im.save(path,'JPEG',quality=QUALITY,optimize=True,progressive=True)
                sha=hashlib.sha256(path.read_bytes()).hexdigest()
                items.append(Item(collection,idx,c['category'],str(path.as_posix()),c['title'],c['page_url'],c['source_url'],c['author'],c['license'],c['license_url'],c['description'],c['original_width'],c['original_height'],im.width,im.height,sha,str(ph),score))
                global_hashes.append(ph); cat_counts[c['category']]=cat_counts.get(c['category'],0)+1
                print(f'[{collection}] {len(items):03d}/200 {c["category"]}: {c["title"]}',flush=True)
                if len(items)>=TARGET:break
    if len(items)!=TARGET:raise RuntimeError(f'{collection}: only {len(items)} accepted from {len(pool)} candidates')
    return items

def sheets(items:list[Item],collection:str):
    d=ROOT/'contact_sheets'; d.mkdir(parents=True,exist_ok=True); tw,th,cols,rows=240,160,5,5
    for si in range(math.ceil(len(items)/25)):
        canvas=Image.new('RGB',(cols*tw,rows*th),'white')
        for j,it in enumerate(items[si*25:(si+1)*25]):
            with Image.open(it.filename) as im:prev=ImageOps.fit(im.convert('RGB'),(tw,th),method=Image.Resampling.LANCZOS)
            canvas.paste(prev,((j%cols)*tw,(j//cols)*th))
        canvas.save(d/f'{collection}_{si+1:02d}.jpg',quality=82,optimize=True)

def main():
    if ROOT.exists():shutil.rmtree(ROOT)
    hashes=[]; a=harvest('luxury_motivation',LUXURY,hashes); b=harvest('stylized_3d',THREED,hashes); items=a+b
    rows=[asdict(i) for i in items]
    (ROOT/'manifest.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding='utf-8')
    with (ROOT/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    summary={'total':400,'luxury_motivation':200,'stylized_3d':200,'unique_sha256':len({i.sha256 for i in items}),
             'unique_phash':len({i.phash for i in items}),'generated_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
             'source':'Wikimedia Commons','mode':'parallel-fast-v2'}
    assert summary['unique_sha256']==400
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    sheets(a,'luxury_motivation'); sheets(b,'stylized_3d')
    (ROOT/'HARVEST_COMPLETE.md').write_text('# Harvest complete\n\n- 200 luxury / motivation images\n- 200 stylized 3D images\n- 400 unique files\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
