# Board, learning and archive-scale validation

## Source and synthetic checks

All application execution and tests use Docker. QA accounts, PGNs and captures
are explicitly synthetic; they are separate from the user's real libraries.

| Check | Current evidence |
|---|---|
| Python, multiprocessing, Spark and PostgreSQL regression | 57 passed, 6 modules skipped because the runner has no Flask; web/study checks run in their own image |
| Public analytics/model/API regression | 15 passed, including all-published-month filtering, archive planning and workload selection |
| Private study/board/learning regression | 34 passed in CI, including legal castling/en passant/promotion, account isolation, durable variations, real Stockfish and drill grading |
| Final queue and teaching-source checks | 3 passed: shared queue bounds and reproducible samples from HTTP/HTTPS Site headers |
| Study browser regression | 9 scenarios passed: imports, private libraries, engine findings, unsaved notes, saved studies, exports and navigation |
| New board/learning browser acceptance | 3 scenario groups passed on desktop/mobile; seven lessons, legal trials, saved configuration, promotion, private progress and guest practice |
| Public empty-data browser regression | 9 scenarios passed, including mobile pipeline/import controls |

See `reports/practice/practice-acceptance.json` and the associated synthetic
desktop/mobile captures. Pipeline/public/study test modules cover different
dependencies; a runner skip does not stand in for passing a web or study test.
The final display refinements were also checked against the deployed app.

## Private-data preservation

Before the change, a consistent read-only fingerprint captured 25,038 immutable
game revisions, four collection versions, 25,204 memberships, 1,353,384 indexed
positions, one saved study and two existing engine reviews across the local
accounts. These are global storage counts, not one player's unique-game count.
The same fingerprints matched after the environment/Docker restart.

The initial backup/isolated restore check passed for all 48 existing tables,
including row fingerprints, constraints, archive reader isolation, private files
and deliberate corrupt-backup rejection. Migrations 006/007 are additive;
earlier checksum-bound migrations are unchanged. A post-deployment restore and
preservation comparison were also verified after deployment. The expanded-archive
pre-deployment restore verified 50 tables; the post-deployment restore verified
all 56 tables, including the new teaching and private practice state.

One intermediate attempt failed because the mounted shell script was edited while
Bash was reading it. Its unfinished bundle was not reported as a successful
restore. The command now copies the script inside its container before executing
it, keeping each run immutable. Both the stable-script rerun and the new
container-frozen-script command passed all 56 table checks. The final bundle is
recorded in `reports/backup-restore.json`.

## Approved live workload

The user selected January–March 2013: 121,332 + 123,961 + 158,635 = 403,928
source records. Official compressed source checksums are fixed in
`config/archive-demo.toml`. The default January-only configuration and the
disabled all-month schedule remain intact.

The main app expands only its parallel-Python publication; the other two retained
app publications keep their original January scope. The public catalog and month
picker expose the actual retained coverage for each engine. The isolated scale
experiment processes all three months through all three engines. Its report is
separate from the original January comparison.

Experiment group: `scale-20261107T072550`.
Pinned image tag: `chess-lakehouse-scale:20261107T072541`.
Image digest: `sha256:da777adbc7152f46c7dae8b68e1f0394b6306a1e84c6cf39357582664d46f00a`.

The Docker/environment restart interrupted one unpublished Spark job. Container
and database inspection confirmed it had stopped. That job was explicitly
abandoned; six completed fresh monthly runs were retained. Workload continuation
uses the original pinned image and starts a fresh Spark replacement. Resumed
partial jobs are excluded from comparisons. Raw monthly provenance stays in the
local data/report volumes.

Measurements are local observations on a shared host. Source preparation and
downloads are excluded; each monthly batch includes engine startup. Per-workload
time/CPU/Parquet totals sum its months; peak client memory is the maximum monthly
peak. PostgreSQL resources and other host activity are outside those client
metrics. Two complete repeats do not establish a general engine ranking or
30 GB compressed/month and multi-year production capacity.
`reports/scale/environment.json` records uncontrolled host activity/cache state
and the interrupted-job replacement; it is part of interpreting the measurements.

## Live release gates

- [x] Main parallel-Python publication contains all 403,928 accepted games.
- [x] Historical teaching examples are materialized from all three verified sources, with input/sample counts and no player identities.
- [x] New app images and migrations 006/007 are deployed and healthy.
- [x] Real-library learning evidence and public historical examples are checked against the deployed APIs.
- [x] Public desktop/mobile acceptance passes on the expanded retained archive.
- [x] Pre-existing private-data fingerprints match after deployment.
- [x] Backup/isolated restore includes the new academy/private tables and passes.
- [x] Both complete workload repeats finish; all three historical models match semantically.
- Final branch CI and review acceptance must match the delivered head: inspect the live checks on [PR #7](https://github.com/sunshine-engineer/chess_data_lakehouse/pull/7). CI state is external to the commit it validates, rather than a self-updating checkbox.

Source and recovery updates `3224054` and `c939dfe` passed CI (runs 42369695481
and 42370262031). The final delivered head is verified against the PR's live
checks before closure. Earlier stacked PRs are not merged by this work.

`reports/board-learning-live.json` verifies both real 12,519-game libraries,
403,928 archive facts and 1,800 historical teaching games. Every sampled position,
SAN/UCI move and resulting FEN was checked. The examples contain 1,800 opening,
1,721 middlegame and 677 endgame positions. These are teaching-sample counts,
not full-archive phase statistics. Existing January model artifacts remained fresh.
`reports/archive-learning-live/acceptance.json` records the read-only desktop/mobile
checks against the deployed larger archive and historical lessons.
The existing populated-app browser suite also passed all 12 scenarios against
the deployed service (`reports/product-expanded/acceptance.json`).

The completed scale group contains 18 fresh monthly runs: two complete
403,928-game workload repeats per path. All 12 equality checks passed. The deployed
benchmark API serves the same report (`reports/scale/api-acceptance.json`). The
original January evidence remains separate and unchanged.
The final benchmark browser check verified all 18 monthly rows, all three source
months, report download and desktop/mobile layout
(`reports/scale/browser-acceptance.json`).
