"""Run inside study-api; keep the baseline private because it contains row IDs.

Capture: python /checks/verify-study-preservation.py --baseline /private/before.json --capture
Verify:  python /checks/verify-study-preservation.py --baseline /private/before.json
New rows are allowed; every original row must retain its original contents.
The additive review-progress column is excluded on both sides of migration 008.
"""
import argparse
import json
from pathlib import Path
from psycopg import sql
from chess_lakehouse.personal.workspace import storage as db

TABLES={'game_revisions':['id'],'library_snapshots':['id'],
        'snapshot_games':['owner_id','snapshot_id','game_id'],'saved_studies':['id'],
        'workspace_reviews':['owner_id','revision_id','ply'],'workspace_analysis':['id']}
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--capture',action='store_true');args=p.parse_args()
current={}
with db.connect() as c:
    c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    for name,keys in TABLES.items():
        query=sql.SQL("SELECT concat_ws(':',{}) AS key,md5((to_jsonb(t)-'progress')::text) AS digest FROM study.{} t").format(
            sql.SQL(',').join(sql.Identifier(k) for k in keys),sql.Identifier(name))
        current[name]={r['key']:r['digest'] for r in c.execute(query)}
if args.capture:
    with args.baseline.open('x') as f:json.dump(current,f)
    print(json.dumps({'status':'captured','rows':{k:len(v) for k,v in current.items()}}))
else:
    before=json.loads(args.baseline.read_text());counts={}
    for name,rows in before.items():
        bad=[key for key,value in rows.items() if current[name].get(key)!=value]
        if bad:raise SystemExit(f'{name}: {len(bad)} original rows changed or missing')
        counts[name]={'preserved':len(rows),'added':len(current[name])-len(rows)}
    print(json.dumps({'status':'passed','tables':counts},indent=2))
