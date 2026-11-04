"""Transactional PostgreSQL staging, dimensional loading and publication."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg import sql

TABLES = ("dim_date", "dim_player", "dim_opening", "dim_time_control", "fact_game", "rejected_game")
KEYS = dict(dim_date="date_key", dim_player="player_key", dim_opening="opening_key",
            dim_time_control="time_control_key")


def connect():
    # libpq reads PGHOST/PGDATABASE/PGUSER/PGPASSWORD; never log credentials.
    return psycopg.connect("")


def set_schema(conn, schema):
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))


def initialize(conn, schema):
    conn.execute("""CREATE TABLE IF NOT EXISTS public.pipeline_runs (
        run_id TEXT PRIMARY KEY, engine TEXT NOT NULL, schema_name TEXT NOT NULL,
        status TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ, report JSONB, error TEXT)""")
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    set_schema(conn, schema)
    conn.execute(Path(__file__).with_name("postgres_schema.sql").read_text())


def copy_partition(rows, schema, chunk_size):
    """Bounded COPY batches; record-number conflicts make task retries idempotent."""
    with connect() as conn:
        set_schema(conn, schema)
        conn.execute("CREATE TEMP TABLE incoming (LIKE stage_input INCLUDING DEFAULTS) ON COMMIT PRESERVE ROWS")
        batch = []
        for row in rows:
            batch.append(tuple(row))
            if len(batch) >= chunk_size:
                _copy_batch(conn, batch)
                batch.clear()
        if batch:
            _copy_batch(conn, batch)


def _copy_batch(conn, rows):
    with conn.cursor().copy("COPY incoming(record_number,game_id,models,reason) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
    conn.execute("INSERT INTO stage_input SELECT * FROM incoming ON CONFLICT (record_number) DO NOTHING")
    conn.execute("TRUNCATE incoming")
    conn.commit()


def materialize(conn, month, stamp, expected):
    """Both engines use the same set-based SQL to build the star schema."""
    count = conn.execute("SELECT count(*) FROM stage_input").fetchone()[0]
    if count != expected:
        raise ValueError(f"expected {expected} staged games, found {count}")
    conn.execute("""CREATE UNLOGGED TABLE stage_accepted AS
        SELECT DISTINCT ON (game_id) record_number, game_id, models
        FROM stage_input WHERE reason IS NULL ORDER BY game_id, record_number""")
    for table, key in KEYS.items():
        order = sql.SQL("item->>{}, a.record_number, position").format(sql.Literal(key))
        if table == "dim_player":
            order = sql.SQL("item->>'player_key', item->>'last_observed_date' DESC, a.record_number DESC, position DESC")
        conn.execute(sql.SQL("""INSERT INTO {table}
            SELECT DISTINCT ON (item->>{key}) (jsonb_populate_record(NULL::{table}, item)).*
            FROM stage_accepted a CROSS JOIN LATERAL jsonb_array_elements(a.models->{name}) WITH ORDINALITY AS entry(item, position)
            ORDER BY {order}""").format(table=sql.Identifier(table), key=sql.Literal(key),
                                         name=sql.Literal(table), order=order))
    conn.execute("""INSERT INTO fact_game
        SELECT (jsonb_populate_record(NULL::fact_game, models->'fact_game'->0)).*
        FROM stage_accepted""")
    conn.execute("""INSERT INTO rejected_game
        SELECT record_number, %s, game_id, 'parse_or_validate', reason, %s
        FROM stage_input WHERE reason IS NOT NULL""", (month, stamp))
    counts = {t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
              for t in TABLES}
    accepted, rejected = counts["fact_game"], counts["rejected_game"]
    if accepted == 0:
        raise ValueError("no accepted games")
    conn.execute("ANALYZE fact_game")
    model_bytes = sum(conn.execute("SELECT pg_total_relation_size(%s::regclass)", (t,)).fetchone()[0]
                      for t in TABLES)
    # Immediate FK/check constraints have already validated all loaded facts.
    return dict(input_games=count, accepted_games=accepted, rejected_games=rejected,
                duplicate_games=count - accepted - rejected, tables=counts, model_bytes=model_bytes)


def publish(conn, engine, schema, run_id, report):
    current = f"{engine}_current"
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(current)))
    for table in TABLES:
        conn.execute(sql.SQL("CREATE OR REPLACE VIEW {}.{} AS SELECT * FROM {}.{}").format(
            sql.Identifier(current), sql.Identifier(table), sql.Identifier(schema), sql.Identifier(table)))
    conn.execute("""UPDATE public.pipeline_runs SET status='complete',finished_at=now(),report=%s
                    WHERE run_id=%s""", (json.dumps(report), run_id))
