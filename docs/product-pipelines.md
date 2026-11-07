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

The application compares the retained benchmark report. Each path has three
recorded samples, and semantic checks compare dimensions, facts and rejects.
The plots show median wall time and client-container peak memory; the full table
also includes throughput, CPU time and Parquet size. Individual run records and
the complete evidence download remain available.

## What was measured

The recorded group `benchmark-20261104T123830-12788` processed 121,332 January
2013 games per run. Median wall
times were approximately 153.8 seconds for Python, 123.7 for parallel Python and
124.6 for Spark. Median client peak memory was approximately 207.3, 240.4 and
851.6 MiB respectively. All twelve recorded semantic comparisons passed.
The [measured report](../reports/comparison-v2.json) is the source of those figures.

This result makes the trade-off concrete: parallel Python reduced elapsed time on
this workload while using more CPU and memory than the single-process path. Spark
had a similar elapsed time and a larger measured client footprint.

## What the measurement excludes

Shared download and source framing are excluded. Spark startup is included.
Client metrics exclude PostgreSQL and remote workers. These local runs do not
establish five-year capacity or throughput at 30 GB compressed archives per month.
Real all-month ingestion remains disabled.

See [pipeline operations and models](optimized-pipeline.md) and
[executed recovery and comparison evidence](optimized-validation.md) for the
commands, failure drills, resource budgets and validation boundary.
