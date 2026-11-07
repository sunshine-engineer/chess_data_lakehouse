-- Additive account-owned libraries. Legacy study tables remain intact for migration.
CREATE TABLE study.accounts (
 id UUID PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE study.sessions (
 token_hash TEXT PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts,
 csrf TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX study_session_expiry ON study.sessions(expires_at);
CREATE TABLE study.auth_attempts (
 key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
 window_start TIMESTAMPTZ NOT NULL DEFAULT now(), blocked_until TIMESTAMPTZ
);
CREATE TABLE study.libraries (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts,
 player TEXT NOT NULL, player_key TEXT NOT NULL, active_snapshot UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(owner_id,player_key), UNIQUE(owner_id,id)
);
CREATE TABLE study.game_revisions (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts,
 game_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, payload JSONB NOT NULL,
 game_date DATE, white_name TEXT NOT NULL, black_name TEXT NOT NULL,
 white_rating INTEGER, black_rating INTEGER, game_result TEXT NOT NULL,
 speed TEXT NOT NULL, opening TEXT NOT NULL, eco TEXT, ply_count INTEGER NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(owner_id,game_id,payload_sha256), UNIQUE(owner_id,id), UNIQUE(owner_id,id,game_id)
);
CREATE INDEX study_revision_game ON study.game_revisions(owner_id,game_id);
CREATE TABLE study.library_snapshots (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL, library_id UUID NOT NULL,
 version INTEGER NOT NULL, parent_id UUID, game_count BIGINT NOT NULL,
 dataset_sha256 TEXT NOT NULL, source_sha256 TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(owner_id,library_id) REFERENCES study.libraries(owner_id,id),
 UNIQUE(library_id,version), UNIQUE(owner_id,id)
);
ALTER TABLE study.libraries ADD FOREIGN KEY(owner_id,active_snapshot) REFERENCES study.library_snapshots(owner_id,id);
CREATE TABLE study.snapshot_games (
 owner_id UUID NOT NULL, snapshot_id UUID NOT NULL, game_id TEXT NOT NULL, revision_id UUID NOT NULL,
 PRIMARY KEY(snapshot_id,game_id),
 FOREIGN KEY(owner_id,snapshot_id) REFERENCES study.library_snapshots(owner_id,id),
 FOREIGN KEY(owner_id,revision_id,game_id) REFERENCES study.game_revisions(owner_id,id,game_id)
);
CREATE INDEX study_snapshot_revision ON study.snapshot_games(revision_id,snapshot_id);
CREATE VIEW study.workspace_game_facts AS
 SELECT m.owner_id,m.snapshot_id,m.game_id,m.revision_id,r.game_date,r.speed,r.opening,r.eco,r.ply_count,
 CASE WHEN lower(r.white_name)=lower(l.player) THEN 'white' ELSE 'black' END AS player_color,
 CASE WHEN lower(r.white_name)=lower(l.player) THEN r.white_rating ELSE r.black_rating END AS player_rating,
 CASE WHEN lower(r.white_name)=lower(l.player) THEN r.black_rating ELSE r.white_rating END AS opponent_rating,
 CASE WHEN r.white_rating IS NULL OR r.black_rating IS NULL THEN 'unknown'
      WHEN (CASE WHEN lower(r.white_name)=lower(l.player) THEN r.black_rating-r.white_rating ELSE r.white_rating-r.black_rating END)>200 THEN 'stronger'
      WHEN (CASE WHEN lower(r.white_name)=lower(l.player) THEN r.black_rating-r.white_rating ELSE r.white_rating-r.black_rating END)<-200 THEN 'weaker'
      ELSE 'similar' END AS opponent_band,
 CASE WHEN r.game_result='1/2-1/2' THEN 'draw'
      WHEN r.game_result=(CASE WHEN lower(r.white_name)=lower(l.player) THEN '1-0' ELSE '0-1' END) THEN 'win' ELSE 'loss' END AS result,
 (CASE WHEN r.game_result='1/2-1/2' THEN 0.5
      WHEN r.game_result=(CASE WHEN lower(r.white_name)=lower(l.player) THEN '1-0' ELSE '0-1' END) THEN 1.0 ELSE 0.0 END)::double precision AS points,
 CASE WHEN lower(r.white_name)=lower(l.player) THEN r.black_name ELSE r.white_name END AS opponent
 FROM study.snapshot_games m JOIN study.game_revisions r ON r.id=m.revision_id AND r.owner_id=m.owner_id
 JOIN study.library_snapshots s ON s.id=m.snapshot_id AND s.owner_id=m.owner_id
 JOIN study.libraries l ON l.id=s.library_id AND l.owner_id=m.owner_id;
CREATE TABLE study.import_jobs (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts, library_id UUID,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('file','lichess','legacy')),
 source_url TEXT, input_path TEXT, player TEXT, mode TEXT NOT NULL CHECK(mode IN ('merge','replace')),
 status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed','cancelled')),
 phase TEXT NOT NULL DEFAULT 'Waiting', bytes_received BIGINT NOT NULL DEFAULT 0,
 games_processed BIGINT NOT NULL DEFAULT 0, new_games BIGINT NOT NULL DEFAULT 0,
 changed_games BIGINT NOT NULL DEFAULT 0, duplicate_games BIGINT NOT NULL DEFAULT 0,
 rejected_games BIGINT NOT NULL DEFAULT 0, diagnostics JSONB NOT NULL DEFAULT '[]',
 snapshot_id UUID, cancel_requested BOOLEAN NOT NULL DEFAULT false, source_sha256 TEXT,
 error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
 FOREIGN KEY(owner_id,library_id) REFERENCES study.libraries(owner_id,id),
 FOREIGN KEY(owner_id,snapshot_id) REFERENCES study.library_snapshots(owner_id,id), UNIQUE(owner_id,id)
);
CREATE INDEX study_import_queue ON study.import_jobs(status,created_at);
CREATE TABLE study.import_members (
 owner_id UUID NOT NULL, job_id UUID NOT NULL, game_id TEXT NOT NULL, revision_id UUID NOT NULL,
 PRIMARY KEY(job_id,game_id),
 FOREIGN KEY(owner_id,job_id) REFERENCES study.import_jobs(owner_id,id) ON DELETE CASCADE,
 FOREIGN KEY(owner_id,revision_id,game_id) REFERENCES study.game_revisions(owner_id,id,game_id)
);
CREATE TABLE study.workspace_reviews (
 owner_id UUID NOT NULL, revision_id UUID NOT NULL, ply INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','reviewed','practiced')), note TEXT NOT NULL DEFAULT '',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,revision_id,ply),
 FOREIGN KEY(owner_id,revision_id) REFERENCES study.game_revisions(owner_id,id)
);
CREATE TABLE study.workspace_analysis (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL, revision_id UUID NOT NULL, settings_hash TEXT NOT NULL,
 player TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed')),
 evidence JSONB, error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
 FOREIGN KEY(owner_id,revision_id) REFERENCES study.game_revisions(owner_id,id),
 UNIQUE(owner_id,revision_id,settings_hash,player)
);
CREATE TABLE study.saved_studies (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL, snapshot_id UUID NOT NULL,
 name TEXT NOT NULL, filters JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(owner_id,snapshot_id) REFERENCES study.library_snapshots(owner_id,id)
);
CREATE TABLE study.workspace_validations (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL, snapshot_id UUID NOT NULL, model_run_id TEXT NOT NULL,
 report JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(owner_id,snapshot_id) REFERENCES study.library_snapshots(owner_id,id),
 UNIQUE(owner_id,snapshot_id,model_run_id)
);
CREATE TABLE study.legacy_mappings (
 legacy_import UUID PRIMARY KEY REFERENCES study.imports, owner_id UUID NOT NULL REFERENCES study.accounts,
 snapshot_id UUID NOT NULL REFERENCES study.library_snapshots
);
