# Board, beginner practice and archive expansion

## Study a position

Open **Personal study**, choose a library and open a game. The board uses original,
local SVG pieces, with no external fonts or assets. **Board appearance** saves theme,
piece style, size, default orientation, coordinates and highlights to your account.
Flip changes the current view. On small screens the board fits the available width.

Select a piece and then its destination to explore a legal continuation. Arrow keys
move focus; Enter selects a square; Escape clears a selection. Promotion offers four
pieces. The server checks every move against the selected immutable game version,
including castling, check and en passant. Trial lines have an 80-half-move limit.
Undo or return to the played game at any time. Name a continuation to save it;
up to 30 variations per game are retained, and each can be removed independently.
Trials never rewrite imported moves. Notes describe the played position.

**Evaluate position** searches the played position or current trial line. Quick,
balanced and deeper searches stop at the earlier node/time limit: 10k/0.3s,
50k/1s and 150k/3s. Each uses one thread and 32 MiB engine hash, plus process
overhead. Results show White-centric evaluation, mate separately, search depth,
nodes and a replayable UCI/SAN line in the API. A bounded estimate is not proof
of the strongest move. Full-game and position jobs share the existing single
engine worker and an account queue limit. Completed position searches are cached
by account, full FEN, move history, engine binary and analysis code/settings.
The engine receives the played/trial history so repetition context is preserved.

## Learn and practice

**Learn & practice** contains seven original lessons across opening development,
middlegame forcing moves/planning, and promotion/basic mating endings. Read the
principle, try the labelled authored drill, then reflect on a real game example.
The server grades only the stated rule-based goal. For example, a legal central
pawn advance can pass the center drill; this does not prove it is best in a game.
Promotion grading checks promotion and stalemate, not whether every underpromotion
wins. Any move satisfying the exact mate goal is accepted.

Guest practice works without an account. Signed-in attempts are private, persist
in PostgreSQL, and use an attempt UUID to make request retries idempotent. Repeated
success is practice completion, not evidence of rating improvement. Progress is
foundation-drill progress; worked-game reflections are not automatically graded.

Personal examples come from the first 30 game IDs in the selected library's active
version. This index-backed, bounded sample is explicit in the UI. It is not a skill
diagnosis or a representative estimate of the player's history. Public examples
come from the bounded teaching materialization below. Both show the actual recorded
move and let the reader open the source game. Recorded moves are never answer keys.

When you select **My game library**, practice focus cards also use full-library
opening counts and an existing completed engine review, when available. Each card
links a relevant lesson and the exact evidence game/version. Opening frequency is
a reason to revisit a familiar position, not a diagnosis. No engine jobs start
automatically and no finding is manufactured when a completed review is unavailable.

The phase heuristic uses the first 20 half-moves as opening, then non-pawn material
of at most 20 points (N/B=3, R=5, Q=9 across both players) as endgame; other positions
are middlegame. This is a teaching navigation aid, not a universal chess definition.
If a sample has no matching phase, the UI says so rather than inventing a position.

Rule reference: [FIDE Laws of Chess](https://handbook.fide.com/chapter/E012023).
Further exercises: [Lichess Practice](https://lichess.org/practice). Lesson prose,
diagrams and SVG geometry in this repository are original; linked lessons are not
copied into the app.

## Import selected archives

The normal `config/pipeline.toml` remains January-only. The opt-in
`config/archive-demo.toml` permits January–March 2013 only, with official checksums
and source counts. Both keep `all_months_enabled = false`. No scheduled stream or
web-triggered ingestion is enabled. The Pipeline benchmarks page can build an
operator command for a validated explicit selection.

```bash
# Add February and March to the main app's parallel-Python publication.
# Existing January facts and private libraries are preserved.
bash scripts/import-archives.sh 2013-02,2013-03 python_mp
# Optional independent publications for the other paths:
bash scripts/import-archives.sh 2013-02,2013-03 python
bash scripts/import-archives.sh 2013-02,2013-03 spark
```

The script builds and executes Docker containers. Every selected month is checked
before network/database work: allowlist, checksum, expected count, quality limits,
record/batch sizes, concurrency and free working disk. Each month publishes
atomically. A multi-month backfill is a sequence of monthly transactions; earlier
months remain published if a later one fails. Rerun only the failed selection.
To allow another month, first add its trusted checksum, expected input count and
download cap to a reviewed configuration; never enable an unbounded all-month loop.

In **Archive overview** and **Game explorer**, choose **All published months** to
query the retained full warehouse. These statistics use all matching facts, not
the teaching sample. Date/rating/opening filters and cursor pagination still apply.
Models continue to describe their stated training month; new archive months do
not silently retrain or broaden model coverage.

Source: [Lichess database](https://database.lichess.org/) and
[official SHA-256 manifest](https://database.lichess.org/standard/sha256sums.txt).
January has 121,332 source games, February 123,961, March 158,635: **403,928 total**.
The public standard archive is CC0. Private user libraries remain separate.

## Publish historical teaching examples

After `docker compose run --rm study-migrate`, run inside the loader image:

```bash
docker compose run --rm --entrypoint python python -m chess_lakehouse.optimized.teaching --month 2013-01
docker compose run --rm --entrypoint python python -m chess_lakehouse.optimized.teaching --month 2013-02
docker compose run --rm --entrypoint python python -m chess_lakehouse.optimized.teaching --month 2013-03
```

Verified source frames are scanned in small batches. The lowest SHA-256 game-ID
sample retains at most 600 PGNs/month by default (hard cap 1,000). PGNs over
128 KiB are excluded; invalid or unsupported sampled games are counted. Only
phase positions and public provenance are saved, with no usernames. Publication
replaces one sample month atomically. `academy.datasets` records source SHA,
input/sample counts and method; `academy.examples` stores compact move examples.
The archive loader writes this schema; public/study roles can only read it.

## Repeat the larger workload

```bash
# Two complete repeats per processing path; opt-in, can take considerable time.
bash scripts/archive-scale.sh 2
# Continue after a confirmed interruption, using the original group/image:
bash scripts/archive-scale.sh 2 scale-YYYYMMDDTHHMMSS chess-lakehouse-scale:YYYYMMDDTHHMMSS
```

This uses the isolated `chess-archive-scale` Compose project, PostgreSQL instance
and volumes. It pins an immutable runner image and runs each month in a fresh
container under the same limits, alternating engine order each repeat. It does
not replace the main app's publications, models or personal data. Reports go to
`reports/scale/`, separate from the original January evidence. Choose the expanded
workload on the benchmark page only after a completed comparison is published.

The script records the pinned tag/digest and target repeats in
`reports/scale/workload-GROUP.json`. Continuation verifies the original image and
retains completed fresh runs. Inspect any `running` job first: a temporary loss of
observation is not proof that it stopped. Only after confirming that its container
has exited may the operator explicitly abandon its unpublished run with the pinned
runner and start a fresh measured replacement. Resumed partial engine jobs are
excluded from fresh benchmark comparisons. A stopped run is not a completed result.

A workload repeat sums monthly processing time, client CPU and Parquet bytes.
Peak memory is the maximum monthly client peak, not a sum or whole-host measure.
Spark startup occurs for each monthly batch. Downloads/shared source framing are
excluded and reported separately. PostgreSQL memory/CPU, shared-host contention,
cache state and other application activity are not controlled capacity measures.
Inspect raw monthly samples and complete source histories before interpreting
the medians. Two repeats show local variability; they do not establish a stable
ranking, 30 GB compressed/month capacity, or 1–5 years of production throughput.

## Storage and recovery

Migration 006 adds account preferences, saved variations, bounded position jobs,
drill attempts and the public teaching schema. Earlier checksum-bound migrations
are unchanged. Migration 007 retains repetition-sensitive search history. Back up before applying them. The existing backup/restore command
now includes the optional `academy` schema and verifies its row fingerprints and
constraints with the rest of the restore:

```bash
docker compose run --rm backup-restore
```

The command restores into a disposable database; it does not replace the live DB.
Synthetic API/browser checks and live archive results are recorded separately in
the [validation handover](board-learning-archives-validation.md). No production-readiness or learning-outcome claim follows
from passing them.

## Implementation map and API boundaries

| Component | Responsibility |
|---|---|
| `web/static/board.js` / `board.css` | Shared SVG board, accessible square interaction, promotion choice and appearance |
| `personal/workspace/board.py` | Owner/version checks, legal replay, preferences, variations and position search |
| `personal/workspace/worker.py` | Existing import/index workers and one shared Stockfish worker |
| `academy.py` | Original lesson catalog, phase examples and exact drill-goal predicates |
| `personal/workspace/learning.py` | Owner-scoped examples, frequency/review evidence and idempotent private attempts |
| `optimized/teaching.py` | Bounded, deterministic public teaching materialization |
| `optimized/comparison.py` | Identical-workload checks, semantic equality and sequential monthly aggregation |
| `config/archive-demo.toml` / `scripts/` | Explicit archive metadata, controlled ingestion and isolated workload execution |

Public routes are `GET /api/learn`, `POST /api/learn/board`,
`POST /api/learn/attempt`, `GET /api/learn/examples` and `GET /api/archive-plan`.
Drill requests select a fixed lesson ID; a client cannot supply an arbitrary FEN or
engine budget. Guest attempts return grading without writing private progress.
The import planner returns a command and never performs ingestion.

Authenticated routes live under `/api/personal/board/` and
`/api/personal/learning/`. POST requests retain the existing action header,
same-origin guard, CSRF token and service authentication. Board operations use
`library_id`, optional `snapshot_id`, `game_id`, integer `ply`, and at most 80 UCI
trial moves. A supplied resource must belong to the signed-in account's selected
version. The server reconstructs the position; it does not trust client FENs.

`board/preferences` supports GET/POST; `board/position`, `board/variations`,
`board/variations/remove` and `board/analysis` use POST. Position jobs are read
through `GET board/analysis/JOB_UUID`. Learning uses `POST learning/examples`,
`GET learning/progress` and `POST learning/attempt`. An attempt carries
`lesson_id`, a UCI `move` and a UUID `attempt_id`; reusing that UUID for a different
challenge/move fails instead of changing the earlier record.

New private tables are `board_preferences` (one row/account), `board_variations`
(owner/revision/played-ply/legal line), `position_analysis` (owner/history/settings
cache and durable job state), and `lesson_attempts` (owner/attempt UUID/challenge
hash/move/result). They reference existing accounts/revisions, rather than copying
libraries. The public `academy` schema is distinct from both private study data
and the full monthly fact tables. No new public SQL-write privilege is introduced.
