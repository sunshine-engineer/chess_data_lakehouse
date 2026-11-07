#!/usr/bin/env bash
# Opt-in Jan–Mar workload, isolated from the main app and its private libraries.
set -euo pipefail
cd "$(dirname "$0")/.."
repeats="${1:-2}"
[[ "$repeats" =~ ^[1-3]$ ]] || { echo 'Use 1..3 full workload repeats' >&2; exit 2; }
project="chess-archive-scale"
export CHESS_IMAGE_ID
export CHESS_SCALE_IMAGE
if [[ -n "${2:-}" ]]; then
  group=$2
  CHESS_SCALE_IMAGE=${3:?Continuing a workload requires its original pinned image tag}
  [[ "$group" =~ ^scale-[0-9]{8}T[0-9]{6}$ ]] || { echo 'Invalid workload group' >&2; exit 2; }
  [[ "$CHESS_SCALE_IMAGE" =~ ^chess-lakehouse-scale:[0-9]{8}T[0-9]{6}$ ]] || { echo 'Use the recorded pinned image tag' >&2; exit 2; }
else
  docker compose build python
  CHESS_IMAGE_ID=$(docker image inspect chess-lakehouse-runner:local --format '{{.Id}}')
  # Subsequent app builds cannot change a running experiment.
  CHESS_SCALE_IMAGE="chess-lakehouse-scale:$(date -u +%Y%m%dT%H%M%S)"
  docker tag "$CHESS_IMAGE_ID" "$CHESS_SCALE_IMAGE"
  group="scale-$(date -u +%Y%m%dT%H%M%S)"
fi
CHESS_IMAGE_ID=$(docker image inspect "$CHESS_SCALE_IMAGE" --format '{{.Id}}')
dc=(docker compose -p "$project" -f compose.yaml -f compose.scale.yaml)
mkdir -p reports/scale
"${dc[@]}" up -d --wait postgres
# This SQL is shell-quoted and accepts only validated fixed-format group/image
# identifiers and the engine/month literals in the loops below. No eval/source.
scalar(){ "${dc[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XqAt -v ON_ERROR_STOP=1 -c "$1"' sh "$1"; }
if [[ -n "${2:-}" ]]; then
  changed=$(scalar "SELECT count(*) FROM control.pipeline_runs WHERE benchmark_group='$group' AND status='complete' AND image_id<>'$CHESS_IMAGE_ID'")
  [[ "$changed" == 0 ]] || { echo 'Recorded workload used a different image; do not mix measurements' >&2; exit 1; }
  active=$(scalar "SELECT count(*) FROM control.pipeline_runs WHERE benchmark_group='$group' AND status='running'")
  [[ "$active" == 0 ]] || { echo 'Inspect running jobs first. Explicitly abandon confirmed stopped jobs before continuing.' >&2; exit 1; }
fi
printf '{"group":"%s","image_tag":"%s","image_id":"%s","repeats":%s,"months":["2013-01","2013-02","2013-03"]}\n' "$group" "$CHESS_SCALE_IMAGE" "$CHESS_IMAGE_ID" "$repeats" > "reports/scale/workload-$group.json"
for month in 2013-01 2013-02 2013-03; do
  "${dc[@]}" run --rm python prepare --config config/archive-demo.toml --month "$month"
done
for ((round=1;round<=repeats;round++)); do
  if ((round%2)); then engines=(python python-mp spark); else engines=(spark python-mp python); fi
  for service in "${engines[@]}"; do
    engine=${service//-/_}
    for month in 2013-01 2013-02 2013-03; do
      complete=$(scalar "SELECT count(*) FROM control.pipeline_runs WHERE benchmark_group='$group' AND engine='$engine' AND source_month='$month-01' AND status='complete' AND report->>'resumed'='false'")
      [[ "$complete" =~ ^[0-9]+$ && "$complete" -le "$repeats" ]] || { echo 'Unexpected completed-run count' >&2; exit 1; }
      if ((complete>=round)); then printf 'Retaining completed repeat %s: %s/%s\n' "$round" "$engine" "$month"; continue; fi
      "${dc[@]}" run --rm "$service" run --config config/archive-demo.toml --month "$month" --engine "$engine" --benchmark-group "$group"
    done
  done
done
"${dc[@]}" run --rm compare compare --config config/archive-demo.toml --benchmark-group "$group"
printf 'Complete isolated workload: %s\n' "$group"
