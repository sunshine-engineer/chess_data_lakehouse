# Board, archive scale and beginner learning

## Outline

1. Replace font glyphs with original SVG chess pieces and an accessible interactive board.
2. Add saved board preferences, legal trial moves, promotion choice, variations and bounded position analysis.
3. Enable explicit archive selections, aggregate archive statistics and workload-aware benchmark reports; retain the paused all-month stream.
4. Materialize a bounded, reproducible historical teaching sample without duplicating full archives in PostgreSQL.
5. Add opening, middlegame and endgame lessons with personal/historical examples, verified exercises and private progress.
6. Execute Docker API/browser tests, preserve current private data, deploy locally and deliver an unmerged review.

## Required behavior

- Board preferences cover themes, piece styles, size, coordinates, orientation and move highlights. Move exploration never changes the imported mainline.
- The server validates legal moves, castling, en passant and promotions. Keyboard and click interaction work on desktop/mobile. Saved variations and position-analysis results remain account-owned.
- Position searches share one bounded engine worker, expose their search budget and distinguish evaluation from guaranteed chess truth.
- Archive selection uses official source URLs, checksums, expected counts and disk/concurrency bounds. No automatic all-month execution is enabled.
- Benchmarks compare the same workload and settings; scale, repetitions, startup/IO scope and client-versus-database memory remain explicit. A measured winner is not a general capacity claim.
- Lessons teach development/center/king safety, forcing replies and planning, and promotion/basic mates/opposition. Advice is original text; rule checks and terminal-board goals are computed.
- Personal recommendations cite the selected user's actual games/evidence. Historical examples are a labelled bounded sample; foundation diagrams are labelled authored positions. Historical moves are not automatically called optimal.
- Progress is private, persisted and idempotent on retries. Completing exercises is not evidence of a rating gain.
- Existing libraries, saved studies, model artifacts, credentials and unrelated planning files remain preserved.

## Evidence

Meaningful tests must cover account isolation, promotion/castling/en-passant legality,
variation persistence, position queue/caching/budgets, preferences, exercise grading,
learning provenance/progress, archive bounds, cumulative stats and comparable workload
reports. Browser checks include board configuration, trial moves, learning chapters,
private/public source switching, recoverable errors and mobile layout. Live archive
scale and any repeated measurements are recorded separately from synthetic checks.
