#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import random
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image, ImageOps

API = "https://commons.wikimedia.org/w/api.php"
UA = "WalidFinalVisualHarvester/4.0 (GitHub Actions; contact via github.com/walidgdg1-ai)"
ROOT = Path("visual-harvest-400")
TARGET = 200
MAX_SIDE = 1280
random.seed(20260725)

LUXURY_QUERIES = [
    ("superyacht", "superyacht"), ("yacht", "luxury yacht"), ("marina", "marina sunset"),
    ("monaco", "Monaco harbour"), ("jet", "private jet interior"), ("first-class", "first class cabin"),
    ("supercar", "supercar"), ("sports-car", "sports car night"), ("watch", "luxury watch"),
    ("penthouse", "penthouse interior"), ("villa", "modern luxury villa"), ("mansion", "mansion interior"),
    ("hotel", "luxury hotel lobby"), ("resort", "infinity pool resort"), ("rooftop", "rooftop skyline sunset"),
    ("skyline", "city skyline night"), ("dubai", "Dubai skyline night"), ("new-york", "New York skyline night"),
    ("office", "executive office skyline"), ("boardroom", "modern boardroom"), ("architecture", "modern architecture night"),
    ("fine-dining", "fine dining interior"), ("mountain", "mountain summit sunrise"),
    ("business", "business person city skyline"), ("travel", "luxury travel")
]

THREED_QUERIES = [
    ("blender", "Blender 3D render"), ("cgi", "computer generated imagery"), ("render", "3D rendering"),
    ("low-poly", "low poly 3D"), ("voxel", "voxel art"), ("isometric", "isometric 3D"),
    ("abstract", "abstract 3D render"), ("fractal", "3D fractal"), ("raytrace", "ray tracing render"),
    ("scifi", "science fiction 3D render"), ("space", "spaceship 3D render"), ("robot", "robot 3D render"),
    ("character", "3D character model render"), ("creature", "3D creature model"),
    ("architecture", "architectural visualization 3D"), ("interior", "3D interior visualization"),
    ("game", "video game screenshot 3D"), ("game-environment", "3D game environment"),
    ("vehicle", "3D vehicle model render"), ("product", "3D product visualization"),
    ("terrain", "3D terrain render"), ("typography", "3D typography"), ("surreal", "surreal 3D digital art"),
    ("neon", "neon 3D render"), ("cinematic", "cinematic 3D scene")
]

BLOCK = ("map", "diagram", "chart", "flag", "coat of arms", "logo", "signature", "scan", "document", "book page", "stamp", "coin", "banknote", "qr code", "barcode")

@dataclass
class Record:
    collection: str
    index: int
    category: str
    filename: str
    commons_title: str
    commons_page: str
    source_url: str
    author: str
    license: str
    license_url: str
    original_width: int
    original_height: int
    saved_width: int
    saved_height: int
    sha256: str


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def meta(ext: dict[str, Any], key: str) -> str:
    value = ext.get(key, {})
    if isinstance(value, dict):
        value = value.get("value", "")
    return strip_html(str(value))


def licensed(name: str) -> bool:
    x = name.lower()
    return any(v in x for v in ("public domain", "cc0", "cc by", "creative commons", "gfdl"))


def search(query: str, offset: int) -> tuple[list[dict[str, Any]], int | None]:
    params = {
        "action": "query", "format": "json", "formatversion": 2,
        "generator": "search", "gsrsearch": query + " filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": 50, "gsroffset": offset,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280,
    }
    for attempt in range(6):
        try:
            r = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=40)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "8")) + 1)
                continue
            r.raise_for_status()
            data = r.json()
            pages = data.get("query", {}).get("pages", [])
            nxt = data.get("continue", {}).get("gsroffset")
            return pages, int(nxt) if nxt is not None else None
        except Exception:
            time.sleep(min(12, 2 ** attempt))
    return [], None


def make_candidate(page: dict[str, Any], category: str) -> dict[str, Any] | None:
    title = str(page.get("title", ""))
    lower = title.lower()
    if any(b in lower for b in BLOCK):
        return None
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    if str(info.get("mime", "")) not in {"image/jpeg", "image/png", "image/webp"}:
        return None
    w, h = int(info.get("width", 0)), int(info.get("height", 0))
    if w < 600 or h < 400:
        return None
    ext = info.get("extmetadata", {}) or {}
    lic = meta(ext, "LicenseShortName") or meta(ext, "UsageTerms")
    if not licensed(lic):
        return None
    url = info.get("thumburl") or info.get("url")
    if not url:
        return None
    return {
        "category": category, "title": title, "url": url,
        "page": info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{quote(title.replace(' ', '_'))}",
        "author": meta(ext, "Artist") or "See Commons page", "license": lic,
        "license_url": meta(ext, "LicenseUrl"), "width": w, "height": h,
    }


def gather(queries: list[tuple[str, str]]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for category, query in queries:
        offset = 0
        for _ in range(5):
            pages, nxt = search(query, offset)
            for page in pages:
                c = make_candidate(page, category)
                if c:
                    found[c["page"]] = c
            if nxt is None:
                break
            offset = nxt
    values = list(found.values())
    random.shuffle(values)
    return values


def download(c: dict[str, Any]) -> tuple[dict[str, Any], Image.Image] | None:
    headers = {"User-Agent": UA, "Referer": "https://commons.wikimedia.org/", "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*"}
    for attempt in range(10):
        try:
            r = requests.get(c["url"], headers=headers, timeout=50)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "11")) + 1)
                continue
            r.raise_for_status()
            if len(r.content) < 5000:
                return None
            with Image.open(io.BytesIO(r.content)) as src:
                src.load()
                im = ImageOps.exif_transpose(src).convert("RGB")
            if im.width < 500 or im.height < 350:
                return None
            ratio = im.width / im.height
            if ratio < 0.4 or ratio > 2.8:
                return None
            if max(im.size) > MAX_SIDE:
                im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            return c, im
        except Exception:
            time.sleep(min(12, 2 ** attempt))
    return None


def slug(title: str) -> str:
    title = re.sub(r"^File:", "", title, flags=re.I)
    title = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", title)
    return (re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:70] or "image")


def harvest(collection: str, queries: list[tuple[str, str]], global_sha: set[str]) -> list[Record]:
    pool = gather(queries)
    if len(pool) < TARGET:
        raise RuntimeError(f"{collection}: candidate pool too small ({len(pool)})")
    out_dir = ROOT / collection
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[Record] = []
    cursor = 0
    category_counts: dict[str, int] = {}
    while len(records) < TARGET and cursor < len(pool):
        batch = pool[cursor:cursor + 5]
        cursor += 5
        results = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(download, c) for c in batch]
            for f in as_completed(futures):
                value = f.result()
                if value:
                    results.append(value)
        for c, im in results:
            if len(records) >= TARGET:
                break
            if len(records) < 175 and category_counts.get(c["category"], 0) >= 14:
                continue
            buffer = io.BytesIO()
            im.save(buffer, "JPEG", quality=85, optimize=True, progressive=True)
            payload = buffer.getvalue()
            sha = hashlib.sha256(payload).hexdigest()
            if sha in global_sha:
                continue
            idx = len(records) + 1
            name = f"{idx:03d}_{c['category']}_{slug(c['title'])}.jpg"
            path = out_dir / name
            path.write_bytes(payload)
            global_sha.add(sha)
            category_counts[c["category"]] = category_counts.get(c["category"], 0) + 1
            records.append(Record(collection, idx, c["category"], path.as_posix(), c["title"], c["page"], c["url"], c["author"], c["license"], c["license_url"], c["width"], c["height"], im.width, im.height, sha))
            print(f"[{collection}] {idx:03d}/200 {c['category']} {c['title']}", flush=True)
        time.sleep(12)
    if len(records) != TARGET:
        raise RuntimeError(f"{collection}: only {len(records)} accepted from {len(pool)} candidates")
    return records


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    sha: set[str] = set()
    luxury = harvest("luxury_motivation", LUXURY_QUERIES, sha)
    threed = harvest("stylized_3d", THREED_QUERIES, sha)
    records = luxury + threed
    rows = [asdict(r) for r in records]
    (ROOT / "manifest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (ROOT / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader(); writer.writerows(rows)
    summary = {
        "total": len(records), "luxury_motivation": len(luxury), "stylized_3d": len(threed),
        "unique_sha256": len(sha), "source": "Wikimedia Commons", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    assert summary == {**summary, "total": 400, "luxury_motivation": 200, "stylized_3d": 200, "unique_sha256": 400}
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (ROOT / "HARVEST_COMPLETE.md").write_text("# Harvest complete\n\n- 200 luxury/motivation images\n- 200 stylized 3D images\n- 400 unique files\n- Attribution in manifest.csv and manifest.json\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)

if __name__ == "__main__":
    main()
