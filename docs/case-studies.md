# Portfolio case studies

## Outline

1. Correct historical data across monthly reruns and corrections.
2. Recover interrupted processing without publishing partial results.
3. Compare serial, process-based and Spark execution using reproducible evidence.

## Historical correctness

**Problem:** The original demo published a view of the latest run. It could not
answer historical queries across archive months, and per-run identities did not
enforce uniqueness across months.

**Change:** A partitioned historical fact table, shared dimensions, and a global
game registry now support atomic source-month refreshes. Stable binary hashes map
to compact warehouse integer keys. Facts retain both players' titles as observed in
the game, while current dimension titles are recomputed when affected months change.

**Evidence:** Container integration tests load A and B, reload A, correct B, and
verify that historical facts, global identities, current titles, compatibility views
and partition pruning stay correct. See `test_history_rerun_corrections_and_global_identity`
in `tests/test_v2_postgres.py` and the [delivery evidence](optimized-validation.md).

**Trade-off:** First published archive ownership is explicit. Cross-archive repeated
game IDs are duplicates, and changing backfill order may change source provenance.
Unused dimension members are retained to preserve stable warehouse keys. Extra
indexes, identity constraints and lineage can offset the storage savings from
smaller join keys; total database size is measured rather than assumed smaller.

## Recovery and quality

**Problem:** Restarting the original pipeline repeated the full parse/load. Its
UNLOGGED staging was useful transient storage, not a durable crash checkpoint.

**Change:** Framed and transformed Parquet shards have checksums and atomic
manifests. SQL data and shard markers commit together in logged staging. Explicit
resume verifies source/config/code identity. Quality gates run before atomic month
publication; a failure preserves the previous completed month.

**Evidence:** Tests kill a real child process after durable loading and resume the
same run. A separate administrative drill SIGKILLs PostgreSQL, restarts it, and
verifies that logged shard markers and the prior publication survived. See
[database crash recovery](../reports/database-crash-recovery.json). The
[backup/restore report](../reports/backup-restore.json) covers a separate disposable
restore, SQL row reconciliation, constraints, reader access and artifact extraction.

**Trade-off:** Sequential Zstandard preparation may rescan input after interruption.
Downstream work is reused; the system does not claim random access into a compressed
file. Durable Parquet and logged SQL staging consume additional disk and WAL. The
same-instance restore drill does not establish off-site disaster recovery or HA.

**Coverage improvement:** The January source has 218 games with missing player
identities. The optimized default retains otherwise valid games with an explicit
unknown member and reports their rate; a configurable coverage gate protects
publication. Missing identity is treated separately from illegal PGN or invalid
results/ratings. Counts therefore differ from the older missing-player rejection policy.

## Evidence-based engine selection

**Problem:** The earlier comparison measured serial Python against parallel Python
parsing inside Spark. That was useful locally but did not establish Spark-specific
benefits or target-scale capacity.

**Change:** The optimized benchmark includes serial Python, bounded multiprocessing
Python, and Spark with native expressions after shared move parsing. All engines
read the same framed Parquet, write typed observations, and use the same SQL model.
Fresh run groups record immutable source/code/image/config metadata, actual cgroup
limits, client resource consumption, file/model sizes, phases and SQL plans.

**Evidence:** [Current comparison](../reports/comparison-v2.md) and
[raw samples and plans](../reports/comparison-v2.json). Comparison refuses mixed
versions/configuration/limits and validates semantic facts, active dimensions and
rejects. Remote worker correctness is covered by the [two-worker synthetic check](../reports/cluster-smoke.json).

**Trade-off:** Shared preparation is outside optimized engine timings. Spark startup
is included; PostgreSQL CPU/memory is outside client cgroup figures. Remote worker
resources are outside a driver container's measurements. Surrogate numbers can
legitimately differ; equality is checked through natural keys and business fields.
The earlier total times included different preparation and quality rules, so a
percentage speedup against that baseline would not be a controlled comparison.

## Honest positioning

Describe the project as a containerized historical batch pipeline with demonstrated
production-oriented controls and measured local behavior. Keep commercial production
experience separate from this independent project. Do not claim that a small archive,
synthetic multi-month tests, or two Docker workers validate 30 GB/month, five-year
capacity, an uptime SLA, or a multi-machine deployment.
