# Personal study assistant

## Outline

1. Start the local Docker services and manually import approved games.
2. Compare results, inspect positions and record a review plan.
3. Ask bounded analytical questions and export reproducible cohorts.
4. Understand data models, resource limits and recovery.

## Start and import

Keep your existing `.env` and database credentials. Add separate strong values
for `STUDY_PASSWORD` and `STUDY_API_TOKEN` from `.env.example`; never commit `.env`.

```bash
docker compose --profile web --profile study build web
docker compose --profile web --profile study up -d --wait web study-api
```

Open `http://localhost:8080/#personal`. The approved configuration is
`config/study.toml`: **sunny2g, October 2026 UTC**. Use the displayed manual
Lichess export link, save the PGN, select it, confirm the player/month and import.
The app never fetches this file itself. A 500-game export is a bounded sample if
the player played more games that month. No authentication token is required for
public games; private-game access is outside this release.

Only completed standard games with that player, a Lichess game ID and an actual
start date inside the approved month are accepted. Illegal mainlines, inconsistent
results, conflicting duplicates within a file and oversized files are rejected.
Absent ratings and opening labels remain unknown. Header labels and uploaded
evaluations are not independently verified. Variations/comments are excluded from
the safe replay; the original upload is retained privately.

Add/correct mode merges by game ID. Identical uploads reuse the existing version;
changed games publish a new complete immutable version. Replace mode changes the
active set and preserves older versions. A failed upload leaves the active version
unchanged. Version selection lets you reproduce earlier reports and exports.

Changing the approved player/month requires an explicit configuration change and
service recreation. Each workspace remains scoped by player and month; changing
configuration does not erase earlier private data. This is a single local owner
app, with no user accounts or multi-user authorization.

## Review loop and interpretation

Filter by player color, clock class, opponent-strength band and opening. Score is
`(wins + 0.5 × draws) / games`; win rate is `wins / games`. Wilson 95% intervals
describe observed binary win rates, not score intervals or future-win guarantees.
Opening reference results use the player's other openings with the same color,
clock class and opponent-strength band (±200 rating for the similar band).
At least five opening games, three reference games per matched stratum and 50%
matched coverage are required before flagging a score gap of at least 15 points.
These are transparent review heuristics, not causal or significance tests.

Select a linked game. Replay legal positions, flip the board and look up recurring
positions within the current filters. Position identity uses board placement,
side to move, castling and legal en-passant rights, excluding move counters. The
first occurrence per game counts once. Observed alternatives show frequency and
results; they do not establish optimal moves. Click a continuation to replay a
matching example. Source clocks and uploaded evaluations are labelled separately.

Request an engine review explicitly for up to three distinct games per version.
One Stockfish 15.1 worker uses one thread, 32 MiB hash, up to 20,000 nodes and
0.25 seconds per position, and a 120-second game budget. Check coverage: longer
games may be analyzed only partly. A preferred alternative plus an evaluation
drop of at least 150 centipawns produces a review candidate. Mate transitions are
labelled separately. A checkmate-in-one explanation is verified on the legal
board; captures/checks are described without claiming a proven material blunder.
Compare replayable engine lines, then save your own note as queued, reviewed or
practiced. Engine variation boards are explicitly labelled and must be exited
before saving a note or asking about the played position.

## Analytics assistant

Supported operations are overview, opening comparisons, clock/color/opponent
groups and review evidence. Examples: “Summarize my games”, “Where do I underperform
in blitz games?”, “Compare White versus Black”, “Which games should I review?”
and “What is the best move here?” on an engine-analyzed replay position.
Answers include applied filters, immutable dataset version and linked games.
Unsupported claims, arbitrary numeric/date thresholds, ambiguous mixed filters,
commands, secret requests and future-rating predictions are refused.

Default routing is deterministic. Optional local model routing:

```bash
docker compose --profile study-ai up -d --wait study-llm
docker compose --profile study-ai exec study-llm ollama pull qwen2.5:0.5b
STUDY_ROUTER=ollama docker compose --profile web --profile study up -d --wait web study-api
```

PowerShell: set `$env:STUDY_ROUTER='ollama'` before the final command. Model weights
are a separate download and remain in a named volume. The model receives only
the question and an operation/filter schema; no raw PGN, comments or notes are
sent. It cannot write SQL, compute final numbers or generate tactical claims.
Schema checks and template/filter guards constrain proposals, and an eight-second
timeout falls back to templates. Structured responses follow an allowlisted JSON
schema ([Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs));
model-proposed filters must agree with explicitly recognized question filters.
The UI exposes the actual route/fallback used.
CPU is capped at two cores and model memory at 2 GiB. Model routing is optional
and limited; this is not open-ended chess coaching or a verified strength gain.

## Research and later-data evaluation

Save a named cohort to pin the exact version and filters. Downloads include typed
Zstandard Parquet with embedded manifest/dictionary, replay PGN, report JSON with
review evidence and a standalone manifest. Every download has an
`X-Content-SHA256` header. Current-version links export the full version; saved
cohort links apply the saved filters. Identifiers can link back to public games,
so exports are not anonymous. Notes/report exports may contain private content.

The dictionary distinguishes pre-game inputs from post-game targets/descriptors.
Keep results, moves, opening labels and length out of pre-game predictive inputs.
For reproducibility retain the downloaded bytes and hash, version/dataset hash,
filters and code/model run ID; mutable review notes can change later report JSON.

After at least 20 real approved games, use “Evaluate frozen models”. It compares
the frozen January 2013 full model, ratings-only model and prior on strictly later
dates, with log loss, classwise reliability and multiclass Brier score. It also
compares length estimates with the frozen median baseline. Nothing is fitted or
tuned on personal games. One player's month is a transfer check, not modern
population validation. Synthetic tests prove the evaluation path only.

## Models and service boundaries

| Table | Purpose and key |
|---|---|
| `study.workspaces` | Approved normalized player/month and active version |
| `study.imports` | Immutable version, parent, source/dataset hashes, manifest and private artifact path |
| `study.games` | `(import_id, game_id)` typed player-perspective facts and Parquet row index |
| `study.positions` | `(import_id, game_id, ply)` normalized position hash and observed next move |
| `study.cohorts` | Version-pinned name and validated filters |
| `study.review_items` | Version/game/ply note, progress and timestamps |
| `study.analysis_jobs` | Bounded queued/running/complete/failed jobs and engine/code/config provenance |
| `study.validations` | Frozen model run and later-version evaluation, unique per version/model |

Each import writes original PGN, typed per-game Parquet and checksummed manifest
under `/study/<import UUID>/`. Streaming `COPY` loads game and position rows;
publication uses a transaction and advisory locks. Historical rows cannot be
updated/deleted through SQL triggers. This small personal importer is memory
bounded by its hard limits; it is separate from the chunked bulk archive pipeline.

`web` uses archive `chess_reader`; it has no study SQL access. Its allowlisted
same-origin proxy authenticates to private `study-api` using a bearer secret.
`study-api` uses `chess_study`, with access only to the study schema. Admin setup
runs in one-shot migration containers. Only loopback web port 8080 is published;
the API, database and optional LLM have no host ports. Application services run
non-root, with read-only root filesystems and bounded CPU/memory. This local
boundary is not an internet-facing authentication or tenancy design.

Hard limits: 10 MiB/upload, 500 games/version, 600 plies/game, 100,000 plies/version,
20 versions/workspace, 50 saved cohorts/workspace and three analyzed games/version.
No automatic retention/deletion or all-month personal streaming exists. Raw games,
model weights, credentials and backups stay outside Git.

## Tests and recovery

```bash
docker compose --profile study run --rm study-tests
docker compose --profile web run --rm web-tests
# Fresh disposable project only: the browser script refuses real-player seeding.
WEB_PORT=18081 STUDY_CONFIG=config/study-demo.toml docker compose -p chess-study-demo --profile web --profile study up -d --wait web study-api
WEB_PORT=18081 STUDY_CONFIG=config/study-demo.toml docker compose -p chess-study-demo --profile web --profile study run --rm personal-browser-tests
docker compose run --rm backup-restore
```

The restore drill holds publication locks, captures a matching SQL snapshot and
both artifact volumes, restores into a disposable database/directory, compares
all row fingerprints and files, checks constraints/reader permissions and rejects
a truncated dump. Bundles live in `backup-data` and contain private games. Model
weights are separate: retrain the historical models if `ml-data` is lost. Do not
delete named volumes to fix a login issue. Keep `.env` with its original passwords.

Restarted engine jobs become failed and require an explicit retry. A publication
with an uncertain commit acknowledgement retains its files; inspect SQL import
IDs and hashes before removing any orphan folder. Do not edit checksum-tracked
applied migrations. Archive January 2013 execution and `all_months_enabled=false`
remain unchanged. Representative 30 GB compressed monthly capacity is unproven.
