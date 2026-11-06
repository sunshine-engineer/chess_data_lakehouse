# Personal study delivery evidence

## Outline

1. Docker regression, browser and recovery checks.
2. Historical model comparison and local-model routing results.
3. Real personal-data acceptance and remaining limits.

## PGN compatibility update and real import

The supplied October export exposed two parser defects: Lichess `/game/ID` URLs
were rejected, and skipping annotated side variations incorrectly popped the
mainline builder stack. Both are fixed. Source IDs now support common Lichess URL
forms and `GameId`; generic PGNs use stable namespaced content IDs. Optional
metadata defaults and their provenance are visible in reports and exports.

**14 personal integration tests and 8 web/ML tests passed in Docker** after the
fix, including nested variations, clocks/evaluations, source-ID conflicts, missing
fields, terminal-result recovery, stable local IDs, replay/export round trips,
old Parquet compatibility and small-sample/date-provenance validation.
The synthetic browser acceptance additionally uploads a generic PGN, checks its
defaulting notice and replays its long local ID through the web proxy on mobile.

The real manually supplied file was imported through the actual browser UI:
**10 approved October games, 519 mainline plies, no metadata defaults needed**.
Source-derived counts and player colors matched the report and grounded assistant.
Replay, one bounded Stockfish review, a saved cohort and four checksummed exports
passed. Frozen historical models were evaluated on these ten later games, with a
prominent descriptive-only small-sample warning. No model fitting used those games.
This resolves the earlier missing-PGN gate; it does not establish modern-population
accuracy or improvement in chess strength.

Real-game details, exports, screenshots and evaluation results remain private in
ignored `data/validation/` and the Docker study volume. Only synthetic fixtures and
aggregate implementation-validation evidence are committed.

## Verified implementation

Tests run inside Docker; integration tests create disposable databases and private
temporary artifact directories. The independent demo uses project
`chess-study-demo`, port 18081 and `config/study-demo.toml`. Its 24 games are
explicitly synthetic **DemoStudent** games, including a legal Fool's Mate example.
They are never seeded into the real sunny2g workspace.

- Original pipeline regression: **55 passed, 1 web/ML module skipped** in the
  pipeline image. A parallel attempt collided on cluster-wide role initialization;
  the complete sequential rerun passed. Run suites sequentially on one cluster.
- Personal integration: **10 tests** cover parser limits/legality, player/date
  approval, duplicate/correction versions, SQL permissions/immutability, reports,
  cohort Parquet/PGN exports, authenticated API, notes/positions, actual bounded
  Stockfish output, exact-position assistant evidence and refusals. Failure injection
  covers storage errors and a real committed transaction with lost acknowledgement.
  Frozen later-data evaluation uses synthetic games/models and verifies caching.
- Web/ML regression: **8 passed**, including chronological splits, actual serialized
  inference, baseline metrics, stale/corrupt artifacts, input bounds and reader SQL.
- `python -m pip check` reports no broken requirements in the app image.
- [Personal Chromium acceptance](../reports/personal-demo/acceptance.json) covers
  desktop/mobile manual upload, legal replay, recurring-position counts, completed
  Stockfish mate explanation and engine variation, note readback, four export
  formats with actual byte hashes, identical-upload reuse, corrections and an old
  cohort pinned to version 1. Eight fixed assistant questions independently check
  computed counts, intent/filter application, version/game references and refusals.
- [Synthetic populated restore drill](../reports/personal-demo/backup-restore.json)
  matched 30 SQL table fingerprints, restored both artifact bundles, verified
  constraints and reader permissions, and rejected a truncated dump. This is a
  disposable local restore drill, not offsite recovery or an RTO/RPO commitment.
- Existing archive Chromium acceptance also passed against the actual 121,332-game
  January publication, including current model inference, filters, pagination and
  desktop/mobile layouts. The [real-volume restore drill](../reports/backup-restore.json)
  matched 33 table fingerprints. The live personal context confirmed sunny2g,
  October 2026 and zero imported versions at the initial release; the model-training code hash matched
  the published report, and personal documentation links resolved.

Screenshots: [desktop](../reports/personal-demo/study-desktop.png),
[mobile](../reports/personal-demo/study-mobile.png) and
[engine evidence](../reports/personal-demo/engine-evidence.png).

## Real historical ML measurements

Run `20261106T134842Z-4c1ec831` read the existing January 2013 publication as
`chess_reader`: 121,332 available games, 120,000 deterministically sampled,
68,005 train / 23,370 validation / 28,625 test. No source archive was downloaded
or altered. Training took **35.60 seconds** in this one local observation.

| Historical test metric | Ratings + clock | Ratings only | Class prior |
|---|---:|---:|---:|
| Accuracy | 63.91% | 63.85% | 51.03% |
| Log loss | 0.7314 | 0.7334 | 0.8137 |
| Multiclass Brier (0–2) | 0.4569 | 0.4582 | 0.5299 |
| Mean classwise ECE | 0.00579 | 0.00680 | 0.00150 |

Clock features add little over ratings. Constant prior probabilities have low ECE
but worse discrimination/log loss; do not use ECE alone to rank useful models.
Zero of 943 draws is the top predicted class, while the model's aggregate draw
probability (3.3085%) is close to the observed frequency (3.2943%). All draw
probabilities are in one 0–0.1 bin: this does not prove per-game draw discrimination.
No post-hoc calibrator was fitted. The reused historical test is now a comparison
benchmark rather than a new independent confirmation set.

The [full report](../reports/ml-evaluation.json) records classwise bins, support,
splits, source signature, code hash, versions and six saved models. The
[outcome card](ml-outcome.md) interprets the comparison. The initial release report
is preserved separately as [ml-evaluation-initial.json](../reports/ml-evaluation-initial.json).

## Optional model probe

[Live Ollama routing evidence](../reports/personal-demo/router-evaluation.json)
records the actual `qwen2.5:0.5b` model digest and eight fixed questions.
All eight final guarded routes matched expectations, but **zero model proposals
were accepted**: one call fell back on timeout/unavailable output and seven on
semantic disagreement. Those are fallback successes, not model accuracy.
The small local model is not useful enough to recommend enabling by default.
The deterministic assistant remains the default, and its numerical/refusal
browser checks are a separate result. Improving the router requires a larger
independent question set and a model/prompt choice that fits measured local limits.

Reproduce the probe without private games:

```bash
docker compose --profile web run --rm --no-deps -e STUDY_ROUTER=ollama -e STUDY_LLM_URL=http://study-llm:11434 -v ./scripts:/checks:ro -v ./reports:/reports --entrypoint python web /checks/evaluate-study-router.py
```

## User-owned acceptance and limits

The real workspace is configured for **sunny2g · October 2026 UTC**. The supplied
manual file has now passed real import/replay/analysis/export and frozen later-data
evaluation. Coaching usefulness still needs the player's judgment. No synthetic
statistic is a sunny2g result. Ten personal games do not establish modern-game
population quality. No strength/rating improvement is measured or promised.

This is a local, single-owner demonstration with hard import/analysis limits.
Public authentication, multi-user isolation, offsite backups, deletion/retention,
load testing, operational alerts, dedicated job scheduling, engine-sandboxing beyond
the container and broad adversarial assistant evaluation remain future work.
Historical January benchmarks do not prove 30 GB compressed/month or 1–5 year
capacity. Bulk all-month processing remains disabled.

See [runbook and data models](personal-study.md) and [acceptance plan](personal-study-plan.md).
