# Personal Study workspace

## Outline

1. Sign in and choose a player library.
2. Import a PGN or download games from a Lichess API link.
3. Follow findings into a game, position and practice note.
4. Ask questions, save a study and export reproducible data.
5. Operate and test the Docker services.

## Start

```bash
docker compose --profile web --profile study build web
docker compose --profile web --profile study up -d --wait web study-api
```

Open `http://localhost:8080/#personal`. Create a **Chess Atlas** account, or sign in
to an existing one. This is a local app account, not a Lichess account. Your Lichess
password is never requested. Each account's libraries, imports, notes, questions
and saved studies are separate. A coach can study several players in one account.

The initial owner's migrated library uses private credentials in
`data/owner-access.json`, generated once during the upgrade. Open that ignored
local file, sign in, then change the app password in **Account**. No default or
shared password is committed. New users register through the interface.

## Import games

Choose **Import games**, then one source:

- **Upload a PGN**: choose a file. Leave **Player to study** empty to detect the
  common player, or enter a name if the file is ambiguous. Different dates,
  months and years can belong to the same library.
- **Import from Lichess**: paste a link such as
  `https://lichess.org/api/games/user/Sunny2g?tags=true&clocks=true&evals=true&opening=true&literate=true`.
  The player is read from the URL. The app downloads and processes the games;
  no manual download step or API token is needed for public games.

There is **no approved-month input or confirmation checkbox**. Dates come from
the games and are used only to filter analysis. Missing exact dates remain
unknown; the app does not invent a day or reject a file because of a month setting.

The Lichess preview explains the scope. With no `max` option it requests all
available completed public games. A maximum, date range, color, opponent or clock
filter already in the link is respected. Required tags/moves are always enabled.
Only the official HTTPS user-games endpoint is accepted; arbitrary URLs, credentials
in URLs, nonstandard ports and redirects are refused. No app session or password
is sent to Lichess. Downloads use one request at a time and wait a full minute
before retrying HTTP 429, following [Lichess API guidance](https://lichess.org/page/api-tips).
The [official export specification](https://github.com/lichess-org/api/blob/master/doc/specs/tags/games/api-games-user-username.yaml)
recommends streaming and documents that omitting `max` downloads all games.

**Import behavior** has two choices:

| Choice | What happens |
|---|---|
| Add games and update changed games | Keep the current collection, add new game IDs, reuse identical games, and retain a new revision only when information changed. Use this for normal updates. |
| Use only the games in this import | Make the imported set the active collection. Other games leave the active view; earlier versions and saved studies remain available. Shared payloads are reused. |

The import runs as a background job. You can watch bytes received and games
processed, cancel it, or return from **Recent imports**. When it completes, the
app opens a separate analysis workspace. A malformed mainline or conflicting
copies of one game stops publication and leaves the current collection unchanged.
Unsupported variants, empty/aborted or unfinished games, and games unrelated to the selected player
are excluded with counts and bounded explanations, rather than breaking an otherwise
valid collection. The engine/replay workspace currently supports standard chess.

Game IDs come from source URLs or `GameId`. Generic PGNs receive a stable
`local_<SHA-256>` identity. Identical source/local games are not stored repeatedly.
If source identity is missing, identical player/date/time/move sequences cannot be
distinguished; include source IDs when available. Missing optional ratings,
opening labels and clocks stay explicitly unknown. Uploaded annotations remain
labelled as unverified source annotations.

## Find a useful study focus

Your player workspace has separate sections:

- **Your focus**: three summary values and at most three findings. Each finding
  explains the evidence, a next action and a few example games.
- **Openings**: at most five common openings with sample size, score and an action
  to open their games. A low raw score alone is not labelled a proven weakness.
- **Games**: ten rows per page with opponent, date, clock, color, result and opening.
  Every game opens its own stable review URL and displays its unique ID.
- **Ask your study**: question prompts, computed answers and supporting game links.
- **Saved studies**: pin the exact collection version and filters, then export it.

Use **Period**, color, clock and opponent-strength filters. **Last 7/30/90 days**
uses current UTC calendar dates; **Choose dates** supports an explicit range.
Empty results mean no games match, not that a player's performance is zero.
Question dates such as “last 30 days” and “last month” become visible analysis
filters. “Last month” means the previous UTC calendar month.

Score is `(wins + 0.5 × draws) / games`. Opening comparisons match the player's
other openings by color, clock and opponent-strength group (±200 rating).
A review candidate requires at least five opening games, three reference games
per matched group, at least 50% matched coverage and a score gap of at least
15 percentage points. Comparisons are descriptive and do not prove causes.
Wilson intervals describe observed binary win rates, not expected improvement.

## Review a game

Open a finding's example or a row in **Games**. Replay legal moves, flip the board,
inspect imported clocks/evaluations, or find recurring positions in the current
selection. Position counts use the first occurrence per game; observed move
frequency does not establish move quality. Copy the game link to return to the
same collection and position. Other accounts cannot open private links.

**Review this game with Stockfish** queues a bounded, explicit analysis. One engine
worker uses one thread, 32 MiB hash, up to 20,000 nodes/position, 0.25 seconds per
position and a two-minute game budget. Long games may have partial coverage.
The library has no lifetime three-game analysis cap; queued work is bounded instead.

A move-loss candidate compares the player's move with a preferred alternative.
Checkmate-in-one claims are verified on the legal board. Other captures/checks
are described cautiously. Replay the preferred and opponent-reply lines; engine
variation boards are explicitly labelled. Return to the played line before saving
its practice note or asking about that played position. Mark notes **To review**,
**Reviewed** or **Practiced**. The app does not claim a rating gain from these actions.

The assistant uses approved computational operations; it cannot write SQL or
invent tactical claims. Examples: “Summarize my games”, “Compare White versus Black”,
“Where do I underperform in blitz games?”, “What should I study next?” and
“What is the best move here?” on a reviewed position. Questions requesting future
ratings, secrets, guarantees or unsupported thresholds are refused. A suggested
practice plan uses the actual report findings and linked games. It is not an
open-ended AI chess coach or a substitute for checking the board.

## Reproducible studies and exports

Save a study to pin its snapshot and resolved filters. Downloads include:

| Format | Contents |
|---|---|
| Parquet | Typed game facts, ratings, clock values, targets and revision checksums; embedded manifest and data dictionary |
| PGN | Replayable canonical games with stable source/local IDs |
| Manifest | Player, version, dataset checksum, filters, row count and feature-availability dictionary |
| Report | The selected report and evidence-linked findings |

Every download has `X-Content-SHA256`. Export generation uses a server-side cursor
and Parquet row batches, not a whole-library Python list. Keep the bytes/hash,
version, filters and code revision for an experiment. Exclude result, opening
labels, moves and length from predictors intended to run before a game.
Game IDs can identify public games; exports are not anonymous. Notes and detailed
real-game results remain private and are not committed to Git.

## Data and processing model

All execution remains in Docker; PostgreSQL stores the account-owned models.
Migrations `004_workspaces.sql` and `005_workspace_positions.sql` are additive. Applied migration checksums are checked,
and the prior single-player data remains intact for recovery.

| Model | Role |
|---|---|
| `accounts`, `sessions`, `auth_attempts` | Scrypt password hashes, hashed session tokens, CSRF tokens, sign-in throttling |
| `libraries`, `library_snapshots` | Private player libraries, active collection and immutable version provenance |
| `game_revisions` | One immutable semantic payload per account/game/checksum, shared across studied players |
| `snapshot_games` | Small references from versions to shared revisions; composite ownership keys prevent cross-account membership |
| `workspace_game_facts` | SQL view deriving player perspective, ratings, score and opponent-strength groups |
| `workspace_positions` | Indexed positions stored once per shared revision, used for recurring-position lookup |
| `import_jobs`, `import_members` | Persistent job progress and chunked staging; active publication is atomic |
| `workspace_analysis`, `workspace_reviews` | Version-bound engine evidence and practice notes |
| `saved_studies`, `legacy_mappings` | Pinned research selections and non-destructive migration lineage |

The API checks ownership on every library, snapshot, job, game, note, analysis and
export. App sessions use HttpOnly/SameSite cookies with CSRF-protected writes;
password changes revoke other sessions. The archive web process keeps its reader
role and proxies allowlisted personal operations to the private study service.
Neither PostgreSQL nor the private API is exposed on a host port. The web app
remains bound to loopback. Public hosting, email recovery, Lichess OAuth/ownership
verification and broader operational hardening are separate deployment work.

Uploads/downloads spool to the study disk volume. Processing is bounded per record
and chunk; duplicate source files are deleted after completion, and failed staging
is cleaned. Unchanged reimports create no additional game payload or snapshot.
Changed information creates a distinct revision, while saved versions reuse its
references. Legacy archives remain preserved separately during this upgrade.
Position lookup uses SQL aggregates rather than replaying the entire library in
Python. A recovery worker indexes older revisions one game at a time; partial
counts are labelled until that work finishes. Exports use disk files and chunked
reads, and remove their temporary files after the download closes.

Default quotas are disabled: `STUDY_MAX_LIBRARY_GAMES=0` and
`STUDY_MAX_STORAGE_BYTES=0`. They can be configured later. Zero does not mean
infinite physical disk: a free-space reserve stops an import cleanly before the
volume fills. A per-game record limit, per-task engine budgets and small HTTP/SQL
pages bound concurrent work without imposing a library game-count limit.

## Operations and tests

```bash
# Test both the legacy parser/data protections and new account workspace:
docker compose --profile study run --rm study-tests
# Archive web and ML regression:
docker compose --profile web run --rm web-tests
# Explicit offline browser transport, in a fresh disposable project:
WEB_PORT=18082 docker compose -p chess-workspace-qa -f compose.yaml -f compose.workspace-qa.yaml --profile web --profile study run --rm personal-browser-tests
# Verify SQL and both artifact volumes in a disposable restore destination:
docker compose run --rm backup-restore
```

`compose.workspace-qa.yaml` serves only labelled synthetic DemoStudent PGNs for
API browser checks. Never use that override for real imports. Production Compose
uses the real HTTPS transport. CI makes no real Lichess requests. Live retrieval,
owner migration and real-data browser checks are separate acceptance evidence.

To migrate the initial owner's existing study after applying migrations, run the
explicit one-shot command with an ignored credential-file mount:

```bash
docker compose --profile study run --rm -v "$PWD/data:/private" --entrypoint python study-api -m chess_lakehouse.personal.workspace.migrate_legacy --username sunny2g --credential-file /private/owner-access.json
```

Use the intended local owner name when migrating another installation. This
command is idempotent and preserves old collection versions,
cohorts, notes and completed engine evidence. It never assigns legacy data to an
arbitrary newly registered account. Do not delete named volumes to repair a login.

Restarted running imports are marked interrupted and staged payloads cleaned;
retry a Lichess link or choose the original PGN again. Queued jobs persist.
Restarted engine jobs require an explicit retry. Backup bundles contain private
account/game data and must be kept private. Model weights remain in the separate
`ml-data` volume. The historical warehouse and bulk all-month archive scheduling
are unchanged; January archive capacity measurements do not establish multi-year
or 30 GB/month production capacity.
