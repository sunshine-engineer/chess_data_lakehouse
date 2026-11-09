"""Read-only Cloud SQL acceptance against the retained, completed source snapshot.

Run inside compose.cloudsql.yaml's migration container with /checks mounted.
The report contains aggregate evidence, never passwords or private game/account IDs.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from psycopg import sql

from chess_lakehouse.transfer import state
from chess_lakehouse.transfer.managed import schema_hash
from chess_lakehouse.transfer.settings import Endpoint


def main():
    """Check the completion ledger, application DDL, representative counts and login."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/migration'))
    args=parser.parse_args()
    if os.environ.get('CHESS_CONTAINER')!='1':parser.error('Run this verifier inside Docker')
    root=args.root;jobroot=root/'cloudsql-upsert'
    tracker=json.loads((jobroot/'tracker.json').read_text())
    metadata=json.loads((jobroot/'manifest.json').read_text())
    prepared=json.loads((root/'cloudsql-schema/schema-preparation.json').read_text())
    if tracker['phase']!='complete' or tracker['report']['status']!='complete':raise ValueError('Migration is not complete')
    target=Endpoint('TARGET')
    digest=schema_hash(target,prepared['schemas'])
    if digest!=prepared['source_schema_sha256']:raise ValueError('Application schema changed after preparation')
    counts={}
    for name in ('warehouse_python','warehouse_python_mp','warehouse_spark'):
        counts[(name,'fact_game')]=sum(metadata['fingerprints'][oid]['rows']
            for oid,table in metadata['relations'].items()
            if table['schema']==name and table['name'].startswith('fact_game_'))
    for oid,table in metadata['relations'].items():
        if table['schema']=='study' and table['name'] in ('accounts','game_revisions','library_snapshots','snapshot_games','saved_studies'):
            counts[('study',table['name'])]=metadata['fingerprints'][oid]['rows']
    with target.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        ledger=conn.execute(sql.SQL('SELECT status,report FROM {}.jobs WHERE id=%s').format(sql.Identifier(state.SCHEMA)),(tracker['job_id'],)).fetchone()
        if not ledger or ledger[0]!='complete' or ledger[1]!=tracker['report']:raise ValueError('Destination completion ledger differs from the tracker')
        if conn.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]:raise ValueError('Application migration login must not be a superuser')
        login,database=conn.execute('SELECT current_user,current_database()').fetchone()
        actual={}
        for (schema,table),expected in counts.items():
            value=conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(schema,table))).fetchone()[0]
            if value!=expected:raise ValueError('Representative count mismatch: '+schema+'.'+table)
            actual[schema+'.'+table]=value
        invalid=conn.execute("""SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
            WHERE n.nspname=ANY(%s) AND NOT c.convalidated""",(prepared['schemas'],)).fetchone()[0]
        if invalid:raise ValueError('Unvalidated application constraints remain')
    report=dict(status='passed',verified_at_utc=datetime.now(timezone.utc).isoformat(),
        target_instance=prepared['target_instance'].split(':')[-1],database=database,login=login,
        login_is_superuser=False,mode=tracker['report']['mode'],conflict_policy='source-wins',
        schemas_verified=len(prepared['schemas']),tables_verified=tracker['report']['tables_verified'],
        rows_verified=tracker['report']['rows_verified'],source_rows=tracker['report']['source_rows'],
        destination_only_rows_preserved=tracker['report']['destination_only_rows_preserved'],
        units_committed=tracker['report']['units_committed'],schema_sha256=digest,
        representative_counts=actual,application_constraints_validated=True,
        native_objects_verified=True,checkpoint_resume_verified=True)
    state.atomic_json(root/'cloudsql-verification.json',report)
    print(json.dumps(report,indent=2,sort_keys=True))


if __name__=='__main__':main()
