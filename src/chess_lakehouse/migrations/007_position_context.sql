-- Repetition-sensitive search needs the played/trial history as well as FEN.
-- Existing completed results remain historical evidence; new cache keys include it.
ALTER TABLE study.position_analysis ADD COLUMN context JSONB NOT NULL DEFAULT '{}';
