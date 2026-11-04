"""Real historical PostgreSQL, Python multiprocessing and Spark integration."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import json
import os
from uuid import uuid4

import pytest
pytest.importorskip('psycopg')
import psycopg
from psycopg import sql

from chess_lakehouse.optimized.migrate import admin_connect, migrate
from chess_lakehouse.optimized.workflow import run
from chess_lakehouse.optimized import warehouse
from chess_lakehouse.optimized.storage import iter_games
from test_v2_storage import source_settings

pytestmark = pytest.mark.skipif(not os.environ.get('PGHOST'), reason='run Docker Compose tests')


@pytest.fixture
def database(monkeypatch):
    admin = admin_connect()
    admin.autocommit = True
    name = 'chess_v2_test_' + uuid4().hex[:12]
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with monkeypatch.context() as env:
            env.setenv('PGDATABASE', name)
            migrate()
            env.setenv('PGUSER', 'chess_loader')
            env.setenv('PGPASSWORD', os.environ['LOADER_PASSWORD'])
            yield name
    finally:
        admin.execute(sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(name)))
        admin.close()


@pytest.mark.parametrize('engine', ['python', 'python_mp', 'spark'])
def test_engine_typed_parquet_and_historical_model(database, tmp_path, engine):
    archive, settings = source_settings(tmp_path)
    report = run(engine, settings, tmp_path / 'reports', archive=archive)
    assert (report['input_games'], report['accepted_games'], report['rejected_games'], report['duplicate_games']) == (5,2,2,1)
    with warehouse.connect() as conn:
        schema = warehouse.schema_for(engine)
        assert conn.execute(sql.SQL('SELECT count(*) FROM {}.fact_game').format(sql.Identifier(schema))).fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='f' AND convalidated", (schema+'.fact_game',)).fetchone()[0] == 7
        assert conn.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='dim_player' AND column_name='player_sk'", (schema,)).fetchone()[0] == 'bigint'
        assert conn.execute('SELECT count(*) FROM control.run_shards WHERE run_id=%s', (report['run_id'],)).fetchone()[0] > 1
        assert conn.execute('SELECT to_regclass(%s)', ('staging.'+report['run_id'],)).fetchone()[0] is None
    manifest = json.loads((settings.data_root/'runs-v2'/report['run_id']/'silver'/'manifest.json').read_text())
    assert manifest['complete'] and all(s['files'] for s in manifest['shards'])


def test_history_rerun_corrections_and_global_identity(database, tmp_path):
    archive, settings = source_settings(tmp_path)
    first = run('python', settings, tmp_path/'reports', archive=archive)
    game = next(iter_games(archive, 8192))
    feb = game.replace('test0001', 'test0005').replace('2012.12.31','2013.02.15').replace('[WhiteElo', '[WhiteTitle "GM"]\n[WhiteElo')
    b_archive,b_settings = source_settings(tmp_path, (feb+'\n'+game).encode(), month='2013-02', expected=2)
    second = run('python', b_settings, tmp_path/'reports', archive=b_archive)
    assert second['accepted_games'] == 1 and second['duplicate_games'] == 1
    again = run('python', settings, tmp_path/'reports', archive=archive)
    assert again['total_history_games'] == 3
    with warehouse.connect() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_python.game_identity').fetchone()[0] == 3
        assert conn.execute("SELECT latest_title FROM warehouse_python.dim_player WHERE player_hash=decode(%s,'hex')", (__import__('hashlib').sha256(b'alice').hexdigest(),)).fetchone()[0] == 'GM'
        plan = conn.execute("EXPLAIN(FORMAT JSON,COSTS OFF) SELECT * FROM warehouse_python.fact_game WHERE source_month=DATE '2013-01-01'").fetchone()[0]
        assert 'fact_game_2013_01' in json.dumps(plan) and 'fact_game_2013_02' not in json.dumps(plan)
    corrected = feb.replace('[WhiteTitle "GM"]\n','')+'\n'+game
    c_archive,c_settings = source_settings(tmp_path, corrected.encode(), month='2013-02', expected=2)
    run('python', c_settings, tmp_path/'reports', archive=c_archive)
    with warehouse.connect() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_python.fact_game').fetchone()[0] == 3
        assert conn.execute("SELECT latest_title FROM warehouse_python.dim_player WHERE player_hash=decode(%s,'hex')", (__import__('hashlib').sha256(b'alice').hexdigest(),)).fetchone()[0] is None


def test_resume_after_parse_and_load_interruption(database, tmp_path):
    archive, settings = source_settings(tmp_path)
    original = run('python', settings, tmp_path/'reports', archive=archive)
    with pytest.raises(InterruptedError):
        run('python', settings, tmp_path/'reports', archive=archive, interrupt_after_transform=1)
    with warehouse.connect() as conn:
        run_id = conn.execute("SELECT run_id FROM control.pipeline_runs WHERE status='failed' ORDER BY started_at DESC LIMIT 1").fetchone()[0]
        assert conn.execute("SELECT run_id FROM control.month_publications WHERE engine='python'").fetchone()[0] == original['run_id']
    with pytest.raises(InterruptedError):
        run('python', settings, tmp_path/'reports', archive=archive, resume=run_id, interrupt_after_load=1)
    with warehouse.connect() as conn:
        checkpoints = conn.execute('SELECT shard_id,checksum,loaded_at FROM control.run_shards WHERE run_id=%s ORDER BY shard_id',(run_id,)).fetchall()
        assert checkpoints
    report = run('python', settings, tmp_path/'reports', archive=archive, resume=run_id)
    assert report['resumed'] and report['accepted_games'] == 2
    with warehouse.connect() as conn:
        after = conn.execute('SELECT shard_id,checksum,loaded_at FROM control.run_shards WHERE run_id=%s ORDER BY shard_id',(run_id,)).fetchall()
        assert all(row in after for row in checkpoints)


def test_quality_failure_preserves_published_month(database, tmp_path):
    archive, settings = source_settings(tmp_path)
    original = run('python', settings, tmp_path/'reports', archive=archive)
    with pytest.raises(ValueError, match='rejection ratio'):
        run('python', replace(settings,max_reject_ratio=0.01), tmp_path/'reports', archive=archive)
    with warehouse.connect() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_python.fact_game').fetchone()[0] == 2
        assert conn.execute("SELECT run_id FROM control.month_publications WHERE engine='python'").fetchone()[0] == original['run_id']
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute('CREATE TABLE warehouse_python.forbidden_loader_table(id INTEGER)')
    reader = psycopg.connect(host=os.environ['PGHOST'], dbname=database, user='chess_reader', password=os.environ['READER_PASSWORD'])
    with reader:
        assert reader.execute('SELECT count(*) FROM warehouse_python.fact_game').fetchone()[0] == 2
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with reader.transaction():
                reader.execute('DELETE FROM warehouse_python.fact_game')
