# Review your recent games

## Daily workflow

1. Open **My libraries → Review recent games**. A successful import also opens
   this table directly.
2. Select **Latest 3**, **Latest 5** or **Latest 10**, or use the date, color,
   clock, result and opponent filters. The count applies after the other filters.
   For example, Latest 3 + Losses means your latest three losses.
3. Open **Open editor** on a row. The board, move list, trial moves, position
   search, questions and practice notes belong to that selected game.
4. Use **Switch games in this study** or Previous/Next game to move within the
   current table page. Back to filtered games keeps the filters, order and page.
5. Request **Analyse complete game** for a full review. To inspect a correction,
   try legal moves on the board, choose a position-search profile and select
   **Evaluate position**. Then ask about that trial position.

Filters and page information are included in links and survive reloading. Times
are displayed in UTC. If the original source supplied only a date, the app labels
its same-day order as approximate instead of inventing a start time.

## Questions that have a defined scope

| Question | Games or position used |
|---|---|
| Summarize my last 3 games? | Exactly the latest three games matching the current filters |
| Summarize my latest three games | Same selection; spelled counts one through ten are supported |
| Summarize my last few games | Latest three matching games |
| Summarize my last 3 blitz games | Latest three matching blitz games |
| Summarize my last 3 losses | Latest three matching losses |
| What should I practise from my last 5 games? | Findings computed from those five games |
| Summarize this game | Only the game open in the editor |
| Summarize (in the game editor) | Defaults to the selected game; library/date/plural-game requests retain their broader scope |
| Why did I lose this game? | That game's completed engine findings; a win/draw is identified correctly |
| What is the best move here? | Completed engine evidence for the selected played position or exact trial line |

Every recent-game answer includes its actual games in a table, with direct
editor links. The app reports when fewer games match than requested. It accepts
counts from 1 to 200 and rejects unsupported recent-count expressions rather
than silently answering for the whole library. Broader conversational requests
remain limited to the supported analytical operations; this is not an unrestricted
chess tutor. A score or result alone does not establish the cause of a loss.

Trial-position answers require **Evaluate position** first and use that exact
legal move history and search profile. A different trial cannot reuse its result.
Changing the board clears the old position answer. Whole-game questions continue
to describe the imported game. Notes are saved on played positions; saved trial
variations are kept separately from the original moves.

## Import messages

Completion messages describe the outcome without a list of zero counters:

- **Added 1 new game.** means a completed game entered the active library.
- **Your library is up to date. No new completed games were found.** means the
  sync completed normally and existing games stayed unchanged.
- **No newly finished games yet. 1 game is still in progress; sync again after
  it finishes.** means the game is pending rather than unsupported or lost.

Import details retain specific counts for unfinished, empty/aborted, unsupported
variant and unrelated games. Pending Lichess games are revisited on the next
sync. File uploads with unfinished games must be uploaded again after completion.
The app does not automatically review all games or continuously poll Lichess.

## Storage and operations

Migration **009** adds `study.game_times`, an auxiliary source-time table, and
the `workspace_game_details` view. Original game payloads, revisions and collection
memberships are unchanged. New imports preserve explicit PGN `UTCDate`/`UTCTime`
in this auxiliary table; syncs retain Lichess `createdAt` timestamps even for
already-stored IDs. Lichess timestamps take precedence over uploaded timestamps.

A one-time metadata-only enrichment fixes ordering for existing libraries that
were imported before start times were retained:

```bash
docker compose exec -T study-api python -m chess_lakehouse.personal.workspace.chronology \
  --account YOUR_APP_ACCOUNT --player YOUR_LICHESS_NAME
```

Run it after migration, with no competing Lichess download. It inventories IDs
and timestamps in bounded batches and updates only games already owned by that
app account. It downloads no moves, creates no games or collection versions and
does not advance the incremental import checkpoint. It is safe to repeat after
an interrupted enrichment. A large history can take several minutes.

The latest-game selection is resolved once for a report, so its counts, group
comparisons and example links use the same games. Recent saved studies also pin
their exact game IDs (at most 200), preventing later time enrichment from changing
their membership. Existing saved studies retain their original filters and version.

`import_jobs.skip_counts` and `pending_games` support precise messages, while
`outcome` in the owner-checked job API supplies the same copy to the UI. New
engine/practice columns in the games table show stored review status. All SQL
filters and sorting choices are validated; game editor requests resolve ownership
before replay or analysis. No private FEN submitted by a client is trusted.

Tests: `tests/test_recent_workspace.py` covers chronology, exact question scopes,
filters, pinned saved selections, ownership, source-time enrichment, pending
messages and exact trial evidence. `tests/browser/recent_smoke.py` exercises the
player's end-to-end workflow on desktop/mobile using synthetic games. All checks
run in Docker; live user data and captures stay outside Git.

The source metadata fields and export ordering follow the official
[Lichess game JSON schema](https://raw.githubusercontent.com/lichess-org/api/master/doc/specs/schemas/GameJson.yaml)
and [user-games export specification](https://raw.githubusercontent.com/lichess-org/api/master/doc/specs/tags/games/api-games-user-username.yaml).
