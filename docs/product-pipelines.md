# Case study: choosing a processing path with evidence

## Outline

1. Compare the same workload.
2. Check data equivalence before comparing speed.
3. Examine time, memory, CPU and storage together.
4. Keep extrapolation separate from measurement.

## The practical problem

Choosing Spark simply because an archive is large is not a performance result.
Startup costs, parsing, client memory and database publication all affect a small
local workload. A useful comparison needs the same source, the same retained data
models and repeated measurements.

## The working approach

The repository has Python, parallel Python and Spark paths. Typed Parquet
checkpoints separate parsing from PostgreSQL loading. Bounded workers and COPY
batches limit concurrent work; publication and recovery checks protect the active
dataset. Source checksums and monthly lineage make a run inspectable.

The application keeps the original January report separate from the expanded
three-month workload. Select a workload to inspect its source history and repeat
count. Semantic checks compare dimensions, facts and rejects.
The plots show median wall time and client-container peak memory; the full table
also includes throughput, CPU time and Parquet size. Individual run records and
the complete evidence download remain available.

## Original January measurement

The recorded group `benchmark-20261104T123830-12788` processed 121,332 January
2013 games per run. Median wall
times were approximately 153.8 seconds for Python, 123.7 for parallel Python and
124.6 for Spark. Median client peak memory was approximately 207.3, 240.4 and
851.6 MiB respectively. All twelve recorded semantic comparisons passed.
The [measured report](../reports/comparison-v2.json) is the source of those figures.

This result makes the trade-off concrete: parallel Python reduced elapsed time on
this workload while using more CPU and memory than the single-process path. Spark
had a similar elapsed time and a larger measured client footprint.

## Expanded January–March workload

The opt-in expansion processed **403,928 games per complete workload repeat** in
an isolated Docker/PostgreSQL project. Two complete repeats per path used the same
pinned image, sources and resource limits; all twelve semantic checks passed.

| Processing path | Median workload time | Median peak client memory |
|---|---:|---:|
| Serial Python | 1,740.5 s (29.0 min) | 252.4 MiB |
| Parallel Python | 1,077.6 s (18.0 min) | 276.7 MiB |
| Spark | 1,026.0 s (17.1 min) | 1,111.5 MiB |

Times sum the three monthly batches within each repeat; memory is the largest
monthly client peak, then the median across repeats. Each monthly batch includes
startup. The [expanded measured report](../reports/scale/comparison-v2.json)
includes every monthly sample and the query plans.

Spark recorded the lowest median wall time in this experiment. Parallel Python
used about one quarter of Spark's client peak memory and more memory than serial
Python. Host contention/cache state were uncontrolled, other local workloads were
active, and one interrupted Spark job was replaced with a fresh run. Two repeats
and a small time gap do not establish a stable winner or isolate a scaling effect.
The original and expanded measurements are separate observations, not a controlled
cross-size scaling experiment.

## What the measurement excludes

Shared download and source framing are excluded. Spark startup is included.
Client metrics exclude PostgreSQL and remote workers. These local runs do not
establish five-year capacity or throughput at 30 GB compressed archives per month.
Real all-month ingestion remains disabled.

See [pipeline operations and models](optimized-pipeline.md) and
[executed recovery and comparison evidence](optimized-validation.md) for the
commands, failure drills, resource budgets and validation boundary.
