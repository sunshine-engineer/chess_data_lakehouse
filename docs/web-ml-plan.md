# Web analytics and ML delivery

## Outline

1. Read-only web application over the retained PostgreSQL models.
2. Reproducible, bounded ML training with independent measured case studies.
3. Docker execution, API/browser verification and a documented handover.

## Acceptance boundary

- Overview: real counts, date coverage, results, ratings, speeds and openings;
  filters affect charts and paginated game exploration.
- Engineering: measured pipeline statistics with historical benchmark provenance.
- ML: outcome classification, game-length regression and descriptive player
  clustering. Each includes a baseline or comparison, evaluation, reproducible
  commands, features, limitations and a model card.
- Interactive predictions use only information available before a game; no moves,
  results, opening inferred from moves or current player titles enter predictors.
- Supervised models use separated chronological train/validation/test days;
  imputation and scaling fit on training data, tuning uses validation only.
- Training is an explicit Docker command with CPU/memory and row limits. The web
  process cannot start ingestion, training or SQL writes. Artifacts are immutable,
  checksummed and mounted read-only in the application.
- Real data remains January 2013 only. No new archive downloads, all-month streaming,
  cloud deployment or claims of modern-game/general production accuracy.
- Docker tests cover queries, limits, invalid inputs, evaluation boundaries,
  missing/stale artifacts, and browser interactions at desktop and mobile sizes.
- Existing ingestion/recovery tests remain green. Personal planning notes and
  notebooks remain untouched.

The app serves locally on a loopback-bound port, using the PostgreSQL reader role.
Public deployment and authentication are separate future work.
