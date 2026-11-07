-- Add-only sync checkpoints are published in the same transaction as a library.
ALTER TABLE study.import_jobs DROP CONSTRAINT import_jobs_mode_check;
ALTER TABLE study.import_jobs ADD CONSTRAINT import_jobs_mode_check CHECK(mode IN ('append','merge','replace'));
ALTER TABLE study.import_jobs ADD COLUMN sync_mode TEXT;
ALTER TABLE study.import_jobs ADD COLUMN sync_since_ms BIGINT;
ALTER TABLE study.import_jobs ADD COLUMN sync_until_ms BIGINT;
ALTER TABLE study.import_jobs ADD COLUMN metadata_games BIGINT NOT NULL DEFAULT 0;
ALTER TABLE study.import_jobs ADD COLUMN skipped_old_games BIGINT NOT NULL DEFAULT 0;
CREATE TABLE study.library_sync_state (
 owner_id UUID NOT NULL, library_id UUID PRIMARY KEY, watermark_ms BIGINT NOT NULL,
 last_job_id UUID NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(owner_id,library_id) REFERENCES study.libraries(owner_id,id),
 FOREIGN KEY(owner_id,last_job_id) REFERENCES study.import_jobs(owner_id,id)
);
CREATE TABLE study.sync_pending_games (
 owner_id UUID NOT NULL,library_id UUID NOT NULL,game_id TEXT NOT NULL,
 PRIMARY KEY(library_id,game_id),
 FOREIGN KEY(owner_id,library_id) REFERENCES study.libraries(owner_id,id)
);
CREATE TABLE study.import_game_queue (
 owner_id UUID NOT NULL,job_id UUID NOT NULL,game_id TEXT NOT NULL,
 pending BOOLEAN NOT NULL DEFAULT false,resolved BOOLEAN NOT NULL DEFAULT false,
 PRIMARY KEY(job_id,game_id),
 FOREIGN KEY(owner_id,job_id) REFERENCES study.import_jobs(owner_id,id) ON DELETE CASCADE
);
ALTER TABLE study.workspace_analysis ADD COLUMN progress JSONB NOT NULL DEFAULT '{}';
