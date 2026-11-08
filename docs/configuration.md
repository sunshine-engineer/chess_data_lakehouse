# Configuration

Outline: deployment settings, credentials, domain settings, and validation.

`runtime.load()` uses **environment → TOML → defaults**. Set
`CHESS_RUNTIME_CONFIG` to an explicit container path to select another TOML file;
a missing explicit file or unknown key fails immediately. The default is
`config/runtime.toml`. Credentials never belong in TOML.

| Concern | Configuration | Default / boundary |
|---|---|---|
| Database | Standard libpq `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGSSLMODE`, `PGSSLROOTCERT` | Compose uses its private PostgreSQL service; external Kubernetes uses verified TLS |
| Paths | `CHESS_DATA_ROOT`, `CHESS_REPORTS_ROOT`, `STUDY_ROOT`, `ML_ROOT`, `EVIDENCE_ROOT`, `DOCS_ROOT`, `STOCKFISH_PATH` | `/data`, `/reports`, `/study`, `/models`, `/evidence`, `/app/docs`, `/usr/games/stockfish` |
| HTTP | `CHESS_BIND_HOST`, `CHESS_WEB_PORT`, `CHESS_STUDY_PORT`, `CHESS_WORKER_PORT` | `0.0.0.0`, 8080, 8081, 8082 |
| Threads | `CHESS_WEB_THREADS`, `CHESS_STUDY_THREADS` | 4 each; validated 1–64 |
| Private API | `STUDY_SERVICE_URL`, `STUDY_PROXY_TIMEOUT_SECONDS` | `http://study-api:8081`, 300 seconds |
| Upload transport | `STUDY_MAX_UPLOAD_BYTES` | 1 TiB transport ceiling; import records and batches have separate memory bounds |
| Worker mode | `STUDY_WORKER` | Runtime default false; Compose explicitly preserves embedded mode; Kubernetes uses a separate worker |
| Worker timing | `STUDY_WORKER_POLL_SECONDS`, `STUDY_SHUTDOWN_SECONDS`, `STUDY_REPERTOIRE_GAME_SECONDS` | 1, 330, 30 seconds; per-game repertoire budget ≤300 seconds |
| Archive worker | `CHESS_ARCHIVE_POLL_SECONDS`, `CHESS_ARCHIVE_CHECK_SECONDS` | 3 and 3600 seconds; subscriptions still require explicit opt-in |
| Cookies | `STUDY_SECURE_COOKIES` | Compose 0 for localhost; Kubernetes 1, requiring HTTPS at your ingress |

Container ports and mount paths also appear in Services, probes and volume mounts.
Change those together in an overlay. Existing database manifests contain absolute
artifact paths: preserve `/study` and `/data` during a migration. The configurable
Python paths alone do not rewrite stored manifests or remount volumes.

Supported mounted secrets are `PGPASSWORD_FILE`, `PGADMIN_PASSWORD_FILE`,
`LOADER_PASSWORD_FILE`, `READER_PASSWORD_FILE`, `STUDY_PASSWORD_FILE`, and
`STUDY_API_TOKEN_FILE`. Each also supports its plain environment variable without
`_FILE`. Configuring two nonempty sources is rejected. Files are read without
printing their content. Standard libpq TLS/client certificate options remain
available. Compose's `.env` example is for local development; use Kubernetes Secret
files for cluster workloads. Existing Compose passwords are intentionally retained
for compatibility. Do not replace a live `.env` to test new settings.

Role names `chess_loader`, `chess_reader`, `chess_study` are part of the existing
SQL permission contract. Bootstrap receives administrative credentials; regular
services receive only their own role's password. They are not interchangeable.

Domain configuration stays with its owner:

- `config/pipeline.toml`, `config/archive-demo.toml` and optimized `Settings` define
  archive months, shard bounds, publication checks, and Spark settings. CLI archive
  expansion remains explicitly bounded; no automatic all-month backfill is enabled.
- `config/study.toml` defines the legacy engine/assistant contract.
- `personal/workspace/config.py` validates `STUDY_<FIELD>` variables for batch,
  record, ply, disk, storage and engine bounds. For example `STUDY_BATCH_GAMES=100`,
  `STUDY_MAX_RECORD_BYTES=8388608`, `STUDY_ENGINE_NODES=20000`,
  `STUDY_ENGINE_HASH_MB=32`, `STUDY_ENGINE_GAME_SECONDS=300`, and
  `STUDY_MIN_FREE_DISK_BYTES=268435456`. Zero disables only the explicitly optional
  library-game and storage quotas.
- `config/postgres-transfer.toml` controls the independent database transfer tool;
  its endpoint variables have `SOURCE_` / `TARGET_` prefixes.

Build the image after source/config changes. To inspect effective deployment
settings without starting a service:

```bash
docker compose run --rm --no-deps --entrypoint python python -m chess_lakehouse.runtime
```

This prints non-secret values and booleans indicating configured credentials.
It does not prove database connectivity. `python -m chess_lakehouse.health database`
or `study` checks connectivity/schema from inside a configured container.

Standalone Compose workers are optional:

```bash
# During your own maintenance window, stop the embedded worker before switching.
docker compose --profile study stop study-api
docker compose -f compose.yaml -f compose.workers.yaml --profile study --profile workers up -d study-api study-worker
```

Keep the override when recreating the API. Running embedded and standalone workers
on the same study volume causes the foreground worker to reject duplicate ownership.
Only one study-worker and one archive-worker are supported for this deployment.
