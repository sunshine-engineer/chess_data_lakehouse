#!/usr/bin/env bash
# All project code executes in containers. Runs alternate order to reduce cache bias.
set -euo pipefail
cd "$(dirname "$0")/.."
repeats="${1:-3}"
if [[ ! "$repeats" =~ ^([1-9]|10)$ ]]; then
  echo 'Usage: bash scripts/benchmark.sh [1..10 repeats per engine]' >&2
  exit 2
fi
docker compose build python
docker compose up -d --wait postgres
docker compose run --rm tests
docker compose run --rm fetch
for ((round=1; round<=repeats; round++)); do
  if ((round % 2)); then
    docker compose run --rm python
    docker compose run --rm spark
  else
    docker compose run --rm spark
    docker compose run --rm python
  fi
done
docker compose run --rm compare
