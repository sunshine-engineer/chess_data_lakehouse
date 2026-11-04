# Historical pipeline optimization delivery

## Outline and acceptance gates

The feature branch must pass these gates before merging to main. Application code,
tests, benchmarks, and operational checks execute in Docker. Real source execution
remains limited to January 2013; multi-month tests use synthetic fixtures.

| Requirement | Completion evidence required |
|---|---|
| Historical correctness | Load A and B, rerun/correct A, preserve B; enforce one global game identity per engine and monthly partition pruning |
| Compact, historical models | Integer warehouse keys, stable binary natural hashes, DATE archive keys, titles as played, explicit unknown identities and source/run lineage |
| Bounded typed storage | Byte-sized framed Parquet and typed transformed Parquet; independent file, batch, partition and writer settings; no nested per-game JSONB |
| Recovery | Immutable shard checksums, durable load checkpoints, explicit resume after process/database interruption, stale-run recovery, unchanged published data on failure |
| Quality gates | Count reconciliation, rejection/unknown-identity thresholds, schema checks, corrupted-shard rejection before publication |
| Engine optimization | Serial Python, bounded multiprocessing Python, native Spark column transforms after common PGN parsing; configurable Spark master/parallelism and bounded database writers |
| Reproducible benchmarks | Fresh comparable run groups, per-run code/config/image/runtime metadata, real cgroup limits, per-phase metrics, database/model sizes, query plans and semantic equality |
| Operations | Structured progress events, disk checks, timeouts, Spark event logs and manual bounded backfill controls; all-month processing disabled |
| Security and delivery | Non-root jobs, separate migration/loader/reader privileges, dependency lock, migration versioning and Docker CI |
| Backup and restore | Containerized dump and restore into disposable database with reconciliation and constraints validated |
| Documentation | Architecture, models, recovery/backup runbooks, measured case studies, limitations and commands match executed behavior |
| Git delivery | Feature commits, passing required local/container and remote CI checks, authorized merge to main and verified final repository state |

## Implementation evidence

The implementation and operational gates above are covered by the **55 passing
Docker tests**, nine real-month benchmark samples, and the executed recovery,
standalone Spark and populated backup/restore drills. See
[validation and report links](optimized-validation.md),
[operating commands and models](optimized-pipeline.md), and
[measured case studies](case-studies.md). Final Git delivery is verified through
the feature pull request and CI on the resulting main commit.

Target-scale capacity is deliberately unclaimed: the original one-real-month
execution boundary remains active. Follow the [capacity plan](capacity-plan.md)
for a separately approved representative 30 GB compressed archive test.

## Modeling defaults

- Keep original archives and validated source occurrences in Parquet. Warehouse facts
  have one canonical game across all archive months for each engine.
- First successfully published archive owns a game ID; cross-month repeats are counted
  as duplicates. Replacing an archive refreshes its own partition atomically.
- Player hashes are stable natural identifiers; warehouse fact joins use BIGINT keys.
- Facts retain the title observed in that game. Current player titles are derived
  from the retained observations, including corrections and older backfills.
- Missing player names use a reserved unknown member, with explicit coverage metrics.
  Illegal PGN, invalid identifiers/results/dates/ratings remain rejects.
- Framing a non-seekable Zstandard source may require rescanning it after interruption;
  committed shards and downstream parse/load work remain reusable and verified.
- No claim of validated 30 GB/month or five-year production capacity is made without
  representative load evidence. Target-scale capacity remains a separate benchmark gate.
