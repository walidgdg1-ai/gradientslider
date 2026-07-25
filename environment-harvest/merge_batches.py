#!/usr/bin/env python3
"""Merge isolated CLIP harvest batches into one audited 300-environment bank."""

import csv
import json
import math
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import imagehash
from PIL import Image, ImageDraw, ImageFont

DOWNLOADED = Path("downloaded-batches")
FINAL = Path("iconic-environments-300")
FINAL_ZIP = Path("iconic-environments-300.zip")


def font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def make_contact_sheets(records):
    sheets = FINAL / "contact-sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    regular = font(15)
    heading = font(23, bold=True)
    per_page = 30
    columns = 5
    thumb_w, thumb_h, label_h, margin = 300, 170, 62, 24
    rows_per_page = math.ceil(per_page / columns)
    width = margin * 2 + columns * thumb_w
    height = 70 + margin + rows_per_page * (thumb_h + label_h)

    for page_index in range(math.ceil(len(records) / per_page)):
        batch = records[page_index * per_page:(page_index + 1) * per_page]
        canvas = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (margin, 20),
            f"Iconic Environments 300 — CLIP audit page {page_index + 1}",
            fill="black",
            font=heading,
        )
        for index, record in enumerate(batch):
            row, column = divmod(index, columns)
            x = margin + column * thumb_w
            y = 70 + row * (thumb_h + label_h)
            source = FINAL / record["filename"]
            if not source.exists():
                continue
            image = Image.open(source).convert("RGB")
            image.thumbnail((thumb_w - 8, thumb_h - 8), Image.Resampling.LANCZOS)
            tile = Image.new("RGB", (thumb_w - 8, thumb_h - 8), (235, 235, 235))
            tile.paste(image, ((tile.width - image.width) // 2, (tile.height - image.height) // 2))
            canvas.paste(tile, (x, y))
            margin_score = record.get("clip_margin")
            score_suffix = f" | CLIP {float(margin_score):+.3f}" if margin_score not in (None, "") else ""
            label = f'{int(record["priority"]):03d} {record["work"]}\n{record["location"]}{score_suffix}'
            draw.multiline_text((x, y + thumb_h - 2), label[:105], fill="black", font=regular, spacing=2)
        canvas.save(sheets / f"contact-sheet-{page_index + 1:02d}.jpg", quality=92, optimize=True)


def main():
    shutil.rmtree(FINAL, ignore_errors=True)
    FINAL.mkdir(parents=True)
    (FINAL / "logs").mkdir()

    records_by_priority = {}
    failures_by_priority = {}
    batch_summaries = []

    artifact_dirs = sorted(path for path in DOWNLOADED.iterdir() if path.is_dir()) if DOWNLOADED.exists() else []
    for artifact in artifact_dirs:
        batch_root = artifact / "iconic-environments-300"
        manifest_json = batch_root / "manifest.json"
        if manifest_json.exists():
            for record in json.loads(manifest_json.read_text(encoding="utf-8")):
                priority = int(record["priority"])
                source = batch_root / record["filename"]
                target = FINAL / record["filename"]
                if not source.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                current = records_by_priority.get(priority)
                if current is None or float(record.get("image_score", 0)) > float(current.get("image_score", 0)):
                    records_by_priority[priority] = record
        failure_csv = batch_root / "failures.csv"
        if failure_csv.exists():
            with failure_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    failures_by_priority[int(row["priority"])] = row
        readme = batch_root / "README.txt"
        if readme.exists():
            batch_summaries.append({"artifact": artifact.name, "summary": readme.read_text(encoding="utf-8")})
        for log in artifact.glob("harvest-*.log"):
            shutil.copy2(log, FINAL / "logs" / log.name)
        direct_log = artifact / "harvest.log"
        if direct_log.exists():
            shutil.copy2(direct_log, FINAL / "logs" / f"{artifact.name}.log")

    records = [records_by_priority[key] for key in sorted(records_by_priority)]
    accepted_priorities = set(records_by_priority)
    missing_priorities = [number for number in range(1, 301) if number not in accepted_priorities]

    fields = [
        "priority", "id", "category", "work", "location", "recognition_hint", "filename",
        "selection_pass", "source_page_url", "final_direct_image_url", "source_domain",
        "title", "provider", "search_query", "metadata_score", "image_score", "width",
        "height", "original_width", "original_height", "upscaled", "face_count",
        "largest_face_ratio", "total_face_ratio", "sharpness", "entropy", "sha256",
        "perceptual_hash", "ocr_word_count", "ocr_text_area_ratio", "clip_identity_score",
        "clip_scene_score", "clip_negative_score", "clip_margin", "clip_winning_negative",
    ]
    with (FINAL / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    (FINAL / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    failure_fields = ["priority", "id", "category", "work", "location", "reason"]
    failure_rows = []
    for priority in missing_priorities:
        row = failures_by_priority.get(priority)
        if row:
            failure_rows.append(row)
        else:
            failure_rows.append({
                "priority": priority,
                "id": "",
                "category": "",
                "work": "",
                "location": "",
                "reason": "batch produced no accepted record",
            })
    with (FINAL / "failures.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=failure_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(failure_rows)

    sha_groups = defaultdict(list)
    for record in records:
        if record.get("sha256"):
            sha_groups[record["sha256"]].append(int(record["priority"]))
    exact_duplicates = {key: values for key, values in sha_groups.items() if len(values) > 1}

    hashes = []
    for record in records:
        try:
            hashes.append((int(record["priority"]), imagehash.hex_to_hash(record["perceptual_hash"])))
        except Exception:
            pass
    near_duplicates = []
    for index, (priority_a, hash_a) in enumerate(hashes):
        for priority_b, hash_b in hashes[index + 1:]:
            distance = hash_a - hash_b
            if distance <= 5:
                near_duplicates.append({
                    "priority_a": priority_a,
                    "priority_b": priority_b,
                    "phash_distance": distance,
                })

    category_counts = Counter(record["category"] for record in records)
    margins = [float(record["clip_margin"]) for record in records if record.get("clip_margin") not in (None, "")]
    audit = {
        "requested": 300,
        "accepted": len(records),
        "missing_priorities": missing_priorities,
        "exact_sha_duplicate_groups": exact_duplicates,
        "near_duplicate_pairs": near_duplicates,
        "category_counts": dict(sorted(category_counts.items())),
        "clip_margin_min": min(margins) if margins else None,
        "clip_margin_mean": sum(margins) / len(margins) if margins else None,
        "clip_margin_max": max(margins) if margins else None,
    }
    (FINAL / "duplicate-and-quality-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (FINAL / "batch-summaries.json").write_text(json.dumps(batch_summaries, ensure_ascii=False, indent=2), encoding="utf-8")

    make_contact_sheets(records)

    summary = [
        "Iconic Environments 300 — CLIP visual-semantic harvest",
        f"Requested: 300",
        f"Accepted after strict metadata + OCR + CLIP validation: {len(records)}",
        f"Still missing: {len(missing_priorities)}",
        f"Exact duplicate groups: {len(exact_duplicates)}",
        f"Perceptual near-duplicate pairs: {len(near_duplicates)}",
        "",
        "Accepted by category:",
    ]
    summary.extend(f"- {category}: {count}" for category, count in sorted(category_counts.items()))
    summary.extend([
        "",
        "Missing priorities are intentionally left empty rather than filled with a wrong franchise,",
        "portrait, poster, miniature, map, HUD-heavy frame, collage, mod, or fan remake.",
    ])
    (FINAL / "README.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (FINAL / "final-summary.json").write_text(json.dumps({
        "accepted": len(records),
        "missing_count": len(missing_priorities),
        "missing_priorities": missing_priorities,
        "exact_duplicate_groups": len(exact_duplicates),
        "near_duplicate_pairs": len(near_duplicates),
    }, indent=2), encoding="utf-8")

    if FINAL_ZIP.exists():
        FINAL_ZIP.unlink()
    with zipfile.ZipFile(FINAL_ZIP, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(FINAL.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(FINAL.parent))
    print((FINAL / "README.txt").read_text(encoding="utf-8"), flush=True)


if __name__ == "__main__":
    main()
