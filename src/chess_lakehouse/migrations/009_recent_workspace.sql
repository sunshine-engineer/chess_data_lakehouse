-- Auxiliary chronology keeps immutable game revisions intact.
CREATE TABLE study.game_times (
 owner_id UUID NOT NULL REFERENCES study.accounts(id),game_id TEXT NOT NULL,
 started_at TIMESTAMPTZ NOT NULL,source TEXT NOT NULL CHECK(source IN ('lichess','pgn_utc')),
 PRIMARY KEY(owner_id,game_id)
);
CREATE INDEX game_times_recent ON study.game_times(owner_id,started_at DESC,game_id);
CREATE VIEW study.workspace_game_details AS
 SELECT f.*,t.started_at,t.source AS time_source,
 coalesce(t.started_at,f.game_date::timestamp AT TIME ZONE 'UTC') AS sort_at
 FROM study.workspace_game_facts f LEFT JOIN study.game_times t USING(owner_id,game_id);
ALTER TABLE study.saved_studies ADD COLUMN game_ids TEXT[];
ALTER TABLE study.saved_studies ADD CHECK(game_ids IS NULL OR cardinality(game_ids)<=200);
ALTER TABLE study.import_jobs ADD COLUMN skip_counts JSONB NOT NULL DEFAULT '{}';
ALTER TABLE study.import_jobs ADD COLUMN pending_games BIGINT NOT NULL DEFAULT 0;
