# Visual Harvest 400

A curated reference dataset generated from freely licensed Wikimedia Commons media.

## Target structure

- `luxury_motivation/` — exactly 200 luxury, ambition, business, travel, architecture, vehicle and success-oriented references.
- `stylized_3d/` — exactly 200 stylized 3D, CGI, game-like, character, environment, typography and abstract references.
- `contact_sheets/` — overview sheets for fast visual review.
- `manifest.csv` and `manifest.json` — source URL, author, license, dimensions, SHA-256, perceptual hash and quality score for every image.
- `summary.json` — final integrity counts.

## Curation rules

The harvester:

1. Searches across many distinct visual themes instead of pulling one repetitive query.
2. Keeps bitmap images with sufficient resolution and useful aspect ratios.
3. Normalizes files to optimized JPEGs with a maximum side of 1400 px.
4. Removes exact duplicates and near-duplicates using perceptual hashes.
5. Rejects low-detail images and common non-reference assets such as maps, flags, scans, diagrams and icons.
6. Accepts only Commons records marked as public domain or carrying a free/open license.
7. Preserves attribution and license metadata in the manifests.

The GitHub Actions workflow fails unless the final result contains exactly 400 unique files: 200 in each collection.