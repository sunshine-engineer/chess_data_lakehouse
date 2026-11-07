"""Separate single import and engine workers; database jobs survive HTTP requests."""
import fcntl
import threading
import time

from psycopg.types.json import Jsonb

from .. import engine
from . import storage as db,imports


def import_one(config,opener=None):
    with db.connect() as c:
        row=c.execute("SELECT * FROM study.import_jobs WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not row:return False
        c.execute("UPDATE study.import_jobs SET status='running',phase='Preparing import' WHERE id=%s",(row['id'],))
    imports.process(row,config,opener)
    return True


def analyze_one(config):
    with db.connect() as c:
        row=c.execute("SELECT * FROM study.workspace_analysis WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not row:return False
        revision=c.execute('SELECT * FROM study.game_revisions WHERE owner_id=%s AND id=%s',(row['owner_id'],row['revision_id'])).fetchone()
        c.execute("UPDATE study.workspace_analysis SET status='running' WHERE id=%s",(row['id'],))
    try:
        if row['settings_hash']!=engine.settings(config)[2]:raise ValueError('Engine settings changed. Request a new review.')
        payload=db.perspective(revision['payload'],row['player'])
        evidence=engine.analyze(payload,config)
        evidence['player_color']=payload['player_color']
        with db.connect() as c:c.execute("UPDATE study.workspace_analysis SET status='complete',evidence=%s,finished_at=now() WHERE id=%s",(Jsonb(evidence),row['id']))
    except Exception as e:
        message=str(e) if isinstance(e,ValueError) else 'Engine review failed; retry this game.'
        with db.connect() as c:c.execute("UPDATE study.workspace_analysis SET status='failed',error=%s,finished_at=now() WHERE id=%s",(message,row['id']))
    return True


def index_one(config):
    with db.connect() as c:
        row=c.execute('SELECT * FROM study.game_revisions WHERE NOT positions_ready ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED').fetchone()
        if not row:return False
        db.index_positions(c,row['owner_id'],row['id'],row['payload'])
        return True


def start(config):
    def loop(kind,run):
        with (db.root()/('.'+kind+'-worker.lock')).open('w') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            interrupted=[]
            with db.connect() as c:
                if kind=='import':
                    interrupted=c.execute("SELECT id,owner_id,input_path FROM study.import_jobs WHERE status='running'").fetchall()
                    c.execute("UPDATE study.import_jobs SET status='failed',phase='Interrupted',error='Service restarted. Retry this file or link.',finished_at=now(),input_path=NULL WHERE status='running'")
                elif kind=='engine':c.execute("UPDATE study.workspace_analysis SET status='failed',error='Service restarted; request this review again.',finished_at=now() WHERE status='running'")
            for job in interrupted:
                imports.cleanup_staging(job['owner_id'],job['id'])
                if job['input_path']:
                    from pathlib import Path
                    Path(job['input_path']).unlink(missing_ok=True)
            while True:
                try:worked=run(config)
                except Exception:
                    import logging
                    logging.exception('Workspace %s worker failed',kind);worked=False
                if not worked:time.sleep(1)
    for kind,run in (('import',import_one),('engine',analyze_one),('position-index',index_one)):
        threading.Thread(target=loop,args=(kind,run),name='study-'+kind,daemon=True).start()
