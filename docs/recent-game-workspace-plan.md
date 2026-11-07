# Recent-game study workflow

1. Replace zero-filled import messages with accurate added/up-to-date/pending outcomes and specific skip reasons.
2. Preserve source start times separately from immutable games. Use UTC timestamps for recent-game order; label date-only records honestly. Backfill existing Lichess metadata without downloading old moves or advancing import checkpoints.
3. Interpret “last 3 games”, recent/few games and filtered variants as an exact, bounded cohort. Show that cohort as a table with the answer; never silently answer for the whole library.
4. Make Recent games a prominent entry point, with date, color, clock, opponent, result, opening and review information. Preserve filters and paging when moving between the table and game editor.
5. Connect the selected game, legal trial lines, engine evidence, questions and practice notes in one workspace. Ground trial-position answers in that exact position's engine result.
6. Keep recent-game saved selections pinned. Verify chronology, counts, ownership, no-op/pending imports, table-to-board navigation, context questions and desktop/mobile rendering in Docker; deploy with backup and original-row preservation checks.

No original moves are edited, no whole-library engine review is started, and bulk all-month archives remain disabled.
