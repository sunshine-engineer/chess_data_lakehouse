# Original standalone pipeline and field definitions

## Processing outline

1. Enforce the single-month configuration before accessing the network.
2. Download the compressed archive in 1 MiB blocks, up to the configured size cap.
3. Compare SHA-256 with the pinned official value; rename `.part` only on success.
4. Stream Zstandard decompression into the python-chess parser, one game at a time.
5. Validate and transform at most `chunk_size` game records per transaction.
6. Load dimensions, then facts; route invalid records to `rejected_game`.
7. Check counts and foreign keys, export typed Parquet in bounded batches, and
   publish `CURRENT.json` only after the snapshot is complete.

This is batch ingestion. Streaming here means bounded reading/decompression of
one file, not live event processing or automatic iteration over monthly archives.
The Python visitor validates legal mainline moves without retaining the game tree.
Comments and variations are not stored. Memory scales with a chunk of headers and
counts, not with the entire archive; the parser may still buffer one unusually
large PGN line/game. Disk holds the archive, SQLite warehouse, and Parquet output.

## Tables

| Table | Grain and key | Fields and meaning |
|---|---|---|
| `fact_game` | One accepted game; `game_id` PK | Date, two player keys, opening and time-control keys; ratings and derived bands; result/winner; move counts; termination; source and actual game months; UTC ingestion timestamp |
| `dim_date` | One actual UTC game-start date; `date_key` PK | ISO date string; year, month, quarter; weekday Monday=0 |
| `dim_player` | One normalized player identifier; `player_key` PK | SHA-256 of casefolded public username; latest observed title and its date |
| `dim_opening` | One ECO/name combination; `opening_key` PK | SHA-256 of `ECO + '|' + Opening`; ECO, family, variation split on first `: ` |
| `dim_time_control` | One initial/increment pair; `time_control_key` PK | Canonical `seconds+increment`, initial and increment seconds, derived speed; `unknown` key for missing values |
| `rejected_game` | One invalid input occurrence; `record_number` PK | Source month, game ID if valid, failure stage, reason, UTC ingestion timestamp |

`schema.sql` in the Python package defines complete column names and SQLite types.
Parquet uses matching explicit string/int64/float64 types even for empty or all-null
columns. `source_month` is stored in Hive-style partition paths, not duplicated in
Parquet files. All five fact dimension references have enforced foreign keys.

### Field rules

- `source_month`: archive selection; `game_month`: first day of the actual UTC start
  month. Games crossing month boundaries remain associated with their source file.
- Ratings: integer 0–4000; unknown `?`/`-`/missing becomes null. Average and signed
  white-minus-black difference are null if either rating is missing.
- Rating bands: 200-point buckets, such as `1600-1799`.
- `ply_count`: individual mainline moves; `move_count`: ceiling of half the ply
  count (number of full-move turns started). Neither counts comments or variations.
- Speed: estimated duration `initial + 40 * increment`, in seconds; ultrabullet
  under 30, bullet under 180, blitz under 480, rapid under 1500, otherwise classical.
  Unknown time control produces `unknown`, not a guessed speed.
- Accepted results: `1-0`, `0-1`, `1/2-1/2`; winner is white, black, or draw.
- Missing opening information maps to one stable unknown opening; ECO, if present,
  must match A00–E99. An ECO code alone does not identify every opening variation.
- A player's title is taken from the latest observed game date in the archive;
  later input wins date ties. A later missing title remains null. This is a monthly
  observation, not a historical title dimension or global latest profile.
- Usernames stay in bronze only. Deterministic hashes allow joins, but are
  pseudonyms and can be matched by guessing known usernames; they are not anonymization.

## Quality and reruns

Malformed/illegal PGN mainlines, invalid IDs/dates/results/ratings/time controls,
missing players, nonstandard variants, and invalid ECO codes are rejected. The
parser tries to recover at the next game; unframed garbage is not guaranteed to
be counted as a game. Rejects contain diagnostics, not raw usernames/moves.

The first valid occurrence of a game ID wins within a run. Repeated occurrences
increment `duplicate_games` and do not create extra facts or dimension changes.
Reconciliation is `input = accepted + rejected + duplicates`, and accepted equals
fact rows. The configured expected input count also guards against an incomplete
monthly parse. Nonempty accepted output, SQLite integrity, and foreign keys are checked
before publication. Rejects are reported, not silently discarded. There is no
configured reject-rate threshold yet; inspect the report before analytics use.

Each rerun builds a fresh isolated warehouse. Successful publication atomically
replaces `CURRENT.json`, leaving earlier snapshots intact. This provides an
idempotent *current dataset*, not an incremental upsert across source months.
After inspecting a new snapshot, old `runs/<id>` directories can be deleted manually.
Never delete the run referenced by `CURRENT.json`.

## Failure recovery

- HTTP/transient failure: three complete download attempts with exponential backoff;
  `.part` is removed on failure. No byte-range resume is attempted.
- Existing cached archive with wrong checksum: stop. Remove or quarantine that file
  manually, then rerun. Never change the checksum just to make a failed file pass.
- Invalid record: save rejection and continue; inspect reasons in SQLite/Parquet.
- Decompression, database, export, or interruption failure: no new snapshot pointer
  is published; the unfinished run is removed on ordinary exceptions/Ctrl+C.
- SIGKILL/power loss can leave an unfinished run and lock. Confirm no process is
  active before removing `.pipeline.lock` and unreferenced incomplete run directories.
- One exclusive lock per output root prevents concurrent CLI writers. Run from the
  project root so the default configuration path resolves. Direct Python helper
  functions are internal and do not apply the CLI lock/config/checksum guard.

## Migration and boundary

Copy the selected snapshot's six `parquet/<table>` directories to another disk or
object store. Recreate SQL tables from the documented fields, load dimensions before
facts, and compare per-table row counts, unique game IDs, nullability, and foreign
keys with `report.json`. Preserve Hive `source_month` partitions; readers that do
not infer them must supply that field explicitly. A SHA-256 for the input archive
is provenance, not a checksum for exported files.

SQLite is a zero-service local development warehouse. Parquet is the portable
columnar layer. This version deliberately does not provision GCP, run Spark, load
BigQuery, create cloud infrastructure, schedule months, or build a dashboard.
Those remain future milestones in `PROJECT_PLAN.md`. Move-level facts, puzzle and
evaluation datasets, and incremental cross-month player history are also deferred.

References: [source and PGN format](https://database.lichess.org/),
[official archive checksums](https://database.lichess.org/standard/sha256sums.txt).
