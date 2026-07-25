#!/usr/bin/env python3
"""Harvest 400 freely licensed visual references from Wikimedia Commons.

Creates exactly:
- 200 luxury / motivation references
- 200 stylized 3D references

The script searches Commons, downloads resized previews, normalizes to JPEG,
removes exact and near-duplicates, records attribution, and emits contact sheets.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import random
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import imagehash
import requests
from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "WalidVisualDatasetHarvester/1.0 (GitHub Actions; Wikimedia Commons attribution preserved)"
ROOT = Path("visual-harvest-400")
TARGET_PER_COLLECTION = 200
MAX_SIDE = 1400
JPEG_QUALITY = 84
MIN_WIDTH = 700
MIN_HEIGHT = 450
PHASH_DISTANCE = 5
RANDOM_SEED = 20260725

LUXURY_QUERIES = [
    ("superyacht", 'superyacht filetype:bitmap'),
    ("yacht-deck", 'luxury yacht deck filetype:bitmap'),
    ("marina", 'marina sunset luxury filetype:bitmap'),
    ("monaco", 'Monaco harbour night filetype:bitmap'),
    ("private-jet", 'business jet aircraft interior filetype:bitmap'),
    ("first-class", 'first class aircraft cabin filetype:bitmap'),
    ("helicopter", 'luxury helicopter city filetype:bitmap'),
    ("supercar", 'supercar city night filetype:bitmap'),
    ("sports-car", 'sports car mountain road filetype:bitmap'),
    ("grand-tourer", 'luxury automobile interior filetype:bitmap'),
    ("watch", 'luxury wristwatch macro filetype:bitmap'),
    ("tailoring", 'business suit fashion filetype:bitmap'),
    ("executive", 'business executive office skyline filetype:bitmap'),
    ("boardroom", 'modern boardroom skyline filetype:bitmap'),
    ("penthouse", 'penthouse interior city view filetype:bitmap'),
    ("villa", 'modern luxury villa architecture filetype:bitmap'),
    ("mansion", 'mansion interior staircase filetype:bitmap'),
    ("hotel", 'luxury hotel lobby interior filetype:bitmap'),
    ("resort", 'luxury resort infinity pool filetype:bitmap'),
    ("rooftop", 'rooftop city skyline sunset filetype:bitmap'),
    ("skyscraper", 'skyscraper city night aerial filetype:bitmap'),
    ("dubai", 'Dubai skyline night aerial filetype:bitmap'),
    ("new-york", 'New York skyline rooftop night filetype:bitmap'),
    ("architecture", 'modern luxury architecture night filetype:bitmap'),
    ("fine-dining", 'fine dining restaurant interior filetype:bitmap'),
    ("champagne", 'champagne celebration elegant filetype:bitmap'),
    ("mountain-success", 'mountain summit sunrise person filetype:bitmap'),
    ("runner", 'runner city sunrise silhouette filetype:bitmap'),
    ("work-focus", 'creative professional working night office filetype:bitmap'),
    ("travel", 'luxury travel destination aerial filetype:bitmap'),
]

THREED_QUERIES = [
    ("blender", 'Blender 3D render filetype:bitmap'),
    ("cgi", 'computer generated image 3D render filetype:bitmap'),
    ("abstract", 'abstract 3D rendering filetype:bitmap'),
    ("low-poly", 'low poly 3D art filetype:bitmap'),
    ("voxel", 'voxel art 3D filetype:bitmap'),
    ("isometric", 'isometric 3D illustration filetype:bitmap'),
    ("typography", '3D typography render filetype:bitmap'),
    ("surreal", 'surreal 3D render digital art filetype:bitmap'),
    ("scifi-city", 'science fiction city 3D render filetype:bitmap'),
    ("cyberpunk", 'cyberpunk 3D render filetype:bitmap'),
    ("fantasy", 'fantasy landscape 3D render filetype:bitmap'),
    ("game-environment", 'video game environment 3D render filetype:bitmap'),
    ("architecture", 'architectural visualization 3D render filetype:bitmap'),
    ("interior", '3D interior visualization render filetype:bitmap'),
    ("character", '3D character render filetype:bitmap'),
    ("robot", 'robot 3D render filetype:bitmap'),
    ("creature", 'creature 3D model render filetype:bitmap'),
    ("spaceship", 'spaceship 3D render filetype:bitmap'),
    ("vehicle", 'concept vehicle 3D render filetype:bitmap'),
    ("product", 'product visualization 3D render filetype:bitmap'),
    ("procedural", 'procedural 3D art filetype:bitmap'),
    ("fractal", '3D fractal render filetype:bitmap'),
    ("raytrace", 'ray tracing 3D render filetype:bitmap'),
    ("landscape", 'digital landscape 3D render filetype:bitmap'),
    ("retro-game", 'retro 3D game screenshot filetype:bitmap'),
    ("stylized", 'stylized 3D scene filetype:bitmap'),
    ("clay", 'clay render 3D filetype:bitmap'),
    ("neon", 'neon 3D render filetype:bitmap'),
    ("space", 'space station 3D render filetype:bitmap'),
    ("cinematic", 'cinematic 3D scene render filetype:bitmap'),
]

BLOCKED_TITLE_TERMS = {
    "map", "diagram", "chart", "coat of arms", "flag", "logo", "icon",
    "signature", "scan", "document", "poster", "book cover", "stamp",
    "coin", "banknote", "screenshot of", "qr code", "barcode",
}

@dataclass
class Item:
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
    description: str
    original_width: int
    original_height: int
    saved_width: int
    saved_height: int
    sha256: str
    phash: str
    quality_score: float


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", text).strip()


def meta_value(ext: dict[str, Any], key: str) -> str:
    value = ext.get(key, {})
    if isinstance(value, dict):
        return clean_html(str(value.get("value", "")))
    return clean_html(str(value))


def allowed_license(name: str) -> bool:
    n = name.lower().strip()
    return any(token in n for token in ("public domain", "cc0", "cc by", "creative commons", "gfdl"))


def commons_search(session: requests.Session, query: str, pages: int = 5) -> Iterable[dict[str, Any]]:
    offset = 0
    for _ in range(pages):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": 50,
            "gsroffset": offset,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 1800,
        }
        payload: dict[str, Any] = {}
        for attempt in range(5):
            try:
                response = session.get(API, params=params, timeout=45)
                response.raise_for_status()
                payload = response.json()
                break
            except Exception:
                if attempt < 4:
                    time.sleep(2 ** attempt)
        pages_out = payload.get("query", {}).get("pages", [])
        if not pages_out:
            return
        random.shuffle(pages_out)
        yield from pages_out
        cont = payload.get("continue", {})
        if "gsroffset" not in cont:
            return
        offset = int(cont["gsroffset"])


def download_bytes(session: requests.Session, url: str) -> bytes | None:
    for attempt in range(5):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            if len(r.content) < 20_000:
                return None
            return r.content
        except Exception:
            if attempt < 4:
                time.sleep(2 ** attempt)
    return None


def normalized_image(raw: bytes) -> Image.Image | None:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if image.width < MIN_WIDTH or image.height < MIN_HEIGHT:
        return None
    ratio = image.width / image.height
    if ratio < 0.45 or ratio > 2.4:
        return None
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return image


def image_quality(image: Image.Image) -> float:
    sample = image.copy()
    sample.thumbnail((400, 400), Image.Resampling.BILINEAR)
    gray = sample.convert("L")
    contrast = ImageStat.Stat(gray).stddev[0]
    sharpness = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]
    rgb_stat = ImageStat.Stat(sample)
    saturation_proxy = max(rgb_stat.mean) - min(rgb_stat.mean)
    megapixels = image.width * image.height / 1_000_000
    return round(contrast * 0.48 + sharpness * 0.30 + saturation_proxy * 0.12 + min(megapixels, 3) * 4, 3)


def duplicate_hash(candidate: imagehash.ImageHash, hashes: list[imagehash.ImageHash]) -> bool:
    return any(candidate - previous <= PHASH_DISTANCE for previous in hashes)


def safe_stem(title: str) -> str:
    title = re.sub(r"^File:", "", title, flags=re.I)
    title = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", title)
    title = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    return title[:70] or "image"


def candidate_from_page(page: dict[str, Any], category: str, collection: str) -> dict[str, Any] | None:
    title = str(page.get("title", ""))
    title_lower = title.lower()
    if any(term in title_lower for term in BLOCKED_TITLE_TERMS):
        return None
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    mime = str(info.get("mime", ""))
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return None
    width, height = int(info.get("width", 0)), int(info.get("height", 0))
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return None
    ext = info.get("extmetadata", {}) or {}
    license_name = meta_value(ext, "LicenseShortName") or meta_value(ext, "UsageTerms")
    if not allowed_license(license_name):
        return None
    source_url = info.get("thumburl") or info.get("url")
    if not source_url:
        return None
    return {
        "collection": collection,
        "category": category,
        "title": title,
        "page_url": info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{quote(title.replace(' ', '_'))}",
        "source_url": source_url,
        "author": meta_value(ext, "Artist") or "See Wikimedia Commons page",
        "license": license_name,
        "license_url": meta_value(ext, "LicenseUrl"),
        "description": meta_value(ext, "ImageDescription") or meta_value(ext, "ObjectName"),
        "original_width": width,
        "original_height": height,
    }


def save_jpeg(image: Image.Image, path: Path) -> tuple[str, int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, image.width, image.height


def accept_candidate(session: requests.Session, candidate: dict[str, Any], accepted: list[Item], hashes: list[imagehash.ImageHash]) -> bool:
    raw = download_bytes(session, candidate["source_url"])
    if not raw:
        return False
    image = normalized_image(raw)
    if image is None:
        return False
    ph = imagehash.phash(image)
    if duplicate_hash(ph, hashes):
        return False
    score = image_quality(image)
    if score < 10.0:
        return False
    index = len(accepted) + 1
    category = candidate["category"]
    filename = f"{index:03d}_{category}_{safe_stem(candidate['title'])}.jpg"
    path = ROOT / candidate["collection"] / filename
    sha256, saved_w, saved_h = save_jpeg(image, path)
    accepted.append(Item(
        collection=candidate["collection"], index=index, category=category,
        filename=str(path.as_posix()), commons_title=candidate["title"],
        commons_page=candidate["page_url"], source_url=candidate["source_url"],
        author=candidate["author"], license=candidate["license"],
        license_url=candidate["license_url"], description=candidate["description"],
        original_width=candidate["original_width"], original_height=candidate["original_height"],
        saved_width=saved_w, saved_height=saved_h, sha256=sha256,
        phash=str(ph), quality_score=score,
    ))
    hashes.append(ph)
    print(f"[{candidate['collection']}] {len(accepted):03d}/{TARGET_PER_COLLECTION} {category}: {candidate['title']}", flush=True)
    return True


def harvest_collection(session: requests.Session, collection: str, queries: list[tuple[str, str]], hashes: list[imagehash.ImageHash]) -> list[Item]:
    accepted: list[Item] = []
    seen_pages: set[str] = set()
    per_category: dict[str, int] = {}
    for pages, category_cap in [(3, 8), (6, 14), (10, 25)]:
        randomized = queries[:]
        random.shuffle(randomized)
        for category, query in randomized:
            if len(accepted) >= TARGET_PER_COLLECTION:
                break
            if per_category.get(category, 0) >= category_cap:
                continue
            for page in commons_search(session, query, pages=pages):
                if len(accepted) >= TARGET_PER_COLLECTION or per_category.get(category, 0) >= category_cap:
                    break
                candidate = candidate_from_page(page, category, collection)
                if not candidate or candidate["page_url"] in seen_pages:
                    continue
                seen_pages.add(candidate["page_url"])
                if accept_candidate(session, candidate, accepted, hashes):
                    per_category[category] = per_category.get(category, 0) + 1
        if len(accepted) >= TARGET_PER_COLLECTION:
            break

    if len(accepted) < TARGET_PER_COLLECTION:
        fallback = ('luxury OR success OR skyline OR yacht OR supercar filetype:bitmap' if collection == "luxury_motivation" else '"3D render" OR CGI OR "computer generated" OR Blender filetype:bitmap')
        for page in commons_search(session, fallback, pages=30):
            if len(accepted) >= TARGET_PER_COLLECTION:
                break
            candidate = candidate_from_page(page, "fallback", collection)
            if not candidate or candidate["page_url"] in seen_pages:
                continue
            seen_pages.add(candidate["page_url"])
            accept_candidate(session, candidate, accepted, hashes)

    if len(accepted) != TARGET_PER_COLLECTION:
        raise RuntimeError(f"Could only harvest {len(accepted)} images for {collection}; expected {TARGET_PER_COLLECTION}")
    return accepted


def make_contact_sheets(items: list[Item], collection: str) -> None:
    thumb_w, thumb_h = 240, 160
    cols, rows = 5, 5
    per_sheet = cols * rows
    sheets_dir = ROOT / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    for sheet_index in range(math.ceil(len(items) / per_sheet)):
        chunk = items[sheet_index * per_sheet:(sheet_index + 1) * per_sheet]
        canvas = Image.new("RGB", (cols * thumb_w, rows * thumb_h), "white")
        for i, item in enumerate(chunk):
            with Image.open(item.filename) as image:
                preview = ImageOps.fit(image.convert("RGB"), (thumb_w, thumb_h), method=Image.Resampling.LANCZOS)
            canvas.paste(preview, ((i % cols) * thumb_w, (i // cols) * thumb_h))
        canvas.save(sheets_dir / f"{collection}_{sheet_index + 1:02d}.jpg", quality=82, optimize=True)


def write_manifests(items: list[Item]) -> None:
    rows = [asdict(item) for item in items]
    (ROOT / "manifest.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    with (ROOT / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "total": len(items),
        "luxury_motivation": sum(i.collection == "luxury_motivation" for i in items),
        "stylized_3d": sum(i.collection == "stylized_3d" for i in items),
        "unique_sha256": len({i.sha256 for i in items}),
        "unique_phash": len({i.phash for i in items}),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Wikimedia Commons",
        "license_policy": "Only files whose Commons metadata identifies a free/open license or public-domain status.",
    }
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> int:
    random.seed(RANDOM_SEED)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if ROOT.exists():
        import shutil
        shutil.rmtree(ROOT)
    hashes: list[imagehash.ImageHash] = []
    luxury = harvest_collection(session, "luxury_motivation", LUXURY_QUERIES, hashes)
    three_d = harvest_collection(session, "stylized_3d", THREED_QUERIES, hashes)
    all_items = luxury + three_d
    write_manifests(all_items)
    make_contact_sheets(luxury, "luxury_motivation")
    make_contact_sheets(three_d, "stylized_3d")
    assert len(all_items) == 400
    assert len({i.sha256 for i in all_items}) == 400
    print(json.dumps({"harvested": 400, "luxury_motivation": 200, "stylized_3d": 200}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
