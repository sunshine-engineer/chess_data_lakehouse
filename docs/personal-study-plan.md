# Personal chess study assistant delivery

Historical single-player release plan. The current multi-user redesign follows
[personal-workspace-plan.md](personal-workspace-plan.md); the approved-month form
and 500-game library cap are superseded.

## Outline

1. Import a manually supplied, bounded player-month corpus into an isolated study schema.
2. Produce reproducible opening reports, review queues and board/position exploration.
3. Add explicit, bounded engine analysis and grounded analytics after reports work.
4. Save cohorts and versioned research exports; strengthen and validate ML evidence.
5. Verify Docker, database permissions, browser workflows and documentation before merge.

## Approved boundary

The first personal corpus is **sunny2g, October 2026 (UTC)**, approved in this chat.
Each published version contains at most 500 unique completed standard Lichess games.
Files are manually uploaded; the application never fetches the user's games itself.
Bulk archive ingestion remains January 2013 only and all-month execution stays off.
One configured player-month workspace is served locally; historical personal versions
and saved cohorts remain reproducible within that workspace.

## Acceptance gates

| Requirement | Evidence required |
|---|---|
| Bounded manual ingestion | File/game/ply limits, confirmed player/month, labelled defaults for absent optional metadata, legal mainlines, duplicate/correction handling, atomic version publication, original and typed Parquet lineage |
| Isolation | Separate restricted study writer/service, read-only archive application, internal service authentication, same-origin write protection, no private PGNs in Git |
| Personal report | Player/color/time/opponent filters, opening score and win intervals, comparison with other games against similar opponent-strength groups, linked examples and explicit small-sample limits |
| Replay and positions | Legal board states and move list, clocks/imported annotations labelled by origin, recurring-position lookup and observed alternatives with counts |
| Mistake guidance | Explicit engine jobs for selected games, fixed CPU/hash/node/time budgets, versioned engine evidence, evaluation-loss candidates and replayable alternatives; no unsupported tactical claims |
| Grounded assistant | Approved analytical intents, exact computed numbers, game/version references, unsupported/ambiguous question refusals, measured correctness; optional local model routing with deterministic fallback |
| Research workspace | Saved cohort definitions pinned to immutable versions, checksummed Parquet and PGN/report exports, feature/data dictionary and reproducibility manifests |
| ML | Ratings-only baseline, probability reliability measurements, explicit frozen-model evaluation on a later approved personal version; distinguish synthetic verification from unavailable personal-game results |
| Documentation | Import instructions, schemas, statistical interpretation, engine/AI budgets and limitations, tests, evaluation reports and operating/recovery commands |

Manual user-game acceptance requires the actual PGN. Synthetic fixtures may prove
the software paths but must never be represented as sunny2g's real games or results.
