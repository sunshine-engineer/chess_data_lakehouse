"""Separate single import and engine workers; database jobs survive HTTP requests."""
import fcntl
import threading
import time

from psycopg.types.json import Jsonb

from .. import engine
from . import storage as db,imports,board,repertoire
from ...runtime import load as runtime


def import_one(config,opener=None):
    """Claim and process one durable import job without sharing private account credentials externally."""
    with db.connect() as c:
        row=c.execute("SELECT * FROM study.import_jobs WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not row:return False
        c.execute("UPDATE study.import_jobs SET status='running',phase='Preparing import' WHERE id=%s",(row['id'],))
    imports.process(row,config,opener)
    return True


def analyze_one(config):
    """Claim one queued game review and persist bounded Stockfish evidence and progress."""
    with db.connect() as c:
        row=c.execute("SELECT * FROM study.workspace_analysis WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not row:return False
        revision=c.execute('SELECT * FROM study.game_revisions WHERE owner_id=%s AND id=%s',(row['owner_id'],row['revision_id'])).fetchone()
        c.execute("UPDATE study.workspace_analysis SET status='running' WHERE id=%s",(row['id'],))
    try:
        if row['settings_hash']!=engine.settings(config)[2]:raise ValueError('Engine settings changed. Request a new review.')
        payload=db.perspective(revision['payload'],row['player'])
        def progress(value):
            with db.connect() as c:c.execute('UPDATE study.workspace_analysis SET progress=%s WHERE id=%s',(Jsonb(value),row['id']))
        evidence=engine.analyze(payload,config,progress)
        evidence['player_color']=payload['player_color']
        with db.connect() as c:c.execute("UPDATE study.workspace_analysis SET status='complete',evidence=%s,finished_at=now() WHERE id=%s",(Jsonb(evidence),row['id']))
    except Exception as e:
        message=str(e) if isinstance(e,ValueError) else 'Engine review failed; retry this game.'
        with db.connect() as c:c.execute("UPDATE study.workspace_analysis SET status='failed',error=%s,finished_at=now() WHERE id=%s",(message,row['id']))
    return True


def index_one(config):
    """Recover the legal-position index for one immutable revision lacking completed indexing."""
    with db.connect() as c:
        row=c.execute('SELECT * FROM study.game_revisions WHERE NOT positions_ready ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED').fetchone()
        if not row:return False
        db.index_positions(c,row['owner_id'],row['id'],row['payload'])
        return True


def engine_one(config):
    # One worker owns both queues; neither kind can create parallel Stockfish.
    """Prioritize interactive searches and game reviews before background repertoire mining."""
    position=board.run_one(config)
    game=analyze_one(config)
    return position or game or repertoire.run_one(config)


def start(config,stop=None):
    """Start one thread per queue and return handles for foreground supervision.

    Embedded mode remains compatible with existing Compose setups. Kubernetes
    uses the foreground entrypoint below with embedded API workers disabled.
    """
    stop=stop or threading.Event()
    def loop(kind,run):
        with (db.root()/('.'+kind+'-worker.lock')).open('w') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            interrupted=[]
            with db.connect() as c:
                if kind=='import':
                    interrupted=c.execute("SELECT id,owner_id,input_path FROM study.import_jobs WHERE status='running'").fetchall()
                    c.execute("UPDATE study.import_jobs SET status='failed',phase='Interrupted',error='Service restarted. Retry this file or link.',finished_at=now(),input_path=NULL WHERE status='running'")
                elif kind=='engine':
                    c.execute("UPDATE study.repertoire_tasks SET status='queued' WHERE status='running'")
                    c.execute("UPDATE study.workspace_analysis SET status='failed',error='Service restarted; request this review again.',finished_at=now() WHERE status='running'")
                    c.execute("UPDATE study.position_analysis SET status='failed',error='Service restarted; request this position again.',finished_at=now() WHERE status='running'")
            for job in interrupted:
                imports.cleanup_staging(job['owner_id'],job['id'])
                if job['input_path']:
                    from pathlib import Path
                    Path(job['input_path']).unlink(missing_ok=True)
            while not stop.is_set():
                try:worked=run(config)
                except Exception:
                    import logging
                    logging.exception('Workspace %s worker failed',kind);worked=False
                if not worked:stop.wait(runtime().workers.poll_seconds)
    threads=[]
    for kind,run in (('import',import_one),('engine',engine_one),('position-index',index_one)):
        thread=threading.Thread(target=loop,args=(kind,run),name='study-'+kind,daemon=True)
        thread.start();threads.append(thread)
    return threads


def main():
    """Run supervised workers with SIGTERM draining and separate live/ready probes.

    Stop accepting new jobs when signalled. Current bounded engine work may
    finish during the configured grace period. A forced import interruption
    retains existing published games; its job becomes retryable at next startup.
    """
    import json
    import signal
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from .config import load
    import os
    from pathlib import Path
    settings=runtime()
    Path(os.environ.setdefault('TMPDIR',str(settings.paths.study/'workspace/tmp'))).mkdir(parents=True,exist_ok=True)
    stop=threading.Event();threads=start(load(),stop)

    class Probe(BaseHTTPRequestHandler):
        """Expose health without requiring a private browser session or API token."""

        def log_message(self,*args):
            """Keep routine Kubernetes probe traffic out of worker logs."""

        def do_GET(self):
            """Readiness checks PostgreSQL; liveness checks worker threads only."""
            healthy=all(thread.is_alive() for thread in threads) and not stop.is_set()
            if self.path=='/readyz' and healthy:
                try:
                    with db.connect() as c:c.execute('SELECT 1')
                except Exception:healthy=False
            if self.path not in ('/livez','/healthz','/readyz'):
                self.send_error(404);return
            self.send_response(200 if healthy else 503);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'status':'ok' if healthy else 'unavailable'}).encode())

    server=ThreadingHTTPServer((settings.server.host,settings.server.worker_port),Probe)
    probe=threading.Thread(target=server.serve_forever,daemon=True);probe.start()
    def terminate(*args):stop.set()
    signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
    try:
        while not stop.wait(1):
            if not all(thread.is_alive() for thread in threads):
                raise RuntimeError('A worker stopped; disable embedded workers and ensure only one worker owns this volume')
    finally:
        stop.set();deadline=time.monotonic()+settings.workers.shutdown_seconds
        for thread in threads:thread.join(max(0,deadline-time.monotonic()))
        server.shutdown();server.server_close()


if __name__=='__main__':main()
