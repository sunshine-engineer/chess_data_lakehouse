# Optimized historical pipeline comparison

Benchmark group: `benchmark-20261104T123830-12788`.

| Median metric | python | python_mp | spark |
|---|---:|---:|---:|
| Fresh samples | 3 | 3 | 3 |
| Processing seconds | 153.83 | 123.734 | 124.586 |
| Input games/second | 788.741 | 980.586 | 973.885 |
| Client CPU seconds | 145.135 | 171.719 | 227.803 |
| Container peak memory MiB | 207.293 | 240.438 | 851.633 |
| Historical PostgreSQL bytes | 97910784 | 97910784 | 97910784 |
| Typed Parquet bytes | 8891659 | 8891659 | 4758602 |

All active fact, dimension and reject rows match semantically across engines.
Warehouse surrogate numbers and run timestamps are intentionally excluded; natural keys and game-time attributes are compared.
Sources and complete archive histories match. Samples with changed code, images, settings or limits are rejected.
Download and shared framing are separately reported preparation work, excluded from these engine timings.
Spark startup/shutdown is included. PostgreSQL CPU/memory is not included in client cgroup metrics.
These are local measurements; no claim of validated 30 GB/month or multi-year production throughput is made.
