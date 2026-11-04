#!/usr/bin/env bash
# Administrative test: unclean restart of only this project's PostgreSQL instance.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm --entrypoint python tests -m chess_lakehouse.optimized.crash_drill prepare
docker compose kill -s SIGKILL postgres
docker compose up -d --wait postgres
docker compose run --rm --entrypoint python tests -m chess_lakehouse.optimized.crash_drill resume
