# Python and Spark — measured Docker comparison

Source month: **2013-01**.

| Metric (median) | Python | Spark |
|---|---:|---:|
| Completed samples | 3 | 3 |
| Total processing seconds | 159.968 | 124.35 |
| Input games/second | 758.48 | 975.73 |
| Container CPU seconds | 145.253 | 216.004 |
| Container peak memory MiB | 152.68 | 981.344 |
| PostgreSQL model bytes | 49520640 | 49520640 |

Observed Spark/Python elapsed-time ratio: **0.78x** (lower favors Spark).

All six latest model tables match, excluding ingestion timestamps.

Both containers have 2 CPU and 3 GiB limits. PostgreSQL is shared and runs separately.
Timing includes checksum verification, per-engine framing, Spark startup/shutdown where applicable,
parsing, staging, SQL modeling, and validation. Download, image build and comparison are excluded.
Memory is the whole-container cgroup peak (JVM/workers included). CPU is the container cgroup usage delta.
PostgreSQL CPU/memory are not attributed to either client; model bytes include indexes but exclude staging.
This is a local single-machine test on one small month, not a distributed scaling benchmark.
Cache order and background machine load can affect results; inspect raw per-run phase timings.
