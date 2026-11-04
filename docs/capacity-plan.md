# Capacity validation boundary

## Outline

1. Size the retention requirement.
2. Measure actual source shape, disk and query workload.
3. Validate progressively larger representative inputs before a throughput claim.

30 GB of compressed input per month implies 360 GB/year and 1.8 TB over five years,
excluding curated data, indexes, working space, WAL and backups. Compression ratios
and games per byte vary with move lengths and annotations. Do not multiply the
January 2013 benchmark runtime or PostgreSQL/source size ratio to forecast a modern
month or a multi-year backfill.

The implementation adds independent file/batch/task/writer limits, configurable
Spark deployment, partitioned historical refresh, durable checkpoints and retention.
Those make larger tests possible; they are not evidence that a particular machine
meets a 30 GB/month processing window.

For an explicitly approved larger archive, record:

| Gate | Evidence to collect |
|---|---|
| Source contract | Archive checksum, compressed bytes, actual game count, expansion and annotation distribution |
| Processing window | Cold and warm runs, source preparation, parse/load/model phases, backfill completion window |
| Resource limits | Client and database CPU/memory, executor resources, disk high-water mark, network and WAL/sort pressure |
| Data correctness | Cross-month deduplication, count reconciliation, unknown/reject coverage, late corrections, stable downstream history |
| Failure recovery | Interrupted download/parse/COPY, process/server crash, replayed shards, unchanged published data |
| Query service | Representative queries with agreed latency, concurrency and partition-pruning evidence |
| Retention and backup | Raw/curated/hot-warehouse tiers, retirement policy, restore time and independent backup location |

Start with representative slices, then a full approved month, and finally a bounded
multi-month backfill. Record an explicit processing-time and query-latency target
before accepting capacity. Keep actual test execution allowlisted; the repository
continues to default to January 2013 and does not discover or stream every month.
