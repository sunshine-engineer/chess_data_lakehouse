"""Account-owned archive controls. A separate loader container executes jobs."""
from uuid import uuid4
from psycopg.types.json import Jsonb

from ... import archive_catalog
from . import storage as db


def catalog():
    """Expose the official checksum-backed monthly catalog and torrent normalization policy."""
    return {'archives':archive_catalog.catalog(),'source':archive_catalog.BASE,'torrent_method':'Official torrent links select their matching HTTPS PGN download.'}


def create(owner,value):
    """Queue one account-owned monthly archive after validating its source and download ceiling."""
    engine=value.get('engine','python_mp')
    if engine not in ('python','python_mp','spark'):raise ValueError('Choose Python, parallel Python or Spark')
    source=archive_catalog.select(value);key=str(uuid4())
    with db.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('archive:'+str(owner),))
        if c.execute("SELECT count(*) AS n FROM study.archive_jobs WHERE owner_id=%s AND status IN ('queued','running')",(owner,)).fetchone()['n']>=2:raise ValueError('Finish or pause an archive job before adding another')
        c.execute('INSERT INTO study.archive_jobs(id,owner_id,source,engine) VALUES(%s,%s,%s,%s)',(key,owner,Jsonb(source),engine))
    return {'id':key,'status':'queued','source':source}


def jobs(owner):
    """Return the account's recent archive jobs and automatic-latest subscription settings."""
    with db.connect() as c:
        rows=c.execute('SELECT * FROM study.archive_jobs WHERE owner_id=%s ORDER BY created_at DESC LIMIT 20',(owner,)).fetchall()
        subscription=c.execute('SELECT * FROM study.archive_subscriptions WHERE owner_id=%s',(owner,)).fetchone()
    return {'jobs':rows,'subscription':subscription}


def control(owner,value):
    """Apply an owner-checked archive pause, retry or cancellation request."""
    action=value.get('action')
    if action not in ('pause','resume','cancel'):raise ValueError('Choose pause, resume or cancel')
    with db.connect() as c:
        row=c.execute('SELECT * FROM study.archive_jobs WHERE owner_id=%s AND id=%s FOR UPDATE',(owner,db.identifier(value.get('id')))).fetchone()
        if not row:raise ValueError('Archive job not found in your account')
        if row['status']=='complete':raise ValueError('A completed publication is immutable; start a new job to reprocess')
        if action=='resume':
            if row['status'] not in ('failed','paused'):raise ValueError('Only paused or failed archive jobs can resume')
            c.execute("UPDATE study.archive_jobs SET status='queued',action='run',error=NULL,updated_at=now() WHERE id=%s",(row['id'],))
        else:
            c.execute("UPDATE study.archive_jobs SET action=%s,status=CASE WHEN status='running' THEN status ELSE %s END,updated_at=now() WHERE id=%s",(action,'paused' if action=='pause' else 'cancelled',row['id']))
    return jobs(owner)


def subscribe(owner,value):
    """Save explicit account preferences for bounded automatic latest-month imports."""
    if type(value.get('enabled')) is not bool:raise ValueError('Choose whether automatic latest imports are enabled')
    maximum=value.get('max_download_bytes');engine=value.get('engine','python_mp')
    if type(maximum) is not int or not 1<=maximum<=2*1024**4 or engine not in ('python','python_mp','spark'):raise ValueError('Choose a valid archive ceiling and engine')
    with db.connect() as c:c.execute('''INSERT INTO study.archive_subscriptions(owner_id,enabled,max_download_bytes,engine) VALUES(%s,%s,%s,%s)
        ON CONFLICT(owner_id) DO UPDATE SET enabled=EXCLUDED.enabled,max_download_bytes=EXCLUDED.max_download_bytes,engine=EXCLUDED.engine,checked_at=NULL,error=NULL''',(owner,value['enabled'],maximum,engine))
    return jobs(owner)
