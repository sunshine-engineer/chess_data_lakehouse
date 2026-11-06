-- One position index per shared revision, never per collection snapshot.
ALTER TABLE study.game_revisions ADD COLUMN positions_ready BOOLEAN NOT NULL DEFAULT false;
CREATE TABLE study.workspace_positions (
 owner_id UUID NOT NULL, revision_id UUID NOT NULL, ply INTEGER NOT NULL,
 position_hash BYTEA NOT NULL CHECK(octet_length(position_hash)=32), next_uci TEXT, next_san TEXT,
 PRIMARY KEY(revision_id,ply),
 FOREIGN KEY(owner_id,revision_id) REFERENCES study.game_revisions(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX study_workspace_position_lookup ON study.workspace_positions(owner_id,position_hash,revision_id);
