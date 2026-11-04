# Optimized pipeline validation

## Outline

1. Correctness and operational checks executed in Docker.
2. Repeated measurements on one real source month.
3. Evidence boundaries and delivery verification.

## Executed checks

The local Docker suite passed **55 tests**, including the preserved baseline tests,
PostgreSQL integration, all three processing engines, and download recovery through
a local HTTP server. The final benchmark script also ran the full suite successfully
before collecting samples. Application execution used containers throughout.

[GitHub CI on implementation commit 95ad5df](https://github.com/sunshine-engineer/chess_data_lakehouse/actions/runs/41831581073)
passed the Docker suite, locked dependency validation, database crash drill,
two-worker Spark check, synthetic multi-month fixture and backup/restore.
The workflow runs again for the final feature branch, pull request and merged main.
Those later results are available in the repository's Actions history.

| Check | Executed evidence |
|---|---|
| Historical corrections | Integration tests load two synthetic months, rerun/correct a month, preserve the other month, check global duplicates, title history and partition pruning |
| Durable process recovery | A child process is killed after a durable SQL checkpoint; explicit resume reuses committed work |
| Database crash recovery | [SIGKILL drill](../reports/database-crash-recovery.json): a new PostgreSQL server process resumes two logged checkpoints, preserves the prior publication, and completes the synthetic fixture |
| Remote Spark execution | [Cluster smoke report](../reports/cluster-smoke.json): tasks on two Docker workers, standalone master, semantic equality with Python on a synthetic fixture |
| Publication gates | Count/coverage checks, corrupted shard rejection and failure preservation tested against PostgreSQL |
| Database privileges | Loader denied warehouse DDL; reader denied writes; administrative migrations run separately |
| Backup/restore | [Populated warehouse report](../reports/backup-restore.json): 25 tables match row counts and row fingerprints in a disposable restored database; constraints, reader permissions and Parquet bundle extraction pass; truncated backup rejected |
| Comparable engines | [Raw comparison](../reports/comparison-v2.json): all 12 semantic comparisons pass across facts, four active dimensions and rejects; complete source histories match |
| Final persisted data | [Database readback](../reports/optimized-persistence.json): each engine has 121,332 January facts, zero rejects, seven fact foreign keys, 121,332 compatibility-view rows and 218 games with unknown players |

The local populated backup bundle is in the named backup volume at the path recorded
in its report. It is not committed. The restore drill does not overwrite the live
warehouse. It establishes local restore behavior, not off-site disaster recovery.

## Real-month benchmark

Group: `benchmark-20261104T123830-12788`. Nine fresh runs, three per engine,
with alternating engine order. Source: January 2013, **17,761,302 compressed bytes**,
121,332 input and accepted games per run, zero rejects and duplicates. All engines
retain 218 games with unknown player identities under the documented coverage policy.
Only January is present in the real-source warehouse history.

| Median measurement | Python | Multiprocessing | Spark local[2] |
|---|---:|---:|---:|
| Processing seconds | 153.830 | 123.734 | 124.586 |
| Input games/second | 788.741 | 980.586 | 973.885 |
| Client CPU seconds | 145.135 | 171.719 | 227.803 |
| Container peak MiB | 207.293 | 240.438 | 851.633 |
| PostgreSQL model bytes | 97,910,784 | 97,910,784 | 97,910,784 |
| Typed Parquet bytes | 8,891,659 | 8,891,659 | 4,758,602 |

Sample ranges were 151.799–170.520 seconds for Python, 121.083–145.405 for
multiprocessing, and 109.075–179.410 for Spark. Three samples show local variability;
they do not establish a throughput guarantee. On this fixture multiprocessing has
a similar median to Spark with substantially less client memory and CPU use.

Shared source framing took 3.268 seconds and produced two framed shards. Download
and shared framing are outside these engine timings; Spark startup/shutdown is
included. PostgreSQL resource use is outside client cgroup measurements. The optional
cluster was stopped before timing. Physical PostgreSQL sizes include indexes and
reusable pages after reruns; compact keys alone do not imply a smaller total database.

The [raw report](../reports/comparison-v2.json) includes individual samples, phase
metrics, SQL plans and compatibility checks. Frozen runtime provenance:

- Image: `sha256:a38b2cf856c3ed2b216aae84b9a43c392cb253ab745f7ac0899b7b13d41595dd`
- Code hash: `3702b2ae30ee89942541eca3e2f510b0a6e1f46fbe7cfee4c1ca9e78bb72e4aa`
- Configuration hash: `bd46528e02d335dfdf6edc455cb4e50f683ea89baf695b93289787403e294d93`

These measurements are not a controlled speedup comparison against the earlier
pipeline: preparation boundaries and missing-player acceptance rules changed.

## Boundary

All-month processing remains disabled. Actual multi-month behavior and separate
Spark worker execution were tested with synthetic data only. No 30 GB archive or
multi-year backfill was executed. At 30 GB compressed per month, raw retention alone
is approximately 360 GB/year or 1.8 TB/five years, before curated data, working space,
WAL, indexes and backups. See the [capacity validation plan](capacity-plan.md) before
making a target-scale or production service claim.
