# Incremental personal sync and full-game coaching

## Outline

1. Save a successful Lichess sync checkpoint and unfinished-game IDs per private library.
2. Initially scan lightweight game metadata, then fetch moves only for missing completed games; later request a bounded new time window plus previously unfinished games.
3. Make add-only imports the UI default. Keep explicit refresh/correction and replacement actions; retain versions and notes.
4. Improve library copy and give the account toolbar consistent typography, alignment and mobile wrapping.
5. Scan every played position, then compare the player's decisions using three candidate continuations, within a five-minute search budget.
6. Explain scores from the player's perspective, show all detected mistakes with three replayable alternatives where legal, and persist progress/coverage.
7. Verify Docker integration, legal lines and explanations, checkpoint failure/cancellation behavior, real-data preservation and rendered desktop/mobile flows.

## Boundaries

No automatic all-month archive ingestion or background review of the entire library.
New-game sync never overwrites a stored game. A deliberate full refresh remains
available for corrections. Custom filtered/capped imports keep their exact bounds
and do not move an unrestricted-history checkpoint. Pending games are revisited
because a game can finish after its start-time window has passed.

All moves must be inspected; the app must never describe an unfinished search as
complete. Stockfish's top lines are search estimates, not proof that every mistake
has been found. Three alternatives are offered when at least three other legal
moves exist. Losing positions get realistic defensive explanations rather than a
manufactured claim of advantage. Earlier review evidence stays readable.
