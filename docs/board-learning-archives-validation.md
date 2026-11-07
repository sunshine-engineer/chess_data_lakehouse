# Board, learning and archive-scale validation

## Source and synthetic checks

All application execution and tests use Docker. QA accounts, PGNs and captures
are explicitly synthetic; they are separate from the user's real libraries.

| Check | Current evidence |
|---|---|
| Python, multiprocessing, Spark and PostgreSQL regression | 57 passed, 6 modules skipped because the runner has no Flask; web/study checks run in their own image |
| Public analytics/model/API regression | 15 passed, including all-published-month filtering, archive planning and workload selection |
| Private study/board/learning regression | 33 passed, including legal castling/en passant/promotion, account isolation, durable variations, real Stockfish and drill grading |
| Final queue and teaching-source checks | 3 passed: shared queue bounds and reproducible samples from HTTP/HTTPS Site headers |
| Study browser regression | 9 scenarios passed: imports, private libraries, engine findings, unsaved notes, saved studies, exports and navigation |
| New board/learning browser acceptance | 3 scenario groups passed on desktop/mobile; seven lessons, legal trials, saved configuration, promotion, private progress and guest practice |
| Public empty-data browser regression | 9 scenarios passed, including mobile pipeline/import controls |

See `reports/practice/practice-acceptance.json` and the associated synthetic
desktop/mobile captures. Pipeline/public/study test modules cover different
dependencies; a runner skip does not stand in for passing a web or study test.
Latest small display refinements are rechecked before release.

## Private-data preservation

Before the change, a consistent read-only fingerprint captured 25,038 immutable
game revisions, four collection versions, 25,204 memberships, 1,353,384 indexed
positions, one saved study and two existing engine reviews across the local
accounts. These are global storage counts, not one player's unique-game count.
The same fingerprints matched after the environment/Docker restart.

The earlier backup/isolated restore check passed for all 48 existing tables,
including row fingerprints, constraints, archive reader isolation, private files
and deliberate corrupt-backup rejection. Migrations 006/007 are additive;
earlier checksum-bound migrations are unchanged. A post-deployment restore and
preservation comparison remain release gates below.

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

## Live release gates

- [ ] Main parallel-Python publication contains all 403,928 accepted games.
- [ ] Historical teaching examples are materialized from all three verified sources, with input/sample counts and no player identities.
- [ ] New app images and migrations 006/007 are deployed and healthy.
- [ ] Real-library learning evidence and public historical examples are checked against the deployed APIs.
- [ ] Public desktop/mobile acceptance passes on the expanded retained archive.
- [ ] Pre-existing private-data fingerprints match after deployment.
- [ ] Backup/isolated restore includes the new academy/private tables and passes.
- [ ] Both complete workload repeats finish; all three historical models match semantically.
- [ ] Final branch CI and review artifacts correspond to the delivered commit.

No unfinished gate is implied by the source test results above. This document is
updated with terminal live evidence before the work is marked complete.
