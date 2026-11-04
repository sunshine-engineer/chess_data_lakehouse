-- Applied once by the migration administrator. Runtime jobs do not own warehouses.
CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE TABLE IF NOT EXISTS control.schema_migrations (
 version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), checksum TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control.pipeline_runs (
 run_id TEXT PRIMARY KEY, engine TEXT NOT NULL CHECK(engine IN ('python','python_mp','spark')),
 source_month DATE NOT NULL, source_sha256 TEXT NOT NULL, benchmark_group TEXT NOT NULL,
 config_hash TEXT NOT NULL, code_hash TEXT NOT NULL, image_id TEXT NOT NULL,
 settings JSONB NOT NULL, runtime JSONB NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','failed','complete','abandoned')),
 started_at TIMESTAMPTZ NOT NULL DEFAULT now(), heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ, error TEXT, report JSONB
);
CREATE INDEX IF NOT EXISTS pipeline_runs_group ON control.pipeline_runs(benchmark_group, engine, status);
CREATE TABLE IF NOT EXISTS control.run_shards (
 run_id TEXT NOT NULL REFERENCES control.pipeline_runs(run_id), shard_id INTEGER NOT NULL,
 checksum TEXT NOT NULL, row_count BIGINT NOT NULL CHECK(row_count >= 0),
 loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(run_id, shard_id)
);
CREATE TABLE IF NOT EXISTS control.month_publications (
 engine TEXT NOT NULL, source_month DATE NOT NULL,
 run_id TEXT NOT NULL REFERENCES control.pipeline_runs(run_id), source_sha256 TEXT NOT NULL,
 accepted_games BIGINT NOT NULL CHECK(accepted_games >= 0),
 published_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(engine,source_month)
);
