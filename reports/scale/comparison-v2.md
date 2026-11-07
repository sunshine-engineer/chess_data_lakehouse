# Optimized historical pipeline comparison

Benchmark group: `scale-20261107T072550`.

Each complete repeat processes January–March 2013 sequentially: **403,928 input
games**. Each path has two complete workload repeats (six fresh monthly runs).
Time, CPU and Parquet bytes sum the months within a repeat; memory is the largest
monthly client-container peak. The table reports medians of those repeat totals.

| Median metric | python | python_mp | spark |
|---|---:|---:|---:|
| Fresh samples | 2 | 2 | 2 |
| Processing seconds | 1740.51 | 1077.578 | 1026.028 |
| Input games/second | 232.157 | 376.009 | 405.861 |
| Client CPU seconds | 1609.911 | 1502.161 | 1792.262 |
| Container peak memory MiB | 252.402 | 276.713 | 1111.504 |
| Historical PostgreSQL bytes | 238022656.0 | 238022656.0 | 238022656.0 |
| Typed Parquet bytes | 29840543.0 | 29840543.0 | 15797874.0 |

All active fact, dimension and reject rows match semantically across engines.
Warehouse surrogate numbers and run timestamps are intentionally excluded; natural keys and game-time attributes are compared.
Sources and complete archive histories match. Samples with changed code, images, settings or limits are rejected.
Download and shared framing are separately reported preparation work, excluded from these engine timings.
Spark startup/shutdown is included. PostgreSQL CPU/memory is not included in client cgroup metrics.
These are local measurements; no claim of validated 30 GB/month or multi-year production throughput is made.

Host contention and OS cache state were uncontrolled, and other local workloads
were active. A Docker/environment restart interrupted one unpublished Spark job;
it was replaced with a fresh run using the same pinned image. Completed samples
were retained. See `environment.json` and individual samples in the JSON report.
The 51.6-second median gap between Spark and parallel Python is an observation,
not evidence of a stable ranking. Parallel Python used about one quarter of
Spark's measured client peak memory, while serial Python had the lowest memory.
