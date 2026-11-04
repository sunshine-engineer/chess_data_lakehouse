"""Disposable database drill: prepare durable work, then resume after server restart."""
from dataclasses import asdict
from pathlib import Path
import json
import os
import shutil
import sys
from uuid import uuid4

from psycopg import sql
import zstandard as zstd

from ..pipeline import sha256_file
from .migrate import admin_connect,migrate
from .settings import Settings,Source
from .storage import atomic_json
from .workflow import run,event
from . import warehouse

STATE=Path('/data/crash-drill-state.json')


def loader(database):
    os.environ['PGDATABASE']=database
    os.environ['PGUSER']='chess_loader'
    os.environ['PGPASSWORD']=os.environ['LOADER_PASSWORD']


def prepare_drill():
    if STATE.exists():
        raise ValueError('a previous drill state exists; resume or explicitly clean it first')
    admin=admin_connect(); admin.autocommit=True
    database='chess_crash_test_'+uuid4().hex[:12]
    root=Path('/data/crash-drill')/uuid4().hex
    root.mkdir(parents=True)
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    os.environ['PGDATABASE']=database
    migrate()
    loader(database)
    archive=root/'fixture.zst'
    archive.write_bytes(zstd.ZstdCompressor().compress(Path('/app/tests/fixtures/games.pgn').read_bytes()))
    settings=Settings(Source('2013-01',sha256_file(archive),5,1000000,'synthetic-no-network'),
        data_root=root,frame_target_bytes=600,copy_batch_rows=2,parse_batch_rows=2,
        max_reject_ratio=0.5,max_unknown_player_ratio=1,min_free_disk_bytes=0)
    baseline=run('python',settings,root/'reports',group='crash-drill',archive=archive)
    try:
        run('python',settings,root/'reports',group='crash-drill',archive=archive,interrupt_after_load=1)
    except InterruptedError:
        pass
    with warehouse.connect() as conn:
        run_id=conn.execute("SELECT run_id FROM control.pipeline_runs WHERE status='failed' ORDER BY started_at DESC LIMIT 1").fetchone()[0]
        checkpoints=conn.execute('SELECT shard_id,checksum,loaded_at::text FROM control.run_shards WHERE run_id=%s ORDER BY shard_id',(run_id,)).fetchall()
        started=str(conn.execute('SELECT pg_postmaster_start_time()').fetchone()[0])
    atomic_json(STATE,dict(database=database,root=str(root),archive=str(archive),settings=settings.snapshot(),
        run_id=run_id,baseline_run=baseline['run_id'],checkpoints=checkpoints,postmaster_started=started))
    admin.close()
    event('crash_drill_ready',database=database,run_id=run_id,checkpoints=len(checkpoints))


def resume_drill():
    state=json.loads(STATE.read_text())
    admin=admin_connect(); admin.autocommit=True
    database=state['database']; root=Path(state['root'])
    values=state['settings']; values.pop('transform_version')
    values['source']=Source(**values['source']); values['data_root']=Path(values['data_root'])
    settings=Settings(**values)
    loader(database)
    try:
        with warehouse.connect() as conn:
            started=str(conn.execute('SELECT pg_postmaster_start_time()').fetchone()[0])
            if started==state['postmaster_started']:
                raise ValueError('PostgreSQL was not restarted; no crash recovery evidence')
            assert conn.execute("SELECT run_id FROM control.month_publications WHERE engine='python'").fetchone()[0]==state['baseline_run']
            assert conn.execute('SELECT relpersistence FROM pg_class WHERE oid=%s::regclass',('staging.'+state['run_id'],)).fetchone()[0]=='p'
            checkpoints=conn.execute('SELECT shard_id,checksum,loaded_at::text FROM control.run_shards WHERE run_id=%s ORDER BY shard_id',(state['run_id'],)).fetchall()
            assert [list(row) for row in checkpoints]==state['checkpoints']
        report=run('python',settings,root/'reports',resume=state['run_id'],archive=Path(state['archive']))
        assert report['accepted_games']==2 and report['resumed']
        atomic_json('/reports/database-crash-recovery.json',dict(status='passed',synthetic_fixture=True,
            server_restart_detected=True,logged_checkpoints_survived=True,prior_publication_preserved=True,
            resumed_games=report['accepted_games'],checkpoints_reused=len(checkpoints)))
        event('crash_recovery_verified',accepted_games=report['accepted_games'])
    finally:
        admin.execute(sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(database)))
        admin.close(); shutil.rmtree(root,ignore_errors=True); STATE.unlink(missing_ok=True)


if __name__=='__main__':
    if sys.argv[1:] == ['prepare']: prepare_drill()
    elif sys.argv[1:] == ['resume']: resume_drill()
    else: raise SystemExit('usage: crash_drill prepare|resume')
