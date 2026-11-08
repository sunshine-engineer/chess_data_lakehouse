"""Logged shard checkpoints and atomic, partition-scoped historical publication."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
import hashlib
import json
import os

import psycopg
from psycopg import sql
import pyarrow as pa
import pyarrow.parquet as pq

from .records import COLUMNS, SCHEMA
from .settings import ENGINES
from .storage import verify_file
from ..runtime import postgres_options


def connect(settings=None):
    """Open the loader connection and optionally apply job-specific SQL timeout limits."""
    conn = psycopg.connect("", connect_timeout=10, application_name="chess-historical", **postgres_options())
    if settings:
        conn.execute("SELECT set_config('statement_timeout',%s,false),set_config('lock_timeout',%s,false)",
                     (str(settings.statement_timeout_seconds * 1000), str(settings.lock_timeout_seconds * 1000)))
        conn.commit()
    return conn


def schema_for(engine):
    """Resolve an allowlisted engine to its historical warehouse schema."""
    if engine not in ENGINES:
        raise ValueError("unknown engine")
    return "warehouse_" + engine


def stage_for(run_id):
    """Validate a run identifier before quoting its private staging table name."""
    if not run_id.startswith(("python_", "python_mp_", "spark_")) or not run_id.replace("_", "").isalnum():
        raise ValueError("invalid run identifier")
    return sql.Identifier("staging", run_id)


def create_run(conn, run_id, engine, settings, provenance, group):
    """Register a historical run and create its typed logged staging relation."""
    types = {pa.int64(): "BIGINT", pa.string(): "TEXT", pa.binary(): "BYTEA", pa.bool_(): "BOOLEAN",
             pa.date32(): "DATE", pa.float64(): "DOUBLE PRECISION"}
    definitions = sql.SQL(",").join(sql.SQL("{} {}").format(sql.Identifier(f.name), sql.SQL(types[f.type])) for f in SCHEMA)
    conn.execute(sql.SQL("CREATE TABLE {} ({}, PRIMARY KEY(record_number))").format(stage_for(run_id), definitions))
    conn.execute("""INSERT INTO control.pipeline_runs
        (run_id,engine,source_month,source_sha256,benchmark_group,config_hash,code_hash,image_id,settings,runtime,status)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')""",
        (run_id, engine, settings.source.month + "-01", settings.source.sha256, group,
         settings.fingerprint(), provenance["code_hash"], provenance["image_id"],
         json.dumps(settings.snapshot()), json.dumps(provenance)))


def shard_checksum(shard):
    """Derive the immutable checksum binding a transformed shard's committed files."""
    return hashlib.sha256(json.dumps(shard["files"], sort_keys=True).encode()).hexdigest()


def load_shard(run_id, shard, settings):
    """Load one verified shard and its database checkpoint in the supported staging contract."""
    for entry in shard["files"]:
        verify_file(entry, SCHEMA)
    checksum = shard_checksum(shard)
    expected = sum(entry["rows"] for entry in shard["files"])
    with connect(settings) as conn:
        prior = conn.execute("SELECT checksum,row_count FROM control.run_shards WHERE run_id=%s AND shard_id=%s",
                             (run_id, shard["shard_id"])).fetchone()
        if prior:
            if prior != (checksum, expected):
                raise ValueError("loaded shard differs from durable checkpoint")
            return dict(shard_id=shard["shard_id"], reused=True, rows=expected)
        conn.execute(sql.SQL("CREATE TEMP TABLE incoming (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP").format(stage_for(run_id)))
        for entry in shard["files"]:
            for batch in pq.ParquetFile(entry["path"]).iter_batches(batch_size=settings.copy_batch_rows):
                with conn.cursor().copy(sql.SQL("COPY incoming ({}) FROM STDIN").format(
                        sql.SQL(",").join(map(sql.Identifier, COLUMNS)))) as copy:
                    for row in batch.to_pylist():
                        copy.write_row(tuple(row[name] for name in COLUMNS))
        conn.execute(sql.SQL("INSERT INTO {} SELECT * FROM incoming ON CONFLICT(record_number) DO NOTHING").format(stage_for(run_id)))
        conn.execute("""INSERT INTO control.run_shards(run_id,shard_id,checksum,row_count)
                        VALUES(%s,%s,%s,%s) ON CONFLICT(run_id,shard_id) DO NOTHING""",
                     (run_id, shard["shard_id"], checksum, expected))
        conn.execute("UPDATE control.pipeline_runs SET heartbeat_at=now() WHERE run_id=%s", (run_id,))
        return dict(shard_id=shard["shard_id"], reused=False, rows=expected)


def load_shards(run_id, manifest, settings, on_shard=None):
    # Bound both open connections and outstanding work; interruption drains at most N shards.
    """Load transformed shards with explicitly bounded database writer concurrency."""
    with ThreadPoolExecutor(max_workers=settings.db_writers) as pool:
        entries = iter(manifest["shards"])
        active = set()
        while True:
            while len(active) < settings.db_writers:
                shard = next(entries, None)
                if shard is None:
                    break
                active.add(pool.submit(load_shard, run_id, shard, settings))
            if not active:
                break
            done, active = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                result = future.result()
                if on_shard:
                    on_shard(result)


def publish(conn, run_id, engine, settings):
    """Called inside one transaction, including quality gates and ledger completion."""
    schema = schema_for(engine)
    month = date.fromisoformat(settings.source.month + "-01")
    conn.execute(sql.SQL("SET LOCAL search_path TO {},pg_temp,control").format(sql.Identifier(schema)))
    count, rejects, unknowns = conn.execute(sql.SQL("""SELECT count(*),
        count(*) FILTER(WHERE reason IS NOT NULL),
        count(*) FILTER(WHERE reason IS NULL AND (white_unknown OR black_unknown)) FROM {}""").format(stage_for(run_id))).fetchone()
    if count != settings.source.expected_games:
        raise ValueError(f"expected {settings.source.expected_games} staged records, got {count}")
    if rejects / count > settings.max_reject_ratio:
        raise ValueError(f"rejection ratio {rejects/count:.6f} exceeds gate {settings.max_reject_ratio}")
    if unknowns / count > settings.max_unknown_player_ratio:
        raise ValueError(f"unknown-player ratio {unknowns/count:.6f} exceeds gate {settings.max_unknown_player_ratio}")
    conn.execute("SELECT control.ensure_partition(%s,%s)", (engine, month))
    conn.execute(sql.SQL("""CREATE TEMP TABLE accepted ON COMMIT DROP AS
        SELECT DISTINCT ON(game_id) * FROM {} WHERE reason IS NULL ORDER BY game_id,record_number""").format(stage_for(run_id)))
    conn.execute("CREATE TEMP TABLE affected_players(player_sk BIGINT PRIMARY KEY) ON COMMIT DROP")
    conn.execute("""INSERT INTO affected_players
        SELECT white_player_sk FROM fact_game WHERE source_month=%s
        UNION SELECT black_player_sk FROM fact_game WHERE source_month=%s""", (month, month))
    conn.execute("DELETE FROM fact_game WHERE source_month=%s", (month,))
    conn.execute("DELETE FROM game_identity WHERE source_month=%s", (month,))
    conn.execute("""INSERT INTO game_identity SELECT game_id,%s FROM accepted
                    ON CONFLICT(game_id) DO NOTHING""", (month,))
    conn.execute("""DELETE FROM accepted a USING game_identity g
                    WHERE a.game_id=g.game_id AND g.source_month<>%s""", (month,))
    accepted = conn.execute("SELECT count(*) FROM accepted").fetchone()[0]
    if count == rejects:
        raise ValueError("no valid games")
    conn.execute("""INSERT INTO dim_date SELECT DISTINCT game_date,extract(year FROM game_date)::int,
        extract(month FROM game_date)::int,extract(quarter FROM game_date)::int,
        extract(isodow FROM game_date)::int-1 FROM accepted ON CONFLICT DO NOTHING""")
    conn.execute("""INSERT INTO dim_player(player_hash,is_unknown)
        SELECT DISTINCT player_hash,is_unknown FROM (
            SELECT white_player_hash AS player_hash,white_unknown AS is_unknown FROM accepted
            UNION ALL SELECT black_player_hash,black_unknown FROM accepted) p ORDER BY player_hash
        ON CONFLICT(player_hash) DO NOTHING""")
    conn.execute("""INSERT INTO dim_opening(opening_hash,eco,family,variation)
        SELECT DISTINCT ON(opening_hash) opening_hash,eco,opening_family,opening_variation
        FROM accepted ORDER BY opening_hash,record_number ON CONFLICT(opening_hash) DO NOTHING""")
    conn.execute("""INSERT INTO dim_time_control(control_code,initial_seconds,increment_seconds,speed)
        SELECT DISTINCT ON(time_control_code) time_control_code,initial_seconds,increment_seconds,speed
        FROM accepted ORDER BY time_control_code,record_number ON CONFLICT(control_code) DO NOTHING""")
    conn.execute("""INSERT INTO fact_game(source_month,game_id,date_key,white_player_sk,black_player_sk,
        opening_sk,time_control_sk,white_title_as_played,black_title_as_played,white_rating,black_rating,
        average_rating,rating_difference,white_rating_band,black_rating_band,result,winner_color,
        ply_count,move_count,termination,speed,source_record_number,run_id)
        SELECT %s,a.game_id,a.game_date,w.player_sk,b.player_sk,o.opening_sk,t.time_control_sk,
        a.white_title,a.black_title,a.white_rating,a.black_rating,a.average_rating,a.rating_difference,
        a.white_rating_band,a.black_rating_band,a.result,a.winner_color,a.ply_count,a.move_count,
        a.termination,a.speed,a.record_number,%s FROM accepted a
        JOIN dim_player w ON w.player_hash=a.white_player_hash JOIN dim_player b ON b.player_hash=a.black_player_hash
        JOIN dim_opening o ON o.opening_hash=a.opening_hash JOIN dim_time_control t ON t.control_code=a.time_control_code""", (month, run_id))
    conn.execute("""INSERT INTO affected_players SELECT white_player_sk FROM fact_game WHERE source_month=%s
        UNION SELECT black_player_sk FROM fact_game WHERE source_month=%s ON CONFLICT DO NOTHING""", (month, month))
    # Re-evaluate only affected players, using indexed role/date lookups across retained months.
    conn.execute("""UPDATE dim_player p SET latest_title=CASE WHEN p.is_unknown THEN NULL ELSE q.title END,
        last_observed_date=q.date_key FROM (
          SELECT a.player_sk,v.title,v.date_key FROM affected_players a LEFT JOIN LATERAL (
            SELECT title,date_key FROM (
              SELECT white_title_as_played AS title,date_key,source_month,source_record_number,1 AS color,game_id
              FROM fact_game WHERE white_player_sk=a.player_sk
              UNION ALL
              SELECT black_title_as_played,date_key,source_month,source_record_number,2,game_id
              FROM fact_game WHERE black_player_sk=a.player_sk
            ) observations ORDER BY date_key DESC,source_month DESC,source_record_number DESC,color DESC,game_id DESC LIMIT 1
          ) v ON true
        ) q WHERE p.player_sk=q.player_sk""")
    conn.execute("DELETE FROM rejected_game WHERE source_month=%s", (month,))
    conn.execute(sql.SQL("""INSERT INTO rejected_game(source_month,record_number,game_id,reason,run_id)
        SELECT %s,record_number,game_id,reason,%s FROM {} WHERE reason IS NOT NULL""").format(stage_for(run_id)), (month, run_id))
    conn.execute("""INSERT INTO control.month_publications(engine,source_month,run_id,source_sha256,accepted_games)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(engine,source_month) DO UPDATE SET
        run_id=excluded.run_id,source_sha256=excluded.source_sha256,accepted_games=excluded.accepted_games,published_at=now()""",
        (engine, month, run_id, settings.source.sha256, accepted))
    # Update stats on the loaded partition, not every year of historical facts.
    conn.execute("SELECT control.analyze_partition(%s,%s)", (engine, month))
    model_bytes = conn.execute("""SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind='r'""", (schema,)).fetchone()[0]
    conn.execute("SELECT control.drop_stage(%s)", (run_id,))
    conn.execute("SELECT control.refresh_compatibility(%s)", (engine,))
    return dict(input_games=count, accepted_games=accepted, rejected_games=rejects,
                duplicate_games=count-accepted-rejects, unknown_player_games=unknowns,
                total_history_games=int(conn.execute("SELECT sum(accepted_games) FROM control.month_publications WHERE engine=%s", (engine,)).fetchone()[0]),
                model_bytes=int(model_bytes or 0))
