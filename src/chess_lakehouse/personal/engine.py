"""Explicit single-worker Stockfish review jobs with fixed resource budgets."""
from dataclasses import asdict
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import chess
import chess.engine
from psycopg.types.json import Jsonb

from . import repository as db


def settings(settings):
    binary=Path(os.environ.get('STOCKFISH_PATH','/usr/games/stockfish'))
    if not binary.is_file():
        raise ValueError('Stockfish is unavailable in this study-service image')
    values=dict(binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        analysis_version=3,analysis_code_sha256=hashlib.sha256((Path(__file__).read_bytes()+Path(__file__).with_name('review.py').read_bytes())).hexdigest(),
        threads=1,hash_mb=settings.engine_hash_mb,nodes=settings.engine_nodes,
        game_seconds=settings.engine_game_seconds,review_loss_cp=150)
    fingerprint=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    return binary,values,fingerprint


def enqueue(config,identifier,game_id):
    _,options,fingerprint=settings(config)
    with db.connect() as c:
        v=db.version(c,config,identifier)
        if not c.execute('SELECT 1 FROM study.games WHERE import_id=%s AND game_id=%s',(v['id'],game_id)).fetchone():
            raise ValueError('game does not belong to this personal version')
        c.execute("SELECT pg_advisory_xact_lock(hashtext('personal-analysis:' || %s))",(str(v['id']),))
        existing=c.execute('SELECT * FROM study.analysis_jobs WHERE import_id=%s AND game_id=%s AND settings_hash=%s',
            (v['id'],game_id,fingerprint)).fetchone()
        if existing:
            if existing['status']=='failed':
                c.execute("UPDATE study.analysis_jobs SET status='queued',error=NULL,started_at=NULL,finished_at=NULL WHERE id=%s",(existing['id'],))
                existing['status']='queued'
            return dict(id=str(existing['id']),status=existing['status'],cached=existing['status']=='complete')
        count=c.execute('SELECT count(DISTINCT game_id) AS n FROM study.analysis_jobs WHERE import_id=%s',(v['id'],)).fetchone()['n']
        known=c.execute('SELECT 1 FROM study.analysis_jobs WHERE import_id=%s AND game_id=%s LIMIT 1',(v['id'],game_id)).fetchone()
        if count>=config.max_analyzed_games and not known:
            raise ValueError('this version has reached its three-game engine review budget')
        job_id=str(uuid4())
        c.execute('''INSERT INTO study.analysis_jobs(id,import_id,game_id,status,settings_hash,evidence)
            VALUES(%s,%s,%s,'queued',%s,%s)''',(job_id,v['id'],game_id,fingerprint,Jsonb({'budget':options})))
        return dict(id=job_id,status='queued',cached=False)


def analyze(row,config,progress=None):
    from .review import analyze as review
    binary,options,_=settings(config)
    return review(row,config,binary,options,progress)


def run_one(config):
    with db.connect() as c:
        w=db.workspace(c,config)
        if not w:
            return False
        job=c.execute("""SELECT j.* FROM study.analysis_jobs j JOIN study.imports i ON i.id=j.import_id
            WHERE i.workspace_id=%s AND j.status='queued' ORDER BY j.created_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED""",(w['id'],)).fetchone()
        if not job:
            return False
        if job['settings_hash']!=settings(config)[2]:
            c.execute("UPDATE study.analysis_jobs SET status='failed',error='Analysis code or configuration changed; request a new review.',finished_at=now() WHERE id=%s",(job['id'],))
            return True
        v=db.version(c,config,str(job['import_id']))
        row=db.payload(v,job['game_id'])
        c.execute("UPDATE study.analysis_jobs SET status='running',started_at=now() WHERE id=%s",(job['id'],))
    try:
        evidence=analyze(row,config)
        with db.connect() as c:
            c.execute("UPDATE study.analysis_jobs SET status='complete',evidence=%s,finished_at=now() WHERE id=%s",
                (Jsonb(evidence),job['id']))
    except Exception as exc:
        with db.connect() as c:
            c.execute("UPDATE study.analysis_jobs SET status='failed',error=%s,finished_at=now() WHERE id=%s",
                ('Engine review failed ('+type(exc).__name__+'); retry this explicit job.',job['id']))
    return True


def start_worker(config):
    def loop():
        # Shared-volume flock prevents duplicate workers, and releases on process death.
        with (db.root()/'.engine-worker.lock').open('w') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with db.connect() as c:
                w=db.workspace(c,config)
                if w:
                    c.execute("""UPDATE study.analysis_jobs SET status='failed',error='Worker restarted; retry the explicit review.',finished_at=now()
                        WHERE status='running' AND import_id IN (SELECT id FROM study.imports WHERE workspace_id=%s)""",(w['id'],))
            while True:
                try:
                    worked=run_one(config)
                except Exception:
                    worked=False
                if not worked:
                    time.sleep(2)
    worker=threading.Thread(target=loop,name='personal-engine-worker',daemon=True)
    worker.start()
    return worker
