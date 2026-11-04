"""Versioned administrative DDL and narrowly privileged loader/reader roles."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg
from psycopg import sql

from .settings import ENGINES


def admin_connect():
    return psycopg.connect(host=os.environ.get("PGHOST", "postgres"), port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "chess"), user=os.environ.get("PGADMIN_USER", os.environ.get("PGUSER", "chess")),
        password=os.environ.get("PGADMIN_PASSWORD", os.environ.get("PGPASSWORD")), connect_timeout=10)


WAREHOUSE_DDL = """
CREATE TABLE {s}.dim_date (
 date_key DATE PRIMARY KEY, year SMALLINT NOT NULL, month SMALLINT NOT NULL CHECK(month BETWEEN 1 AND 12),
 quarter SMALLINT NOT NULL CHECK(quarter BETWEEN 1 AND 4), weekday SMALLINT NOT NULL CHECK(weekday BETWEEN 0 AND 6));
CREATE TABLE {s}.dim_player (
 player_sk BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 player_hash BYTEA UNIQUE NOT NULL CHECK(octet_length(player_hash)=32),
 is_unknown BOOLEAN NOT NULL, latest_title TEXT, last_observed_date DATE);
CREATE TABLE {s}.dim_opening (
 opening_sk BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 opening_hash BYTEA UNIQUE NOT NULL CHECK(octet_length(opening_hash)=32), eco TEXT, family TEXT, variation TEXT);
CREATE TABLE {s}.dim_time_control (
 time_control_sk BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, control_code TEXT UNIQUE NOT NULL,
 initial_seconds BIGINT CHECK(initial_seconds >= 0), increment_seconds BIGINT CHECK(increment_seconds >= 0), speed TEXT NOT NULL);
CREATE TABLE {s}.game_identity (
 game_id TEXT PRIMARY KEY, source_month DATE NOT NULL, UNIQUE(game_id,source_month));
CREATE TABLE {s}.fact_game (
 source_month DATE NOT NULL, game_id TEXT NOT NULL, date_key DATE NOT NULL REFERENCES {s}.dim_date,
 white_player_sk BIGINT NOT NULL REFERENCES {s}.dim_player,
 black_player_sk BIGINT NOT NULL REFERENCES {s}.dim_player,
 opening_sk BIGINT NOT NULL REFERENCES {s}.dim_opening,
 time_control_sk BIGINT NOT NULL REFERENCES {s}.dim_time_control,
 white_title_as_played TEXT, black_title_as_played TEXT,
 white_rating INTEGER CHECK(white_rating BETWEEN 0 AND 4000), black_rating INTEGER CHECK(black_rating BETWEEN 0 AND 4000),
 average_rating DOUBLE PRECISION, rating_difference INTEGER, white_rating_band TEXT, black_rating_band TEXT,
 result TEXT NOT NULL CHECK(result IN ('1-0','0-1','1/2-1/2')),
 winner_color TEXT NOT NULL CHECK(winner_color IN ('white','black','draw')),
 ply_count INTEGER NOT NULL CHECK(ply_count >= 0), move_count INTEGER NOT NULL CHECK(move_count >= 0),
 termination TEXT, speed TEXT NOT NULL,
 source_record_number BIGINT NOT NULL, run_id TEXT NOT NULL REFERENCES control.pipeline_runs,
 ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(source_month,game_id),
 FOREIGN KEY(game_id,source_month) REFERENCES {s}.game_identity(game_id,source_month)
) PARTITION BY RANGE(source_month);
CREATE INDEX ON {s}.fact_game(date_key);
CREATE INDEX ON {s}.fact_game(white_player_sk,date_key DESC);
CREATE INDEX ON {s}.fact_game(black_player_sk,date_key DESC);
CREATE TABLE {s}.rejected_game (
 source_month DATE NOT NULL, record_number BIGINT NOT NULL, game_id TEXT, reason TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES control.pipeline_runs, ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(source_month,record_number));
CREATE VIEW {s}.semantic_fact AS
 SELECT f.source_month,f.game_id,f.date_key,encode(w.player_hash,'hex') AS white_player_key,
 encode(b.player_hash,'hex') AS black_player_key,encode(o.opening_hash,'hex') AS opening_key,
 t.control_code AS time_control_key,f.white_title_as_played,f.black_title_as_played,
 f.white_rating,f.black_rating,f.average_rating,f.rating_difference,f.white_rating_band,f.black_rating_band,
 f.result,f.winner_color,f.ply_count,f.move_count,f.termination,f.speed,f.source_record_number
 FROM {s}.fact_game f JOIN {s}.dim_player w ON w.player_sk=f.white_player_sk
 JOIN {s}.dim_player b ON b.player_sk=f.black_player_sk
 JOIN {s}.dim_opening o ON o.opening_sk=f.opening_sk
 JOIN {s}.dim_time_control t ON t.time_control_sk=f.time_control_sk;
"""


def migrate(conn=None, loader="chess_loader", reader="chess_reader"):
    own = conn is None
    conn = conn or admin_connect()
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('chess-schema-migration'))")
            for role, password_name in ((loader, "LOADER_PASSWORD"), (reader, "READER_PASSWORD")):
                password = os.environ.get(password_name)
                if not password:
                    raise ValueError(f"{password_name} is required for role bootstrap")
                if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                    conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                else:
                    # Only privileged migrations rotate credentials; runtime jobs never receive them.
                    conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            script = Path(__file__).parents[1] / "migrations" / "001_history.sql"
            checksum = hashlib.sha256(script.read_bytes() + WAREHOUSE_DDL.encode()).hexdigest()
            conn.execute("CREATE SCHEMA IF NOT EXISTS control")
            exists = conn.execute("SELECT to_regclass('control.schema_migrations')").fetchone()[0]
            applied = conn.execute("SELECT checksum FROM control.schema_migrations WHERE version=1").fetchone() if exists else None
            if applied and applied[0] != checksum:
                raise ValueError("migration checksum changed; add a new migration rather than editing applied DDL")
            if not applied:
                conn.execute(script.read_text())
                for engine in ENGINES:
                    schema = "warehouse_" + engine
                    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                    conn.execute(sql.SQL(WAREHOUSE_DDL).format(s=sql.Identifier(schema)))
                conn.execute("INSERT INTO control.schema_migrations(version,checksum) VALUES(1,%s)", (checksum,))
            conn.execute("""CREATE OR REPLACE FUNCTION control.ensure_partition(engine TEXT, month DATE)
                RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
                DECLARE target TEXT; partition_name TEXT;
                BEGIN
                  IF engine NOT IN ('python','python_mp','spark') OR month <> date_trunc('month',month)::date
                     OR month < DATE '2000-01-01' OR month > DATE '2100-12-01' THEN
                    RAISE EXCEPTION 'invalid engine or partition month';
                  END IF;
                  target := 'warehouse_' || engine;
                  partition_name := 'fact_game_' || to_char(month,'YYYY_MM');
                  PERFORM pg_advisory_xact_lock(hashtext(target || partition_name));
                  EXECUTE format('CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.fact_game FOR VALUES FROM (%L) TO (%L)',
                      target,partition_name,target,month,(month+INTERVAL '1 month')::date);
                END $$""")
            conn.execute("REVOKE ALL ON FUNCTION control.ensure_partition(TEXT,DATE) FROM PUBLIC")
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION control.ensure_partition(TEXT,DATE) TO {}").format(sql.Identifier(loader)))
            conn.execute("""CREATE OR REPLACE FUNCTION control.analyze_partition(engine TEXT, month DATE)
                RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
                BEGIN
                  IF engine NOT IN ('python','python_mp','spark') OR month <> date_trunc('month',month)::date THEN
                    RAISE EXCEPTION 'invalid engine or partition month';
                  END IF;
                  EXECUTE format('ANALYZE %I.%I', 'warehouse_' || engine, 'fact_game_' || to_char(month,'YYYY_MM'));
                END $$""")
            conn.execute("REVOKE ALL ON FUNCTION control.analyze_partition(TEXT,DATE) FROM PUBLIC")
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION control.analyze_partition(TEXT,DATE) TO {}").format(sql.Identifier(loader)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA control,staging TO {}").format(sql.Identifier(loader)))
            conn.execute(sql.SQL("GRANT CREATE ON SCHEMA staging TO {}").format(sql.Identifier(loader)))
            conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA control TO {}").format(sql.Identifier(loader)))
            # Applied migrations are immutable metadata, not writable by the loader.
            conn.execute(sql.SQL("REVOKE INSERT,UPDATE,DELETE ON control.schema_migrations FROM {}").format(sql.Identifier(loader)))
            for engine in ENGINES:
                schema = sql.Identifier("warehouse_" + engine)
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {},{}").format(schema, sql.Identifier(loader), sql.Identifier(reader)))
                conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(schema, sql.Identifier(loader)))
                conn.execute(sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(schema, sql.Identifier(loader)))
                conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema, sql.Identifier(reader)))
                conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(schema, sql.Identifier(reader)))
        conn.commit()
    finally:
        if own:
            conn.close()
