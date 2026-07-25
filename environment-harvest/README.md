# Iconic Environments 300

Execution-only public GitHub Actions harvest for 300 instantly recognizable environments.

## Curated allocation

- 70 video-game environments
- 70 film environments
- 50 television-series environments
- 30 western-animation environments
- 30 anime environments
- 15 music-video environments
- 15 television/YouTube sets
- 20 real-world and documentary locations

## Execution architecture

The workflow validates a continuous catalog of priorities 1–300, then runs ten isolated batches of 30 environments. Each batch performs exact work/location metadata matching, landscape and resolution checks, face-area rejection, sharpness and entropy checks, SHA-256 deduplication, and perceptual-hash deduplication within the batch.

A final job merges all batch outputs into the `iconic-environments-300` artifact containing:

- one numbered JPEG per accepted environment
- `iconic-environments-300.zip`
- CSV and JSON manifests with source URLs and quality metrics
- a failures report
- contact sheets
- all ten batch logs

This branch is isolated from `main` and exists only to execute and retrieve the harvest artifact.
