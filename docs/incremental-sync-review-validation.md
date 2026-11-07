# Incremental import and full-game review validation

## Verified source and synthetic behavior

All application and test execution used Docker. The final image passed **44
private-study tests**, including ten new sync/review tests. The public app and
model suite passed **15 tests**. Dependency verification found no broken
requirements. Earlier checksum-bound migrations 001–007 are unchanged.

The synthetic workspace browser passed **10 scenarios** on desktop/mobile,
including two consecutive metadata-only syncs, toolbar typography, all three
alternative lines, played-continuation replay, notes surviving review polling,
versioned exports, replacement behavior and account isolation. The independent
board/learning browser passed **three scenario groups**. These use explicit
offline Lichess transport; they are separate from live-provider verification.

See `reports/incremental-review/workspace-acceptance.json` and
`reports/incremental-practice/practice-acceptance.json`. The included review and
library captures contain synthetic DemoStudent games only.

## Deployed real-data checks

The local web and study services are healthy after migration 008. A real
67-ply game completed the new review in **125.4 seconds**: all 67 played moves
and all 33 player decisions were checked. It produced five findings and fifteen
alternative lines. The deployed desktop/mobile browser rendered those findings,
replayed a continuation and reported no JavaScript errors or horizontal overflow.
Private screenshots and raw games stay outside Git.

The successful initial live sync inventoried **12,527 IDs**, skipped move
downloads for **12,521 stored games** and published no changed game payloads.
The immediate following sync used the saved checkpoint: it checked **two IDs**
in its recent window and downloaded **zero games**. No missing completed standard
games were available in this live check. Controlled integration tests cover
inserting missing completed games and later completion of pending games.
All fifteen alternatives from the real review were legally replayed and their
resulting FENs verified. Aggregate evidence is in
`reports/incremental-review/live-acceptance.json` and `deployed-browser.json`.

The first live sync exposed a URL-format mismatch in the shortcut used for
skipped PGNs: bulk exports use `/game/ID`, while that shortcut accepted `/ID`.
The job failed without advancing its checkpoint or publishing changes. Header
identity now reuses the main parser's existing URL and `GameId` rules. A
regression test covers completed, empty and known-game bulk-export records.
The failed attempt's local evidence is retained separately from the retry.

A fresh pre-deployment baseline captured 25,040 original game revisions, five
collection versions, 37,725 memberships, one saved study and three existing
workspace engine reviews. These are global storage counts across accounts,
not a single player's distinct-game count. After migration, every original row
matched its prior checksum. The additive progress field is excluded consistently
on both sides of that comparison. The checker permits new rows while requiring
every original row to remain present and unchanged.
The final comparison also passed: the only added row among the checked tables is
the new engine review. Revisions, collection versions, memberships and the saved
study are unchanged. See `reports/incremental-review/preservation.json`.

The pre-deployment backup restored successfully across all **56 existing
tables**, validating SQL row fingerprints, constraints, reader permissions and
artifact bundles. Live initial/follow-up sync and final post-deployment restore
were separate gates. Both live syncs passed, and the post-deployment backup
restored successfully across all **59 tables**, including the new sync tables.
`reports/backup-restore.json` records its bundle and all successful checks.
The final Docker image also passed all 44 private-study tests after the URL fix.

Branch CI is an independent source check; inspect the attached draft PR for its
current status. The local provider/browser/restore evidence above is already
complete. This change does not merge either the parent board/learning work or
the new review branch.

## Scope and remaining limits

An engine score is an estimate, not proof that every mistake was found. Findings
use a 1.50-pawn threshold. Longer games can exhaust the five-minute budget;
partial coverage is labelled and directly tested. Three alternatives require
enough legal root moves; losing positions are never labelled advantageous merely
because one continuation is better than the played move.

The initial metadata scan can take minutes for a large existing library. Later
syncs use a checkpoint plus overlap and pending IDs. Tests exercise new-game
ingestion and late completion even when a live library has no new games. These
checks do not establish multi-year bulk-archive production capacity. All-month
archive scheduling remains disabled, and the earlier January–March archive
publications are unchanged.
