#!/usr/bin/env bash
# Explicit selections only; validates every month before downloading anything.
set -euo pipefail
cd "$(dirname "$0")/.."
months="${1:?Usage: bash scripts/import-archives.sh YYYY-MM[,YYYY-MM] [python|python_mp|spark]}"
engine="${2:-python_mp}"
case "$engine" in python|python_mp|spark) ;; *) echo 'Unsupported engine' >&2; exit 2;; esac
docker compose build python
service=${engine//_/-}
docker compose run --rm "$service" backfill --config config/archive-demo.toml --months "$months" --engine "$engine"
