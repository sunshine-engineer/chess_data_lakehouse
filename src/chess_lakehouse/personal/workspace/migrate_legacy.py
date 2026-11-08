"""Explicit private owner provisioning and non-destructive legacy study migration."""
import argparse
import json
from pathlib import Path
import secrets
from uuid import uuid4

from psycopg.types.json import Jsonb

from .. import repository as old
from . import accounts,storage as db,imports
from .config import load


def migrate(owner):
    """Reuse immutable payloads, snapshots, saved studies, notes and engine evidence."""
    with db.connect() as c:
        versions=c.execute('''SELECT i.*,w.player FROM study.imports i JOIN study.workspaces w ON w.id=i.workspace_id
            ORDER BY i.created_at,i.version''').fetchall()
    migrated=0
    for legacy in versions:
        with db.connect() as c:
            if c.execute('SELECT 1 FROM study.legacy_mappings WHERE legacy_import=%s',(legacy['id'],)).fetchone():continue
            lib=db.library(c,owner,player=legacy['player'],create=True)
            key=str(uuid4())
            c.execute("""INSERT INTO study.import_jobs(id,owner_id,library_id,source_kind,player,mode,status,source_sha256,phase)
                VALUES(%s,%s,%s,'legacy',%s,'merge','running',%s,'Migrating saved games')""",(key,owner,lib['id'],legacy['player'],legacy['source_sha256']))
            for row in old.payloads(legacy):
                if 'game_date' in row.get('metadata_defaults',[]):row['game_date']=None
                revision=db.save_revision(c,owner,row)
                c.execute('INSERT INTO study.import_members VALUES(%s,%s,%s,%s)',(owner,key,row['game_id'],revision))
        snapshot=imports.publish(owner,key,load())
        with db.connect() as c:
            c.execute('UPDATE study.import_jobs SET games_processed=%s WHERE id=%s',(legacy['game_count'],key))
            c.execute('INSERT INTO study.legacy_mappings VALUES(%s,%s,%s)',(legacy['id'],owner,snapshot))
            c.execute('''INSERT INTO study.saved_studies(id,owner_id,snapshot_id,name,filters)
                SELECT id,%s,%s,name,filters FROM study.cohorts WHERE import_id=%s ON CONFLICT(id) DO NOTHING''',(owner,snapshot,legacy['id']))
            for review in c.execute('SELECT * FROM study.review_items WHERE import_id=%s',(legacy['id'],)).fetchall():
                revision=c.execute('SELECT revision_id FROM study.snapshot_games WHERE snapshot_id=%s AND game_id=%s',(snapshot,review['game_id'])).fetchone()
                c.execute('INSERT INTO study.workspace_reviews VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (owner,revision['revision_id'],review['ply'],review['status'],review['note'],review['updated_at']))
            for job in c.execute("SELECT * FROM study.analysis_jobs WHERE import_id=%s AND status='complete'",(legacy['id'],)).fetchall():
                revision=c.execute('SELECT revision_id FROM study.snapshot_games WHERE snapshot_id=%s AND game_id=%s',(snapshot,job['game_id'])).fetchone()
                c.execute('''INSERT INTO study.workspace_analysis(id,owner_id,revision_id,settings_hash,player,status,evidence,finished_at)
                    VALUES(%s,%s,%s,%s,%s,'complete',%s,%s) ON CONFLICT DO NOTHING''',
                    (str(uuid4()),owner,revision['revision_id'],job['settings_hash'],legacy['player'],Jsonb(job['evidence']),job['finished_at']))
        migrated+=1
    return migrated


def provision(username,credential_file):
    """Provision the approved migration of legacy player-month publications into account libraries."""
    path=Path(credential_file)
    with db.connect() as c:user=c.execute('SELECT id,username FROM study.accounts WHERE username_key=%s',(username.casefold(),)).fetchone()
    if not user:
        password=secrets.token_urlsafe(20)
        user=accounts.create(username,password)
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x') as out:json.dump({'username':username,'password':password,'instructions':'Sign in to Chess Atlas, then change this app password in Account settings.'},out,indent=2)
        try:path.chmod(0o600)
        except PermissionError:pass
    count=migrate(str(user['id']))
    print(json.dumps({'owner':username,'legacy_versions_migrated':count,'credential_file':str(path),'password_printed':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--username',required=True);parser.add_argument('--credential-file',required=True)
    args=parser.parse_args();provision(args.username,args.credential_file)
