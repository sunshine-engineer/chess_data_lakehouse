"""Stream research cohorts to disk in chunks; download only after owner checks."""
import hashlib
import json
from uuid import uuid4

from psycopg.types.json import Jsonb
import pyarrow as pa
import pyarrow.parquet as pq

from . import storage as db,insights

DICTIONARY={
    'game_id':('Stable source/local game identity','identifier'),
    'game_date':('Game-start date, unknown if missing','pre-game, nullable'),
    'player_color':('Color of the selected library player','pre-game'),
    'player_rating':('Selected player rating from the PGN','pre-game, nullable'),
    'opponent_rating':('Opponent rating from the PGN','pre-game, nullable'),
    'opponent_band':('Rating difference: weaker below -200, similar ±200, stronger above 200','pre-game'),
    'initial_seconds':('Initial clock in seconds','pre-game, nullable'),
    'increment_seconds':('Increment per move in seconds','pre-game, nullable'),
    'speed':('Clock class using initial plus 40 increments','pre-game'),
    'result':('Selected player result: win/draw/loss','post-game target'),
    'points':('Win=1, draw=0.5, loss=0','post-game target'),
    'opening':('Uploaded descriptive opening label','post-game descriptor; exclude from pre-game predictors'),
    'eco':('Uploaded opening code','post-game descriptor'),
    'ply_count':('Number of mainline half moves','post-game target'),
    'revision_sha256':('Checksum of the shared immutable game payload','provenance')}


def save(owner,library_id,snapshot_id,name,selected):
    if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('Give this study a name of up to 80 characters')
    selected=insights.filters(selected or {})
    with db.connect() as c:
        snap=db.snapshot(c,owner,library_id,snapshot_id);key=str(uuid4())
        c.execute('INSERT INTO study.saved_studies(id,owner_id,snapshot_id,name,filters) VALUES(%s,%s,%s,%s,%s)',(key,owner,snap['id'],name.strip(),Jsonb(selected)))
        return {'id':key,'name':name.strip(),'snapshot_id':str(snap['id']),'filters':selected}


def studies(owner):
    with db.connect() as c:
        return [dict(r) for r in c.execute('''SELECT t.id,t.name,t.filters,t.snapshot_id,s.version,s.library_id,l.player,t.created_at
            FROM study.saved_studies t JOIN study.library_snapshots s ON s.id=t.snapshot_id
            JOIN study.libraries l ON l.id=s.library_id WHERE t.owner_id=%s ORDER BY t.created_at DESC LIMIT 50''',(owner,)).fetchall()]


def prepare(owner,library_id=None,snapshot_id=None,study_id=None,selected=None,format='parquet',config=None):
    if format not in ('parquet','pgn','manifest','report'):raise ValueError('Choose Parquet, PGN, manifest or report')
    selected=insights.filters(selected or {})
    with db.connect() as c:
        if study_id:
            saved=c.execute('SELECT * FROM study.saved_studies WHERE owner_id=%s AND id=%s',(owner,db.identifier(study_id))).fetchone()
            if not saved:raise ValueError('Saved study not found in your account')
            original=c.execute('SELECT library_id FROM study.library_snapshots WHERE owner_id=%s AND id=%s',(owner,saved['snapshot_id'])).fetchone()
            library_id=original['library_id'];snapshot_id=saved['snapshot_id'];selected=saved['filters']
        snap=db.snapshot(c,owner,library_id,snapshot_id);clause,params=insights.where(owner,snap,selected)
        count=c.execute('SELECT count(*) AS n FROM study.workspace_game_facts f WHERE '+clause,params).fetchone()['n']
        manifest={'format_version':2,'player':snap['player'],'library_id':str(snap['library_id']),'snapshot_id':str(snap['id']),
            'version':snap['version'],'dataset_sha256':snap['dataset_sha256'],'rows':count,'filters':selected,
            'data_dictionary':{k:{'meaning':v[0],'availability':v[1]} for k,v in DICTIONARY.items()},
            'privacy':'Private account-owned study. Game IDs may identify public source games; exports are not anonymous.',
            'feature_rule':'Exclude all post-game fields when predicting before the first move.'}
        folder=db.root()/'exports';folder.mkdir(exist_ok=True)
        path=folder/(str(uuid4())+'.'+format)
        try:
            if format in ('manifest','report'):
                value=manifest if format=='manifest' else insights.report(owner,library_id,snap['id'],selected,config)
                path.write_text(json.dumps(value,indent=2,default=str,allow_nan=False)+'\n')
            else:
                with c.cursor(name='research_export') as cursor:
                    cursor.execute('SELECT f.*,r.payload,r.payload_sha256 FROM study.workspace_game_facts f JOIN study.game_revisions r ON r.id=f.revision_id WHERE '+clause+' ORDER BY f.game_date NULLS LAST,f.game_id',params)
                    if format=='pgn':
                        with path.open('w') as out:
                            for row in cursor:out.write(row['payload']['pgn']+'\n\n')
                    else:
                        schema=pa.schema([(name,pa.date32() if name=='game_date' else pa.int32() if name in ('player_rating','opponent_rating','ply_count') else
                            pa.int64() if name in ('initial_seconds','increment_seconds') else pa.float64() if name=='points' else pa.string()) for name in DICTIONARY],
                            metadata={b'chess_study_manifest':json.dumps(manifest,sort_keys=True).encode()})
                        with pq.ParquetWriter(path,schema,compression='zstd') as writer:
                            while batch:=cursor.fetchmany(512):
                                records=[]
                                for row in batch:
                                    record={k:row.get(k) for k in DICTIONARY}
                                    record.update(initial_seconds=row['payload']['initial_seconds'],increment_seconds=row['payload']['increment_seconds'],revision_sha256=row['payload_sha256'])
                                    records.append(record)
                                writer.write_table(pa.Table.from_pylist(records,schema=schema))
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                while block:=stream.read(65536):digest.update(block)
            types={'parquet':'application/vnd.apache.parquet','pgn':'application/x-chess-pgn','manifest':'application/json','report':'application/json'}
            return path,types[format],f'{snap["player"]}-study-v{snap["version"]}.'+('json' if format in ('manifest','report') else format),digest.hexdigest()
        except Exception:
            path.unlink(missing_ok=True);raise
