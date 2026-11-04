"""Run with Docker Compose: real PostgreSQL and local Spark, no external cloud."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import zstandard as zstd

pytest.importorskip('psycopg')
from psycopg import sql
from chess_lakehouse import warehouse
from chess_lakehouse.comparison import compare
from chess_lakehouse.container_cli import run_engine
from chess_lakehouse.pipeline import sha256_file

pytestmark = pytest.mark.skipif(not os.environ.get('PGHOST'), reason='use docker compose run --rm tests')


@pytest.fixture(scope='session', autouse=True)
def isolated_postgres_database():
    """Tests cannot replace the real month's current views or benchmark ledger."""
    if not os.environ.get('PGHOST'):
        yield
        return
    from uuid import uuid4
    previous = os.environ.get('PGDATABASE', 'chess')
    database = 'chess_test_' + uuid4().hex[:12]
    admin = warehouse.connect()
    admin.autocommit = True
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    os.environ['PGDATABASE'] = database
    try:
        yield
    finally:
        os.environ['PGDATABASE'] = previous
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
        admin.close()


@pytest.fixture
def input_config(tmp_path):
    archive = tmp_path / 'fixture.zst'
    archive.write_bytes(zstd.ZstdCompressor().compress((Path(__file__).parent / 'fixtures' / 'games.pgn').read_bytes()))
    return archive, dict(archive_sha256=sha256_file(archive), expected_games=5)


def test_container_engines_postgres_equivalence_and_failure_recovery(input_config, tmp_path):
    archive, config = input_config
    reports = tmp_path / 'reports'
    with patch('chess_lakehouse.container_cli.archive_path', return_value=(archive, 'fixture')):
        first = run_engine('python', config, '2013-01', 2, 2, reports)
        # Rerun checks current-view replacement without duplicate facts.
        second = run_engine('python', config, '2013-01', 2, 2, reports)
        spark = run_engine('spark', config, '2013-01', 2, 2, reports)
        for report in (first, second, spark):
            assert (report['input_games'], report['accepted_games'], report['rejected_games'], report['duplicate_games']) == (5, 2, 2, 1)
        result = compare(reports)
        assert all(result['equality'].values())
        assert (reports / 'comparison.json').exists()
        with patch('chess_lakehouse.warehouse.materialize', side_effect=ValueError('injected modeling failure')):
            with pytest.raises(ValueError, match='injected'):
                run_engine('python', config, '2013-01', 2, 2, reports)
    with warehouse.connect() as conn:
        assert conn.execute('SELECT count(*) FROM python_current.fact_game').fetchone()[0] == 2
        # Actual FK enforcement, not just a metadata assertion.
        with pytest.raises(__import__("psycopg").errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute("DELETE FROM python_current.dim_player")
        stage = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=%s AND table_name='stage_input'", (second['schema'],)).fetchone()[0]
        assert stage == 0


def test_postgres_copy_task_retry_is_idempotent(tmp_path):
    from uuid import uuid4
    schema = 'test_' + uuid4().hex[:16]
    with warehouse.connect() as conn:
        warehouse.initialize(conn, schema)
    try:
        rows = [(1, None, None, 'invalid game'), (2, None, None, 'missing ID')]
        warehouse.copy_partition(iter(rows), schema, 1)
        warehouse.copy_partition(iter(rows), schema, 1)
        with warehouse.connect() as conn:
            warehouse.set_schema(conn, schema)
            assert conn.execute('SELECT count(*) FROM stage_input').fetchone()[0] == 2
    finally:
        with warehouse.connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_report_file_failure_preserves_committed_database(input_config, tmp_path):
    archive, config = input_config
    with patch('chess_lakehouse.container_cli.archive_path', return_value=(archive, 'fixture')):
        with patch('pathlib.Path.write_text', side_effect=OSError('disk full')):
            with pytest.raises(OSError, match='disk full'):
                run_engine('python', config, '2013-01', 2, 2, tmp_path / 'reports')
    with warehouse.connect() as conn:
        assert conn.execute('SELECT count(*) FROM python_current.fact_game').fetchone()[0] == 2
        latest = conn.execute("SELECT status FROM public.pipeline_runs WHERE engine='python' ORDER BY started_at DESC LIMIT 1").fetchone()[0]
        assert latest == 'complete'


def test_same_engine_advisory_lock_blocks_second_writer(input_config, tmp_path):
    archive, config = input_config
    with warehouse.connect() as owner:
        owner.execute("SELECT pg_advisory_lock(hashtext('chess-python'))")
        with patch('chess_lakehouse.container_cli.archive_path', return_value=(archive, 'fixture')):
            with pytest.raises(ValueError, match='another python pipeline is active'):
                run_engine('python', config, '2013-01', 2, 2, tmp_path / 'reports')
