# Docker execution evidence — 2026-11-04

## Scope and result

Python and Spark both processed the complete January 2013 Lichess standard-rated
archive inside Docker and loaded typed PostgreSQL star models. Three independent
containers ran per engine, sequentially in alternating order:
`Python, Spark, Spark, Python, Python, Spark`. All-month processing stayed disabled.

Each of the six runs read **121,332 games**, accepted **121,114**, rejected **218**,
and found **0 duplicates**. Rejections were missing-player records. The source
checksum is pinned in `config/pipeline.toml` and recorded in every report.

## Measured comparison

| Median over 3 runs | Python | Spark |
|---|---:|---:|
| Processing seconds | 159.968 | 124.350 |
| Input games/second | 758.48 | 975.73 |
| Client container CPU seconds | 145.253 | 216.004 |
| Peak container memory, MiB | 152.680 | 981.344 |
| PostgreSQL model bytes, including indexes | 49,520,640 | 49,520,640 |

Spark took about **22% less elapsed time**, while using about **49% more client CPU**
and **6.4 times the peak container memory**. This is a local one-month observation,
not a guarantee of performance on other datasets or distributed clusters. Python
has the smaller resource footprint; Spark's parallelism improves elapsed time here.

The source archive was already present in a Docker volume for each timed run.
Checksum verification, framing, parsing, chunked COPY, SQL modeling, validation and
Spark startup/shutdown are included. Download, image build, container creation and
comparison queries are excluded. PostgreSQL runs separately; its CPU and memory
are not included in client metrics. Memory is the whole-container cgroup peak,
including charged cache, rather than process RSS.

[Generated Markdown report](../reports/comparison.md) and
[raw samples and runtime metadata](../reports/comparison.json) are the primary
measurement artifacts. Both clients were limited to 2 CPU and 3 GiB; Spark used
`local[2]`, two partitions, and a 1 GiB driver heap. PostgreSQL had 2 CPU and 1 GiB.

## Verification actually executed

- Docker image build and Compose configuration validation succeeded.
- **38 tests passed inside the final Docker image** in 10.23 seconds. These include
  real Spark tasks, PostgreSQL COPY/model loading, duplicate handling, chunk
  boundaries, FK enforcement, engine equality, retry-safe COPY, advisory locking,
  failed-model publication recovery, and post-commit report-write failure recovery.
- Tests create and drop a disposable database. The real month and comparison
  ledger remained available after the final test run.
- Exact bidirectional `EXCEPT ALL` comparisons passed for all six latest model
  tables, excluding only run-specific ingestion timestamps.
- PostgreSQL's container was **recreated**, retaining its named volume. Both
  `python_current` and `spark_current` retained all model rows; all five fact
  foreign keys in each private model schema remained validated. See
  [persistence readback](../reports/persistence.json).
- Both example analytics queries and the run-ledger query executed inside Docker.
- The comparison ran again after recreation and isolated tests; model equality
  still passed. Dependency validation used `pip check` inside Docker.
- Benchmark shell syntax and tracked-file whitespace checks passed.

Observed tables per engine:

| Table | Rows |
|---|---:|
| `fact_game` | 121,114 |
| `dim_date` | 32 |
| `dim_player` | 4,898 |
| `dim_opening` | 1,946 |
| `dim_time_control` | 394 |
| `rejected_game` | 218 |

The extra date is December 31, 2012: some games started before the source month.

## Runtime provenance and limits

Timing samples used runner image:

```text
sha256:1d121abdb0f54303ffdf9c89de1f3ab2491efef398cb9a99b2b79a63fc38ef1e
```

Final integration-tested image:

```text
sha256:0bfebc9f04e4386df983a821ea10e9c51739e0401ff7c1afe90fab93808a33f0
```

The final image adds post-commit report-write recovery, progress logs, disposable
integration-test database isolation, and report environment metadata. The parser,
COPY loader, dimensional SQL, chunk/partition settings and resource limits used
for the six timing samples are unchanged. Tests and final output equality checks
were executed against that final image. No performance samples were fabricated
for the recovery paths.

Spark 3.5.7 and PostgreSQL 16.15 executed successfully. The generated JSON report
records Python, package, kernel and architecture versions. No cloud services,
BigQuery, multi-node Spark cluster, all-month execution, concurrent throughput,
or multi-machine scaling were validated. PostgreSQL remains running; stopping it
with `docker compose down` preserves both named data volumes.
