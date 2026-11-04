#!/usr/bin/env bash
# All application code runs in Docker; common source preparation is a separate phase.
set -euo pipefail
cd "$(dirname "$0")/.."
repeats="${1:-3}"
if [[ ! "$repeats" =~ ^([1-9]|10)$ ]]; then
  echo 'Usage: bash scripts/benchmark.sh [1..10 repeats per engine]' >&2
  exit 2
fi
docker compose build python
export CHESS_IMAGE_ID
auto_image=$(docker compose --profile tools config --images | awk '/chess-lakehouse-runner/ {print;exit}')
CHESS_IMAGE_ID=$(docker image inspect "$auto_image" --format '{{.Id}}')
group="benchmark-$(date -u +%Y%m%dT%H%M%S)-${RANDOM}"
docker compose up -d --wait postgres
docker compose run --rm tests
docker compose run --rm python prepare
for ((round=1; round<=repeats; round++)); do
  if ((round % 2)); then engines=(python python-mp spark); else engines=(spark python-mp python); fi
  for service in "${engines[@]}"; do
    case "$service" in python-mp) engine=python_mp;; *) engine=$service;; esac
    docker compose run --rm "$service" run --engine "$engine" --benchmark-group "$group"
  done
done
docker compose run --rm compare compare --benchmark-group "$group"
printf 'Measured benchmark group: %s\n' "$group"
