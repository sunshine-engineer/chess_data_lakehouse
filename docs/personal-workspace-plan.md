# Personal Study redesign

## Outline

1. Replace the fixed player/month form with accounts and private player libraries.
2. Stream manual PGNs or a validated Lichess API URL into background import jobs.
3. Store game payloads once, reuse revisions across collection snapshots, and paginate analysis.
4. Route completed imports to a focused workspace: findings, games/board, questions and saved studies.
5. Preserve legacy games, verify account isolation, real imports, deduplication and browser flows in Docker.

## Required behavior

- Local Chess Atlas accounts use hashed passwords and private sessions; these are not Lichess accounts/passwords.
- A player can be selected or inferred from a PGN/link. There is no approved-month field, approval checkbox or fixed player restriction.
- Multiple dates and months belong in a player's library; dates are analysis filters, not import permissions.
- Imports have no default game-count cap. Storage quotas are configurable and disabled by default. Processing remains bounded per game/chunk, with disk-space checks and cancellable jobs.
- Only the official HTTPS Lichess user-games endpoint can be downloaded. Required tags/moves are requested automatically; public games need no API token. A provided maximum/date filter is explained and respected.
- Import choices explain merging versus replacing the active collection. Identical games do not create duplicate payloads or snapshots; changed games retain only a distinct revision and shared snapshot references.
- Each game has a stable source/local ID and its own replay URL. Account ownership is checked on every resource.
- Reports show a few useful findings with evidence, uncertainty and a concrete next action. Tactical findings require actual Stockfish analysis.
- Analysis has period/color/clock/opponent filters, pagination, question prompts, replay/engine variations, notes and version-pinned exports.
- Existing games and studies remain preserved. Initial owner access is provisioned privately, not committed. Other users register normally.
- Source code stays on a review branch; deliver a tested app and reviewable PR for the user's review.

## Evidence gates

Docker tests cover sign-up/login/logout, account isolation across all endpoints, multi-month imports, auto player detection, streamed imports beyond 500 games, deduplication/revisions, replacement semantics, malformed/unsafe URLs, HTTP errors/rate limits/cancellation, report arithmetic/filters/evidence and exports. Browser acceptance covers both import paths, analysis navigation, board/game links, question prompts, mobile layout and two accounts. Live Lichess retrieval and legacy migration are checked separately from deterministic tests. Private games and credentials stay outside Git.
