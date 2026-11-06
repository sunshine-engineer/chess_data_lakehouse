CREATE SCHEMA IF NOT EXISTS study;
CREATE TABLE study.workspaces (
 id UUID PRIMARY KEY, player TEXT NOT NULL, player_normalized TEXT NOT NULL,
 month DATE NOT NULL CHECK(month=date_trunc('month',month)::date),
 active_import UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(player_normalized,month)
);
CREATE TABLE study.imports (
 id UUID PRIMARY KEY, workspace_id UUID NOT NULL REFERENCES study.workspaces,
 version INTEGER NOT NULL CHECK(version>0), parent_id UUID REFERENCES study.imports,
 source_sha256 TEXT NOT NULL, dataset_sha256 TEXT NOT NULL,
 game_count INTEGER NOT NULL CHECK(game_count BETWEEN 1 AND 500),
 ply_count INTEGER NOT NULL CHECK(ply_count BETWEEN 0 AND 100000),
 artifact_path TEXT NOT NULL, manifest JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,version)
);
ALTER TABLE study.workspaces ADD FOREIGN KEY(active_import) REFERENCES study.imports(id);
CREATE TABLE study.games (
 import_id UUID NOT NULL REFERENCES study.imports, game_id TEXT NOT NULL,
 game_date DATE NOT NULL, player_color TEXT NOT NULL CHECK(player_color IN ('white','black')),
 player_rating INTEGER CHECK(player_rating BETWEEN 0 AND 4000),
 opponent_rating INTEGER CHECK(opponent_rating BETWEEN 0 AND 4000),
 opponent_band TEXT NOT NULL CHECK(opponent_band IN ('weaker','similar','stronger','unknown')),
 result TEXT NOT NULL CHECK(result IN ('win','draw','loss')),
 points DOUBLE PRECISION NOT NULL CHECK(points IN (0,0.5,1)),
 speed TEXT NOT NULL, eco TEXT, opening TEXT NOT NULL, ply_count INTEGER NOT NULL,
 payload_index INTEGER NOT NULL, PRIMARY KEY(import_id,game_id)
);
CREATE INDEX study_game_filters ON study.games(import_id,player_color,speed,opponent_band);
CREATE TABLE study.positions (
 import_id UUID NOT NULL, game_id TEXT NOT NULL, ply SMALLINT NOT NULL,
 position_hash BYTEA NOT NULL CHECK(octet_length(position_hash)=32),
 next_uci TEXT, next_san TEXT,
 PRIMARY KEY(import_id,game_id,ply), FOREIGN KEY(import_id,game_id) REFERENCES study.games
);
CREATE INDEX study_position_lookup ON study.positions(import_id,position_hash);
CREATE TABLE study.cohorts (
 id UUID PRIMARY KEY, import_id UUID NOT NULL REFERENCES study.imports,
 name TEXT NOT NULL, filters JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE study.review_items (
 import_id UUID NOT NULL, game_id TEXT NOT NULL, ply INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL CHECK(status IN ('queued','reviewed','practiced')),
 note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=2000), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(import_id,game_id,ply), FOREIGN KEY(import_id,game_id) REFERENCES study.games
);
CREATE TABLE study.analysis_jobs (
 id UUID PRIMARY KEY, import_id UUID NOT NULL, game_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed')),
 settings_hash TEXT NOT NULL, evidence JSONB, error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
 FOREIGN KEY(import_id,game_id) REFERENCES study.games,
 UNIQUE(import_id,game_id,settings_hash)
);
CREATE TABLE study.validations (
 id UUID PRIMARY KEY, import_id UUID NOT NULL REFERENCES study.imports,
 model_run_id TEXT NOT NULL, report JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(import_id,model_run_id)
);
CREATE OR REPLACE FUNCTION study.protect_published() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'published study data is immutable'; END $$;
CREATE TRIGGER study_import_immutable BEFORE UPDATE OR DELETE ON study.imports
 FOR EACH ROW EXECUTE FUNCTION study.protect_published();
CREATE TRIGGER study_game_immutable BEFORE UPDATE OR DELETE ON study.games
 FOR EACH ROW EXECUTE FUNCTION study.protect_published();
CREATE TRIGGER study_position_immutable BEFORE UPDATE OR DELETE ON study.positions
 FOR EACH ROW EXECUTE FUNCTION study.protect_published();
