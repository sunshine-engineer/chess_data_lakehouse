# Chess historical data pipeline

Containerized Python and PySpark pipelines ingest verified Lichess archives into
partitioned PostgreSQL star models and typed Parquet. Serial Python, bounded
multiprocessing Python, and Spark use the same chess validation and natural keys.

**Default bulk execution permits January 2013; an opt-in configuration permits
January–March 2013. All-month archive scheduling remains disabled. Personal libraries accept any dates through manual
PGNs or user-requested Lichess API imports. Cluster checks use synthetic data.**

## Outline

1. Build and test the non-root Docker runtime.
2. Process one explicitly approved archive and query historical models.
3. Verify recovery, backup/restore and comparable performance measurements.
4. Review the data model, operational runbook and measured case studies.
5. Explore Chess Atlas and its three documented ML case studies.
6. Import manual PGNs or a player's public history and save versioned personal studies.
7. Configure an interactive study board, practice beginner lessons and expand
   explicitly selected archives using the [board and learning guide](docs/board-learning-archives.md).

## Start

Requires Docker with Compose and Linux containers. No host Python, Java, Spark or
PostgreSQL installation is required. Keep an existing `.env`: a database volume
retains its original administrator password.

```bash
# First checkout only:
cp .env.example .env
# Set three separate local passwords in .env.
docker compose build python
docker compose up -d --wait postgres
docker compose run --rm tests
docker compose run --rm python prepare
docker compose run --rm python
docker compose run --rm python-mp
docker compose run --rm spark
```

PowerShell uses `Copy-Item .env.example .env` for the initial copy. One-shot
administrative containers initialize volume ownership and apply versioned migrations;
regular jobs run as UID 10001 with the restricted database loader role.

## Behavior

- Byte-bounded framed Parquet separates source preparation from chess parsing.
- Typed validated Parquet and logged PostgreSQL shard checkpoints support explicit
  resume after interruption; already committed shards are verified and reused.
- Monthly fact refreshes commit atomically. Loading or rerunning January preserves
  February and all other retained months.
- Global game identities prevent cross-archive duplicates. Facts use BIGINT
  dimension keys, retain titles as played, and link to source/run provenance.
- Missing player identities use an explicit unknown dimension member and a quality
  coverage metric. Invalid games go to rejects; publication obeys quality gates.
- `warehouse_python`, `warehouse_python_mp`, and `warehouse_spark` contain complete
  histories. Existing `<engine>_current` views expose that history after a successful
  optimized publication, while retaining the earlier query column shapes.

## Validate and compare

```bash
# Three fresh containers per engine, with alternating execution order:
bash scripts/benchmark.sh 3

# Verify SQL and Parquet backup/restore into a disposable destination:
docker compose run --rm backup-restore

# Optional: real tasks on two separate Docker worker containers, synthetic data only:
docker compose --profile tools --profile cluster run --rm --use-aliases cluster-driver
```

Benchmarks reject mixed source, code, image, configuration and resource settings.
They compare semantic facts, active dimensions and rejects, rather than expecting
warehouse surrogate numbers to be identical. SQL plans accompany the results.

See [optimized comparison](reports/comparison-v2.md), [operations and data models](docs/optimized-pipeline.md),
[delivery evidence](docs/optimized-validation.md), and [portfolio case studies](docs/case-studies.md).
The [earlier benchmark](reports/comparison.md) and [baseline validation](docs/docker-validation.md)
remain historical evidence, not target-scale capacity claims.

Stop services while retaining named data volumes:

```bash
docker compose --profile tools --profile cluster --profile web --profile study --profile study-ai down
```

Lichess archives are [CC0 source data](https://database.lichess.org/).
The default archive's SHA-256 comes from the [official checksum list](https://database.lichess.org/standard/sha256sums.txt).
Credentials, bulk data and backup bundles are excluded from Git. **30 GB/month and
five-year production capacity require representative load testing and are not claimed.**

## Chess Atlas web app and ML laboratory

After loading the warehouse above, run:

```bash
docker compose --profile web build web
docker compose --profile web run --rm ml-train
docker compose --profile web up -d --wait web
```

Open **http://localhost:8080/#overview** for filtered data charts, paginated game exploration,
pipeline benchmarks and a model laboratory. The three documented ML studies are
[outcome classification](docs/ml-outcome.md), [game-length regression](docs/ml-length.md),
and [descriptive player clustering](docs/ml-clusters.md). Results include actual
baselines/comparisons, chronological evaluation and limitations; predictions use
pre-game features only. The app reads PostgreSQL as `chess_reader` and cannot
start training or ingestion.

See [setup, APIs and operations](docs/web-app.md),
[executed web/ML evidence](docs/web-ml-validation.md), and
[evaluation report](reports/ml-evaluation.json). All-month processing remains off.

The finished archive workspace adds linked overview findings, shareable filters,
source-ID search, game details, full cursor browsing and bounded CSV downloads.
Pipeline pages show measured time/memory trade-offs and repeat-run evidence.
**Model insights** separates matchup estimates, baseline/observational comparisons,
evaluation and player profiles. Five case studies retain the application
navigation. See [product acceptance](docs/product-workspace-validation.md) for
populated/empty-state, mobile, recovery and Personal Study regression results.

## Personal Study workspace

Set distinct `STUDY_PASSWORD` and random `STUDY_API_TOKEN` values in your existing
`.env`, then run `make study` (or the Docker commands in the runbook). Open
**http://localhost:8080/#personal**. Create an app account, upload a PGN or paste a
Lichess games API link, and open the resulting player workspace. There is no
approved-month form or default library game-count cap. Player names can be
selected or detected automatically. Repeated games reuse their stored payloads.
**Sync new games** fetches only missing game moves after an initial metadata check;
stored games stay unchanged by default. Full-game Stockfish reviews allow up to
five minutes and explain three alternative lines at detected mistakes. Read the
[sync and review guide](docs/incremental-sync-game-review.md).

Separate pages provide a short study focus, opening comparisons, ten-row game
pages, board/engine review, questions and version-pinned saved studies. Accounts
keep libraries and notes private. Imports run in background jobs, process bounded
chunks and can be cancelled. Library/storage quotas are configurable and disabled
by default. The archive app keeps its read-only database role; the private study
service uses the separate `study` schema. Answers come from computed operations
and actual engine evidence rather than unconstrained generated claims.

Read the [current setup and data model](docs/personal-study.md),
[redesign plan](docs/personal-workspace-plan.md),
[executed validation](docs/personal-workspace-validation.md), and
[workspace browser evidence](reports/personal-demo/workspace-acceptance.json).
The earlier [single-player delivery evidence](docs/personal-study-validation.md)
and [legacy runbook](docs/personal-study-legacy.md) remain historical references.
Private owner access is generated once in ignored `data/owner-access.json` during
migration; sign in with it and change the app password. Other users register normally.
Real games, passwords, screenshots and detailed study results stay outside Git.
