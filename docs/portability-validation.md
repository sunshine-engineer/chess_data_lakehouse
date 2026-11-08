# Portability validation

Local Docker evidence, 2026-11-08. No chosen Kubernetes cluster or real destination
database was deployed or migrated. Live Compose images were left running.

| Check | Result |
|---|---|
| Python/Spark pipeline suite | 71 passed, 21 skipped, 158.00 seconds |
| Cross-instance PostgreSQL transfer suite | 13 passed, 143.45 seconds |
| Web/study/configuration suite | 95 passed, 256.61 seconds; includes real mounted-password authentication and outage probes |
| Separate foreground worker | Ready in Docker; SIGTERM with idle queues exited 0, no OOM kill |
| Python API inventory | 309 public top-level functions documented; generated index current |
| Kubernetes | All eight Kustomize targets rendered; strict Kubernetes 1.36 schema validation passed |
| Compose | Base, standalone-worker override and isolated QA configuration parsed successfully |
| Packaged migration CLI | Help/argument entrypoint passed inside the built migration image |
| Dependencies | Web and migration images: pip check passed; stale host egg-info excluded from build context |
| Live study preservation | Original 25,052 revisions, 8 snapshots, 75,305 memberships, 1 saved study, 6 game analyses preserved; no row changes |

The pipeline-only image intentionally skips Flask/ML/Stockfish tests and the
cross-instance tests without SOURCE_/TARGET_ endpoints. Separate suites cover
those environments. No skipped check is counted as a pass.

The transfer tests use two distinct PostgreSQL 16 containers and disposable
databases. They cover native DDL, partitioned tables, identity/generated values,
arrays/JSONB/BYTEA, unicode/newlines, duplicates, NULLs, zero-column and empty
tables, functions, views/materialized views, indexes, RLS, large objects, unlogged
tables, sequence values, database settings/comments and the repository's actual
SQL migrations/role ACLs. They exercise crashes before/after COPY and native DDL
commits, **real SIGKILL before/after COPY commit**, idempotent rerun, corrupted
tracker recovery, corrupted archive rejection, changed source/target rejection,
and refusal to overwrite populated destinations (including function-only ones).

Relevant reproducible commands, all execution in containers:

```bash
docker build -f docker/ops.Dockerfile -t chess-lakehouse-ops:local .
docker run --rm -v "$PWD:/workspace:ro" chess-lakehouse-ops:local
docker build -f docker/Dockerfile -t chess-lakehouse-runner:portability .
docker build -f docker/web.Dockerfile -t chess-lakehouse-web:portability .
docker compose -f compose.portability-qa.yaml run --rm pipeline-tests
# Run sequentially: bootstrap tests rotate cluster-wide synthetic roles.
docker compose -f compose.portability-qa.yaml run --rm web-tests
docker compose -f compose.portability-qa.yaml up -d --wait study-worker
docker compose -f compose.portability-qa.yaml stop study-worker
docker compose -f compose.migration-qa.yaml build tests
docker compose -f compose.migration-qa.yaml run --rm tests
```

The new CI workflow encodes these checks. Local execution does not establish a
remote CI result; this branch has not been published for this task.

Acceptance still required on the selected target: storage binding/permissions and
flock semantics, registry access, admission/network policy behavior, TLS/ingress,
actual state copy and checksum verification, real data migration capacity/time,
application cutover and rollback rehearsal, observability and backup/PITR policy.
The small integration fixtures establish correctness paths, not 30 GB/month
throughput or a five-year maintenance-window duration.
