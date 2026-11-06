# Personal Study workspace validation

## Outline

1. Verify account isolation and chunked, multi-month imports.
2. Exercise the complete workflow in a browser using labelled synthetic games.
3. Check the existing owner's migration and explicitly authorised public-history import separately.
4. Verify backup recovery and state the remaining boundaries.

## Docker acceptance

| Check | Executed result |
|---|---|
| Personal parser and account workspace | 25 tests passed, including 1,200-game imports in 17-game chunks, identical reimports, corrections and replacement |
| Accounts and permissions | Two accounts; private reports, games, imports, notes, engine jobs, questions and exports; CSRF and session revocation checked |
| Defensive imports | Missing metadata and dates, generic PGNs, invalid mainlines, cancelled jobs, quotas, safe API URLs, redirect refusal and full-minute HTTP 429 delay |
| Empty historical games | Empty/aborted games are excluded with an explanation; a valid collection still publishes |
| Study evidence | Exact aggregates, period filters, real Stockfish mate evidence, indexed recurring positions and unsupported-question refusal |
| Exports | Parquet types, immutable saved selections, four checksummed formats and temporary-file cleanup |
| Web/ML regression | 8 tests passed |
| Desktop/mobile browser | 8 end-to-end scenarios passed with no JavaScript errors; includes engine-polling draft preservation and two-account isolation |
| Dependency check | `python -m pip check` passed inside the web image |

The browser API transport is explicitly **offline and synthetic**, enabled only
through `compose.workspace-qa.yaml`. It validates URL handling and application
flows; it does not establish live provider availability. See the machine-readable
[browser acceptance](../reports/personal-demo/workspace-acceptance.json) and the
[desktop](../reports/personal-demo/workspace-focus-desktop.png),
[mobile](../reports/personal-demo/workspace-focus-mobile.png) and
[board](../reports/personal-demo/workspace-board.png) captures.

One parallel local test attempt collided while changing shared PostgreSQL roles.
The personal suite and web/ML suite then passed sequentially. CI runs these
suites sequentially; test databases remain separate from the app database.

## Existing data and real provider

The original study was backed up and restored before the upgrade. Its two
collection versions, including the 156-game October collection, were migrated
to the explicitly provisioned owner account. Existing game files, old tables,
saved cohorts, review notes and completed engine evidence remain preserved.
Migrations 004 and 005 are additive and recorded with checksums.

The user explicitly authorised a complete public-history Lichess import, with
no game-count option in the supplied URL. An initial live attempt found an empty
historical game and stopped before publication: the active collection remained
156 games, staging was cleared, and no orphan payloads remained. The importer
was corrected and regression-tested before retrying.

The final live-import and restore results are recorded here only after completion.
Private job records, credentials, game details and real-data browser captures
are retained under ignored `data/validation/workspace-live/`; they are not Git
artifacts. Initial local access is in ignored `data/owner-access.json`.

## Boundaries

This is a tested local portfolio workspace. It does not establish public-hosting
readiness, multi-tenant adversarial security, unrestricted chess-variant support,
or five-year capacity at 30 GB compressed archives per month. The current study
board supports standard chess; excluded records remain counted and explained.

The assistant uses computed operations and explicit engine evidence. It is not
an open-ended LLM coach. Per-task CPU/memory/time budgets remain in place while
the default library game-count and payload-storage quotas are disabled.

Bulk ingestion remains January 2013 only, with `all_months_enabled = false`.
Personal account libraries can contain any dates. These are separate workflows.
The changes remain on a review branch for the owner's acceptance.
