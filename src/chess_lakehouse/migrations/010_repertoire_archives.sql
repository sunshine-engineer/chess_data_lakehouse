-- Durable queues are separate from immutable imported games and reviews.
CREATE TABLE study.repertoire_builds (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts(id) ON DELETE CASCADE,
 snapshot_id UUID NOT NULL, status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','paused','complete')),
 source_stage TEXT NOT NULL DEFAULT 'personal', archive_fallback BOOLEAN NOT NULL DEFAULT true,
 archive_cursor JSONB NOT NULL DEFAULT '{}', error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(owner_id,id), UNIQUE(owner_id,snapshot_id),
 FOREIGN KEY(owner_id,snapshot_id) REFERENCES study.library_snapshots(owner_id,id) ON DELETE CASCADE
);
CREATE TABLE study.repertoire_tasks (
 build_id UUID NOT NULL REFERENCES study.repertoire_builds(id) ON DELETE CASCADE,
 source_key TEXT NOT NULL, revision_id UUID, payload JSONB, player TEXT NOT NULL,
 source_month TEXT, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','complete','failed','skipped')),
 error TEXT, coverage JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(build_id,source_key)
);
CREATE INDEX repertoire_work ON study.repertoire_tasks(build_id,status,source_key);
CREATE TABLE study.repertoire_exercises (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts(id) ON DELETE CASCADE,
 build_id UUID NOT NULL, source_key TEXT NOT NULL, ply INTEGER NOT NULL CHECK(ply>=0),
 phase TEXT NOT NULL CHECK(phase IN ('opening','middlegame','endgame')), fen TEXT NOT NULL,
 evidence JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(owner_id,id), UNIQUE(build_id,source_key,ply),
 FOREIGN KEY(build_id,source_key) REFERENCES study.repertoire_tasks(build_id,source_key) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,build_id) REFERENCES study.repertoire_builds(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX repertoire_list ON study.repertoire_exercises(owner_id,build_id,phase,created_at,id);
CREATE TABLE study.repertoire_attempts (
 owner_id UUID NOT NULL, attempt_id UUID NOT NULL, exercise_id UUID NOT NULL,
 move TEXT NOT NULL, correct BOOLEAN NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,attempt_id),
 FOREIGN KEY(owner_id,exercise_id) REFERENCES study.repertoire_exercises(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX repertoire_progress ON study.repertoire_attempts(owner_id,exercise_id,created_at);
CREATE TABLE study.archive_jobs (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts(id) ON DELETE CASCADE,
 source JSONB NOT NULL, engine TEXT NOT NULL CHECK(engine IN ('python','python_mp','spark')),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','paused','cancelled','complete','failed')),
 action TEXT NOT NULL DEFAULT 'run' CHECK(action IN ('run','pause','cancel')),
 phase TEXT NOT NULL DEFAULT 'Queued', progress JSONB NOT NULL DEFAULT '{}',
 run_id TEXT, error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX archive_work ON study.archive_jobs(created_at) WHERE status='queued';
CREATE TABLE study.archive_subscriptions (
 owner_id UUID PRIMARY KEY REFERENCES study.accounts(id) ON DELETE CASCADE,
 enabled BOOLEAN NOT NULL DEFAULT false, max_download_bytes BIGINT NOT NULL CHECK(max_download_bytes>0),
 engine TEXT NOT NULL CHECK(engine IN ('python','python_mp','spark')), checked_at TIMESTAMPTZ, error TEXT
);
GRANT USAGE ON SCHEMA study TO chess_loader;
GRANT SELECT,UPDATE ON study.archive_jobs,study.archive_subscriptions,study.repertoire_builds TO chess_loader;
GRANT SELECT,INSERT,UPDATE ON study.repertoire_tasks TO chess_loader;
GRANT INSERT ON study.archive_jobs TO chess_loader;
-- The loader does not receive access to accounts, sessions, private game payloads or attempts.
