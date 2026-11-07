-- Additive private practice state; imported games and existing notes stay immutable.
CREATE TABLE study.board_preferences (
 owner_id UUID PRIMARY KEY REFERENCES study.accounts(id) ON DELETE CASCADE,
 preferences JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE study.board_variations (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL, revision_id UUID NOT NULL,
 ply INTEGER NOT NULL CHECK(ply>=0), moves JSONB NOT NULL,
 label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 80), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(owner_id,revision_id) REFERENCES study.game_revisions(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX board_variations_owner ON study.board_variations(owner_id,revision_id);
CREATE TABLE study.position_analysis (
 id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES study.accounts(id) ON DELETE CASCADE,
 fen TEXT NOT NULL, settings_hash TEXT NOT NULL, profile TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed')),
 evidence JSONB NOT NULL DEFAULT '{}', error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
 UNIQUE(owner_id,fen,settings_hash)
);
CREATE INDEX position_analysis_queue ON study.position_analysis(created_at) WHERE status='queued';
CREATE TABLE study.lesson_attempts (
 owner_id UUID NOT NULL REFERENCES study.accounts(id) ON DELETE CASCADE,
 attempt_id UUID NOT NULL, lesson_id TEXT NOT NULL, challenge_hash TEXT NOT NULL,
 move TEXT NOT NULL, correct BOOLEAN NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,attempt_id)
);
CREATE INDEX lesson_attempts_progress ON study.lesson_attempts(owner_id,lesson_id,created_at);
-- Small public teaching sample, separate from full warehouse statistics.
CREATE SCHEMA academy;
REVOKE ALL ON SCHEMA academy FROM PUBLIC;
CREATE TABLE academy.datasets (
 source_month DATE PRIMARY KEY, source_sha256 TEXT NOT NULL,
 input_games BIGINT NOT NULL, sample_games INTEGER NOT NULL,
 method TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE academy.examples (
 source_month DATE NOT NULL REFERENCES academy.datasets(source_month) ON DELETE CASCADE,
 game_id TEXT NOT NULL, payload JSONB NOT NULL,
 PRIMARY KEY(source_month,game_id)
);
GRANT USAGE ON SCHEMA academy TO chess_loader,chess_reader;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA academy TO chess_loader;
GRANT SELECT ON ALL TABLES IN SCHEMA academy TO chess_reader;
