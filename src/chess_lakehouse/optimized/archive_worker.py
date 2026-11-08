"""Loader-only archive supervisor and lazy full-archive repertoire feeder.

One subprocess at a time; no Docker socket or user-supplied shell commands.
SIGTERM pauses the active job. Restart recovers it. Download partials and verified
frames/silver shards survive. A resumed zstd framing pass rescans the prefix.
"""
import io
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import chess.pgn
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pyarrow.parquet as pq

from .. import archive_catalog
from .storage import file_lock,verify_file,FRAME_SCHEMA


def connect():return psycopg.connect('',row_factory=dict_row,connect_timeout=5)


def update(key,**values):
    from psycopg import sql
    with connect() as c:c.execute(sql.SQL('UPDATE study.archive_jobs SET {},updated_at=now() WHERE id=%s').format(sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in values)),[*[Jsonb(v) if isinstance(v,dict) else v for v in values.values()],key])


def scheduled():
    with connect() as c:rows=c.execute("SELECT * FROM study.archive_subscriptions WHERE enabled AND (checked_at IS NULL OR checked_at<now()-interval '1 hour')").fetchall()
    for subscription in rows:
        try:
            source=archive_catalog.select(subscription)
            with connect() as c:
                exists=c.execute("SELECT 1 FROM study.archive_jobs WHERE owner_id=%s AND source->>'month'=%s LIMIT 1",(subscription['owner_id'],source['month'])).fetchone()
                if not exists:c.execute('INSERT INTO study.archive_jobs(id,owner_id,source,engine) VALUES(%s,%s,%s,%s)',(str(uuid4()),subscription['owner_id'],Jsonb(source),subscription['engine']))
                c.execute('UPDATE study.archive_subscriptions SET checked_at=now(),error=NULL WHERE owner_id=%s',(subscription['owner_id'],))
        except Exception as exc:
            with connect() as c:c.execute('UPDATE study.archive_subscriptions SET checked_at=now(),error=%s WHERE owner_id=%s',(str(exc)[:300],subscription['owner_id']))


stopping=False
def stop(*args):
    global stopping
    stopping=True


def run_one():
    with connect() as c:
        job=c.execute("SELECT * FROM study.archive_jobs WHERE status='queued' AND action='run' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not job:return False
        c.execute("UPDATE study.archive_jobs SET status='running',phase='Downloading / verifying',updated_at=now() WHERE id=%s",(job['id'],))
    try:
        archive_catalog.source_url(job['source']['url'])
        with tempfile.TemporaryDirectory(prefix='archive-job-') as folder:
            descriptor=Path(folder)/'job.json';descriptor.write_text(json.dumps(job,default=str))
            output=Path(folder)/'events.jsonl'
            with output.open('w') as log:
                child=subprocess.Popen([sys.executable,'-m','chess_lakehouse.optimized.archive_execute',str(descriptor)],stdout=log,stderr=log,start_new_session=True)
            action='run';position=0;last_event={}
            try:
                while child.poll() is None:
                    with connect() as c:
                        row=c.execute('SELECT action FROM study.archive_jobs WHERE id=%s',(job['id'],)).fetchone();action=row['action']
                    if stopping:action='pause'
                    if action!='run':
                        os.killpg(child.pid,signal.SIGTERM)
                        try:child.wait(timeout=10)
                        except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                        break
                    with output.open() as reader:
                        reader.seek(position)
                        while line:=reader.readline():
                            if not line.endswith('\n'):break
                            position=reader.tell()
                            try:event=json.loads(line)
                            except ValueError:continue
                            last_event=event
                            if event.get('event')=='run_started':job['run_id']=event['run_id']
                    phase={'download_progress':'Downloading','frames_ready':'Transforming','run_started':'Transforming','shard_transformed':'Transforming','shard_loaded':'Loading','run_complete':'Published'}.get(last_event.get('event'),'Verifying / framing')
                    update(job['id'],phase=phase,progress={k:last_event[k] for k in ('event','bytes_received','total_bytes','shard_id','accepted_games') if k in last_event},run_id=job['run_id'])
                    time.sleep(1)
            finally:
                if child.poll() is None:os.killpg(child.pid,signal.SIGTERM);child.wait(timeout=10)
            # Drain final events, including a run_started emitted just before pause.
            with output.open() as reader:
                for line in reader:
                    try:event=json.loads(line)
                    except ValueError:continue
                    if event.get('event')=='run_started':job['run_id']=event['run_id']
            # Publication is authoritative even if the stop request raced with completion.
            with connect() as c:
                published=c.execute("SELECT run_id FROM control.pipeline_runs WHERE benchmark_group='archive-app' AND status='complete' AND source_month=%s AND source_sha256=%s AND engine=%s AND started_at>=%s ORDER BY finished_at DESC LIMIT 1",(job['source']['month']+'-01',job['source']['sha256'],job['engine'],job['created_at'])).fetchone()
            if published:update(job['id'],status='complete',phase='Published',run_id=published['run_id'],error=None)
            elif action!='run':update(job['id'],status='paused' if action=='pause' else 'cancelled',phase='Paused; committed work retained' if action=='pause' else 'Cancelled; source files retained',run_id=job['run_id'])
            elif child.returncode:
                with output.open() as reader:
                    reader.seek(max(0,output.stat().st_size-4000));logging.error('Archive child output: %s',reader.read())
                raise ValueError('Archive process failed; inspect archive-worker logs and resume after fixing the cause.')
            else:raise ValueError('Archive process exited without a completed publication')
    except Exception as exc:
        logging.exception('Archive job failed')
        update(job['id'],status='failed',error=str(exc)[:300],phase='Failed; committed work retained')
    return True


def source_key(source):return str(source['source_month'])[:7]+':'+source['source_sha256']


def next_source(sources,cursor):
    pending=[s for s in sources if source_key(s) not in cursor.get('done',[])]
    # Finish the current immutable source before choosing a newly arrived month.
    return next((s for s in pending if source_key(s)==cursor.get('source')),pending[0] if pending else None)


def feed_one(root=Path('/data')):
    """Provide one game at a time from verified complete frames, without sampling.

    Cursor is a shard/row-group/row tuple. At most one row group is read in memory
    (frame byte cap 64MiB). Both players are reviewed before advancing. Imported
    source months discovered later are visited first when selecting a new month.
    """
    with connect() as c:
        build=c.execute("SELECT * FROM study.repertoire_builds b WHERE status='running' AND source_stage='archive' AND NOT EXISTS(SELECT 1 FROM study.repertoire_tasks t WHERE t.build_id=b.id AND t.status IN ('queued','running')) ORDER BY updated_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not build:return False
        sources=c.execute('SELECT DISTINCT source_month,source_sha256 FROM control.month_publications ORDER BY source_month DESC').fetchall()
        cursor=build['archive_cursor'];done=cursor.get('done',[])
        source=next_source(sources,cursor)
        if not source:
            c.execute("UPDATE study.repertoire_builds SET error='Waiting for another imported archive month. Latest available locally has been scanned.',updated_at=now() WHERE id=%s",(build['id'],));return False
        key=source_key(source)
        if cursor.get('source')!=key:cursor={'source':key,'done':done,'shard':0,'group':0,'row':0,'side':0}
        manifest=None
        for path in (root/'framed').glob('*/manifest.json'):
            candidate=json.loads(path.read_text())
            if candidate.get('complete') and candidate.get('source_sha256')==source['source_sha256']:manifest=candidate;break
        if not manifest:
            c.execute("UPDATE study.repertoire_builds SET error='Published source frames are unavailable; restore retained pipeline data.',updated_at=now() WHERE id=%s",(build['id'],));return False
        if cursor['shard']>=len(manifest['shards']):
            cursor['done'].append(key);cursor.pop('source');c.execute('UPDATE study.repertoire_builds SET archive_cursor=%s,updated_at=now() WHERE id=%s',(Jsonb(cursor),build['id']));return True
        entry=manifest['shards'][cursor['shard']];path=verify_file(entry,FRAME_SCHEMA);parquet=pq.ParquetFile(path)
        if cursor['group']>=parquet.num_row_groups:cursor.update(shard=cursor['shard']+1,group=0,row=0,side=0)
        else:
            batch=parquet.read_row_group(cursor['group'],columns=['pgn','record_number'])
            if cursor['row']>=batch.num_rows:cursor.update(group=cursor['group']+1,row=0,side=0)
            else:
                text=batch.column('pgn')[cursor['row']].as_py();number=batch.column('record_number')[cursor['row']].as_py()
                # python-chess skips variations; read at most the framed per-record bound.
                try:
                    game=chess.pgn.read_game(io.StringIO(text));board=game.board() if game else None
                    if game is None or game.errors or game.headers.get('Variant','Standard')!='Standard' or not board.is_valid():raise ValueError('Unsupported archive game')
                    moves=[m.uci() for m in game.mainline_moves()]
                    if not moves or len(moves)>2000:raise ValueError('Unsupported archive game length')
                    side='white' if cursor['side']==0 else 'black'
                    payload=dict(initial_fen=board.fen(),moves=moves,player_color=side)
                    import re
                    match=re.search(r'lichess\.org/(?:game/)?([A-Za-z0-9]{8})(?:[/?#]|$)',game.headers.get('Site',''))
                    if match:payload['game_id']=match[1]
                    record_key=f"{key}:{number}:{side}"
                    c.execute('INSERT INTO study.repertoire_tasks(build_id,source_key,payload,player,source_month) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(build['id'],record_key,Jsonb(payload),side,str(source['source_month'])[:7]))
                except ValueError:pass
                if cursor['side']==0:cursor['side']=1
                else:cursor.update(row=cursor['row']+1,side=0)
        c.execute('UPDATE study.repertoire_builds SET archive_cursor=%s,error=NULL,updated_at=now() WHERE id=%s',(Jsonb(cursor),build['id']))
    return True


def main():
    if os.environ.get('CHESS_CONTAINER')!='1':raise ValueError('Run this worker inside Docker')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with file_lock(Path('/data/.archive-supervisor.lock'),blocking=False):
        with connect() as c:c.execute("UPDATE study.archive_jobs SET status=CASE WHEN action='cancel' THEN 'cancelled' WHEN action='pause' THEN 'paused' ELSE 'queued' END,phase='Recovered after restart' WHERE status='running'")
        while not stopping:
            try:
                scheduled()
                if not run_one():
                    if not feed_one():time.sleep(3)
            except Exception:logging.exception('Archive supervisor error');time.sleep(5)


if __name__=='__main__':main()
