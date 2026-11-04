# Executed validation — 2026-11-04

## One-month result

The default command downloaded the January 2013 standard-rated archive from
[Lichess](https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst)
and verified it against the [official SHA-256 list](https://database.lichess.org/standard/sha256sums.txt).
No other source month was downloaded or processed.

| Measure | Observed result |
|---|---:|
| Compressed archive bytes | 17,761,302 |
| Input games | 121,332 |
| Accepted game facts | 121,114 |
| Rejected games | 218 |
| Duplicate games | 0 |
| Chunk size | 5,000 |
| Chunks, including final partial chunk | 25 |
| Processing and export duration, excluding download | 235.31 seconds |
| Date dimension rows | 32 |
| Player dimension rows | 4,898 |
| Opening dimension rows | 1,946 |
| Time-control dimension rows | 394 |

All 218 rejections were `missing player`. Their source records remain recoverable
from bronze, with IDs and reasons in `rejected_game`. The date dimension includes
December 31, 2012 because some games started before the source archive month.

Input SHA-256:

```text
aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635
```

Local output selection: `data/lakehouse/CURRENT.json`.
Measured snapshot: `20261104T033126-ea98c32e`; its `report.json` records these counts.
Generated data is ignored by Git and must be recreated after cloning.

## Checks actually run

- Editable installation with test dependencies; CLI help and `pip check` passed.
- **30 offline tests passed**, including chunk sizes 1/2/20, final partial chunks,
  entirely null rating columns, invalid records and illegal moves, duplicate game
  IDs, missing metadata, input-count mismatch, checksum/cache failures, empty data,
  one-month enforcement, concurrent-writer protection, reruns, and failed publication.
- Read back all six exported Parquet datasets using Hive partition inference;
  every row count matched SQLite and the run report. Dataset scanning in 5,000-row
  batches read the same counts.
- SQLite integrity and all five fact-to-dimension foreign keys passed.
- Reconciliation: `121332 = 121114 + 218 + 0`.
- Executed both example SQL queries successfully.
- A separate full-month run using the pre-existing local archive produced identical
  model rows in every table, excluding run-specific ingestion timestamps. This
  checks equivalence of the two archive paths; fixture tests separately verify
  reruns within the same output root do not duplicate the current dataset.
- Python compilation and tracked-file whitespace checks passed.

Test runtime: Python 3.14, chess 1.11.2 (python-chess 1.999), Zstandard 0.25.0,
PyArrow 26.0.1, pytest 9.2.0. python-chess emits one upstream asyncio deprecation
warning on Python 3.14; tests still pass. Other Python versions were not executed.

## Limits of this evidence

This validates local SQL/Parquet loading for one month. It does not establish
cloud deployment, BigQuery loading, Spark execution, multi-month scaling, peak
memory usage, disaster recovery, or performance under production load. Two local
validation runs overlapped, so the recorded duration is not an isolated benchmark.
All-month processing remains disabled in configuration and rejected by the CLI.
