# Portability validation

Local Docker evidence, 2026-11-08. No chosen Kubernetes cluster or real destination
database was deployed or migrated. Live Compose images were left running.

| Check | Result |
|---|---|
| Python/Spark pipeline suite | 71 passed, 21 skipped, 158.00 seconds |
| Cross-instance PostgreSQL restore + upsert suite | 34 passed, 137.43 seconds; includes original 13 restore checks and 21 upsert checks |
| Web/study/configuration suite | 95 passed, 256.61 seconds; includes real mounted-password authentication and outage probes |
| Separate foreground worker | Ready in Docker; SIGTERM with idle queues exited 0, no OOM kill |
| Python API inventory | 318 public top-level functions documented; generated index current |
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

The upsert extension additionally verifies source-wins updates (including NULLs),
retained destination-only rows/tables, composite keys, generated columns,
partition leaves, forward-only identity sequences, nullable FK cycles, actual
study/warehouse models, CLI plan/run, tracker recovery, new snapshots/jobs,
stage/baseline/target checks, and alternate-identity conflicts. Staging, merge and
FK-patch commit boundaries are interrupted before/after commit; a real process
is also SIGKILLed after an upsert commit and resumed. Existing published-history
guards remain enabled and conflicting immutable rows are rejected. Custom
triggers, incompatible/missing tables, keyless tables and unsupported FK cycles
are rejected before destination application rows are changed.

The pipeline/web counts above are the previous portability baseline. This upsert
change reran the combined database suite and strict API/Kubernetes checks; it did
not redeploy or rerun browser tests against the live application.

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

## Managed Cloud SQL execution — 2026-11-09

Live migration to the user-selected `postgres-test` instance completed using
source-wins upsert. The administrator explicitly approved its PostgreSQL 15→16
upgrade; the Cloud SQL upgrade operation completed before schema preparation.

- **20 application schemas**, **121 physical tables**, **3,586,769 rows** verified.
- Native source/destination application schema SHA256 matched before and after data
  load, covering tables, views, functions, indexes, constraints, ownership and ACLs.
- All application constraints were validated. Representative counts matched:
  Python facts 121,332; multiprocessing facts 403,928; Spark facts 121,332;
  private game revisions 25,052; snapshots 8; snapshot memberships 75,305.
- The requested `dev` login was created with a generated local password. It is
  not a superuser; ordinary ownership/permission groups retain application grants.
  Source superuser attributes and original role passwords were not copied.
- A real SIGINT interruption and checkpointed resume retained the uploaded data
  and committed upserts. The final tracker/ledger recorded **478 committed units**
  and terminal `complete` status; the separate read-only verifier passed as `dev`.
- Verification aggregates the original SHA256 modular sums on PostgreSQL rather
  than fetching every row hash across the network. Empty, single-row, duplicate
  and 10,003-row cases matched the original streaming calculation. The live copy
  also matched the original snapshot fingerprints for all 3,586,769 rows.
- **40 Docker regression tests passed** in 156.79 seconds. Strict API/Kubernetes
  checks passed with **323 documented public functions** and eight render targets.

The aggregate [execution report](../reports/cloudsql-migration.json) contains no
credentials, project ID or private game/account identifiers. Native archives,
tracking state and generated passwords remain in ignored local directories.
See [the managed migration guide](cloudsql-migration.md) for repeatable commands.

This is a database copy. The existing application still uses local PostgreSQL;
its PGN/Parquet/model/report volumes are not relocated. After restarting local
writers, Cloud SQL represents the accepted snapshot, not continuous replication.
