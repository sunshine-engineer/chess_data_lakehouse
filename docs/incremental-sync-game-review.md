# New-game sync and full-game review

## Use it

Open **My libraries → Sync new games** to check a player's public Lichess games.
Uploads and Lichess links default to **Add new games only**. Existing games,
notes and saved collections stay unchanged. A full-history link has no date or
`max` filter. The first connection inventories game IDs, so a large existing
library can still take several minutes to connect; it does not download its old
moves. Following syncs request a recent window.

Choose **Refresh games, including changed information** when you deliberately
want corrected ratings, annotations or other source changes. Choose replacement
only to make the supplied games the active collection. Earlier saved versions
stay available. Replacement resets the sync checkpoint so a later full sync can
discover games removed from the active view.

Open a game and select **Analyse complete game**. The background worker checks
every played position and compares each of your decisions with engine candidates.
It allows up to five minutes per game. The page shows live counts and reports
partial coverage if the budget expires. It lists every detected score loss of at
least 1.50 pawn units, ordered by size; no three-finding or twenty-finding cap is
applied. Small inaccuracies below this threshold are not listed. A finite engine
search cannot guarantee that every chess mistake has been found.

Each finding has three distinct alternative first moves where available, ranked
by the engine's estimates. Read the paragraph, then use **Replay line**, **Next**
and **Back** to inspect it on the board. A separate played continuation explains
the opponent's response. These previews leave the game intact. Return to the
played game before writing a practice note.

Scores are from the studied player's side: positive favors you, negative favors
the opponent; 1.00 is approximately a pawn-equivalent advantage. Mate scores
count moves to mate. A better defensive line can still leave a losing position.
Paragraphs describe legal moves, replies, material changes and engine scores;
they do not invent a winning plan or use an external language model.

## Import contract and data model

This is incremental API ingestion, not database change-log capture. A successful
unfiltered add-only sync stores its request cutoff in `library_sync_state`.
Future metadata scans start 24 hours before that cutoff to tolerate delayed
availability and equal timestamps. Existing IDs are checked in bounded SQL
batches. Only missing IDs and previously unfinished IDs receive a PGN request,
in groups of 100. Pending games are checked even when their start time is outside
the new window. Empty/aborted and unsupported games are explained rather than
added to the completed standard-game collection.

`import_game_queue` stages requested IDs per owner/job. `sync_pending_games`
tracks unfinished IDs per private library. `import_jobs` exposes metadata counts,
skipped stored games and cutoff bounds. Snapshot publication, pending IDs and the
checkpoint commit in one transaction. Missing completed PGNs, cancellation,
download errors and failed publication do not advance it. Retries repeat the
last successful window. A no-new-game sync reuses the active snapshot without
copying or rehashing its memberships. All existing ownership checks also apply
to `POST /libraries/<id>/sync`.

Filtered/capped API links keep their original scope and never move the global
checkpoint. File imports cannot use a remote checkpoint, but known Lichess IDs
are skipped after reading their headers, before move parsing. Missing Site
headers still use the existing deterministic local IDs. The app cannot infer
that a local ID and an unrelated remote ID describe the same game. Add-only mode
does not ingest later annotations for an existing ID; use explicit refresh.
Deletion/privacy changes at Lichess do not delete local historical games.
Header identity uses the main parser's rules, including `/game/ID` URLs,
player-suffixed URLs and `GameId` tags. Empty or unfinished PGNs retain that
identity so they can be safely excluded or revisited without losing the checkpoint.

Migration **008** is additive and preserves migrations 001–007. It introduces
the three sync tables, job counters and `workspace_analysis.progress`. It adds
`append` alongside the existing `merge` and `replace` modes. No original game
payloads or notes are rewritten by migration.

## Review implementation

One local Stockfish process uses one thread and a 32 MB hash by default. A quick
position pass receives up to 35% of the game budget; a deeper pass shares the
remaining time across every player decision. MultiPV searches up to four root
moves, then compares the played move from the same position, searching it
separately if needed. The three highest-ranked alternatives exclude the played
move. Every returned variation is legally replayed before storage. The replay
maintains move history for repetition handling without keeping a full move stack
copy for every position.

`STUDY_ENGINE_GAME_SECONDS` defaults to 300 and cannot exceed 300. Per-position
node/time limits can finish work sooner. Progress records both position coverage
and player decisions compared. New analysis fingerprints include algorithm
version 3, source hashes, engine binary and resource settings; old reviews remain
stored and readable under Earlier reviews. Account toolbar controls share a
font and height and wrap on narrow screens.

## Docker verification and operation

```bash
docker compose --profile study build study-api
docker compose --profile study run --rm study-tests
docker compose --profile web run --rm web-tests
# Back up and prove restore before updating the local service.
docker compose run --rm backup-restore
docker compose --profile web --profile study up -d --wait web study-api
```

For isolated, offline browser QA, use `compose.workspace-qa.yaml` in its own
project (`chess-workspace-qa`, `WEB_PORT=18082`). It serves synthetic DemoStudent
metadata and PGNs. Never select this override for real data. CI uses that transport
and does not contact Lichess. `tests/test_incremental_review.py` covers add-only
preservation, late finishes, unchanged checkpoints on failure/cancel, empty and
filtered imports, ownership, legal real-engine lines, Black's score perspective,
unlimited detected findings and honest partial coverage.

`scripts/verify-study-preservation.py` captures private row checksums and verifies
that all original revisions, memberships, versions, saved studies, reviews and
notes remain unchanged. Keep its baseline outside Git. Only new rows are allowed.
Use it in the study container or a one-shot study service with a private mount.
Backup bundles contain private account data and must stay private.

No whole-library auto-analysis, automatic periodic sync, OAuth, all-month archive
expansion or production-scale claim is introduced by this change.

## API references

Lichess documents time-window exports, metadata without moves and streamed
responses in its [user games export specification](https://raw.githubusercontent.com/lichess-org/api/master/doc/specs/tags/games/api-games-user-username.yaml).
The [ID export specification](https://raw.githubusercontent.com/lichess-org/api/master/doc/specs/tags/games/api-games-export-_ids.yaml)
supports bulk POST exports and explains the delay for ongoing games. Stockfish
integration uses the documented [python-chess engine API](https://python-chess.readthedocs.io/en/latest/engine.html)
for MultiPV, root-move restrictions and search limits. The 24-hour overlap and
transactional checkpoint policy are this application's design choices.
