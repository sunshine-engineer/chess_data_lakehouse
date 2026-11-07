# Recent-game workspace validation

## Docker and synthetic checks

- **62 private-study tests passed**, including 18 recent-workspace cases covering
  exact counts, same-day chronology, count grammar, composed filters, saved cohort
  membership, source-time enrichment, pending messages and trial/played questions.
- **15 public app/model tests passed**.
- **95 source files matched** the built Docker image byte for byte.
- The new desktop/mobile browser passed four scenario groups: import to recent
  table, exact last-three answer/editor selection, legal trial/evidence question,
  and filters/paging/reload preservation.
- Existing study browser acceptance passed ten scenarios; board/learning browser
  acceptance passed three scenario groups.

See `reports/recent-study/recent-acceptance.json`,
`reports/recent-study-regression/workspace-acceptance.json` and
`reports/recent-practice/practice-acceptance.json`. Published captures contain
synthetic DemoStudent games only. These offline checks do not stand in for live
source or real-library verification.

The new browser check exposed an immediate-back navigation issue while a game
was loading. The back link is now set before awaiting data, with a loading state
that hides stale editor controls. A final integration check also caught an older
position-question call omitting its optional trial list; an empty trial is now
the explicit default. Both corrected paths are covered by the passing suite.
Real-library browser testing also exposed a fast trial-move/profile interaction:
changing the profile while legality confirmation was pending could discard the
move. Profile changes now preserve that pending move, and position-sensitive
actions wait for confirmation. The browser suite explicitly holds the trial
response while changing profiles and then verifies the trial engine answer.
The real mobile check additionally exercised an answer containing a games table.
Grid children now have a zero minimum width so the table scrolls within its panel
rather than widening the page. The synthetic mobile check includes that answer.

## Existing data and source-time enrichment

The fresh baseline contains 25,044 original revisions across accounts, seven
collection versions, 62,772 memberships, one saved study and five existing
engine-review records. These are global storage counts, not one player's distinct
game total. Migration 009 is additive; original revisions and prior migrations
are unchanged. The row checker excludes only the newly added progress/cohort
columns when comparing pre-migration records.

Before deployment, backup and isolated restore verified all 59 existing tables,
including row fingerprints, constraints, reader permissions and both artifact
bundles. Its private bundle is retained locally.

Metadata-only enrichment checked 12,532 Lichess records and restored start times
for all 12,525 games in the selected real library. It downloaded no moves. The
actual checkpoint-table row fingerprint matched the pre-deployment backup after
enrichment, before any deliberate live sync test. Original PGNs and moves are
not rewritten to add chronology.

An independent live check compared the latest three stored games against a fresh
50-game Lichess metadata response. IDs, start times and result counts matched:
two wins and one loss in that checked selection. The answer and games table both
contained exactly three games. A “this game” summary used one game; a real trial
question used the exact quick-search position and a legally replayable engine PV.
Two live incremental syncs returned “Your library is up to date. No new completed
games were found.” without zero-counter lists or the generic exclusion warning.
See `reports/recent-study/live-acceptance.json`.

The original-row preservation check passed after enrichment and live syncs: no
original revision, collection, membership, saved study or existing workspace
review changed. See `reports/recent-study/preservation.json`.
The final deployed desktop/mobile browser passed with the real library, including
the exact three-game answer, an implicit selected-game summary, and a trial
question with its answer table present on mobile. Temporary test sessions were
revoked, and real screenshots remain private. See `deployed-browser.json` in the
same report folder.

The final backup/isolated restore passed **all 60 tables**, including chronology,
row fingerprints, constraints, reader permissions and both artifact bundles.
The bundle is recorded in `reports/backup-restore.json`. The final original-row
check again reports zero changed or added rows among the preserved core tables.
The final 62-test pass also verifies implicit selected-game questions: “Summarize”
and “Why did I lose?” in the editor default to that game; explicit plural-game,
library and date-range requests retain their requested analytical scope.

## Boundaries

Recent-game counts apply after the other filters and are limited to 1–200.
Unknown start times remain labelled approximate. New recent-game saved studies
pin their game IDs; existing saved studies keep their original filters/version.
Tactical questions use engine evidence, with no promise that every mistake is
found. The app still does not infer arbitrary natural-language filters or perform
automatic whole-library review. Bulk archive scheduling remains disabled.
