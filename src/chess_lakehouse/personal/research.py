"""Saved immutable cohorts and self-describing bounded research downloads."""
import hashlib
import io
import json
from uuid import uuid4

from psycopg.types.json import Jsonb
import pyarrow as pa
import pyarrow.parquet as pq

from . import repository as db
from .reports import select_games,validate_filters

DICTIONARY={
    'game_id':{'meaning':'Source game ID or local_ plus content SHA-256 when source identity is absent','availability':'identifier; not a model feature'},
    'game_date':{'meaning':'UTC game-start date','availability':'pre-game'},
    'player_color':{'meaning':'Approved player color','availability':'pre-game'},
    'player_rating':{'meaning':'Player header rating','availability':'pre-game; nullable'},
    'opponent_rating':{'meaning':'Opponent header rating','availability':'pre-game; nullable'},
    'opponent_band':{'meaning':'Opponent minus player rating: weaker < -200, similar ±200, stronger >200','availability':'pre-game; unknown if missing'},
    'result':{'meaning':'Player-perspective win/draw/loss','availability':'post-game target'},
    'points':{'meaning':'Win=1, draw=0.5, loss=0','availability':'post-game target'},
    'speed':{'meaning':'Clock class from initial + 40×increment','availability':'pre-game'},
    'eco':{'meaning':'Uploaded ECO code','availability':'post-game descriptive'},
    'opening':{'meaning':'Uploaded opening label; unclassified if absent','availability':'post-game descriptive'},
    'ply_count':{'meaning':'Mainline half-moves','availability':'post-game target'}
}


def save(settings,identifier,name,filters):
    if not isinstance(name,str) or not name.strip() or len(name)>80:
        raise ValueError('cohort name must contain 1–80 characters')
    filters=validate_filters(filters,settings)
    with db.connect() as c:
        v=db.version(c,settings,identifier)
        c.execute("SELECT pg_advisory_xact_lock(hashtext('study-cohorts:' || %s))",(str(v['workspace_id']),))
        count=c.execute('SELECT count(*) AS n FROM study.cohorts WHERE import_id IN (SELECT id FROM study.imports WHERE workspace_id=%s)',(v['workspace_id'],)).fetchone()['n']
        if count>=settings.max_cohorts:
            raise ValueError('saved-cohort limit reached')
        identifier=str(uuid4())
        c.execute('INSERT INTO study.cohorts(id,import_id,name,filters) VALUES(%s,%s,%s,%s)',
            (identifier,v['id'],name.strip(),Jsonb(filters)))
        return dict(id=identifier,import_id=str(v['id']),name=name.strip(),filters=filters)


def export(settings,identifier=None,cohort_id=None,format='parquet',filters=None):
    filters=validate_filters(filters or {},settings)
    with db.connect() as c:
        if cohort_id:
            saved=c.execute('SELECT * FROM study.cohorts WHERE id=%s',(db.uuid(cohort_id),)).fetchone()
            if not saved:
                raise ValueError('unknown cohort')
            identifier=saved['import_id'];filters=saved['filters']
        v=db.version(c,settings,identifier)
        rows=select_games(c,v['id'],filters)
        manifest=dict(format_version=1,import_id=str(v['id']),version=v['version'],player=settings.player,
            month=settings.month,dataset_sha256=v['dataset_sha256'],filters=filters,rows=len(rows),
            data_dictionary=DICTIONARY,privacy='Private personal study; game IDs may identify public source games.')
        selected_ids={row['game_id'] for row in rows}
        manifest['metadata_defaults']={key:value for key,value in v['manifest'].get('metadata_defaults',{}).items() if key in selected_ids}
        manifest['date_default']='Missing exact dates use the approved month first day as a labelled placeholder, not a verified play date.'
        if format=='report':
            from .reports import report
            value=report(settings,str(v['id']),filters)
            content=(json.dumps(value,indent=2,default=str,allow_nan=False)+'\n').encode()
            mime='application/json';extension='report.json'
        elif format=='manifest':
            content=(json.dumps(manifest,indent=2,default=str)+'\n').encode()
            mime='application/json';extension='json'
        elif format=='pgn':
            selected={r['game_id'] for r in rows}
            content=('\n\n'.join(r['pgn'] for r in db.payloads(v) if r['game_id'] in selected)+'\n').encode()
            mime='application/x-chess-pgn';extension='pgn'
        elif format=='parquet':
            schema=pa.schema([(name,pa.int32() if name in ('player_rating','opponent_rating','ply_count') else
                pa.float64() if name=='points' else pa.string()) for name in DICTIONARY])
            records=[{k:str(r[k]) if k=='game_date' else r[k] for k in DICTIONARY} for r in rows]
            table=pa.Table.from_pylist(records,schema=schema)
            table=table.replace_schema_metadata({b'chess_study_manifest':json.dumps(manifest,default=str,sort_keys=True).encode()})
            stream=io.BytesIO();pq.write_table(table,stream,compression='zstd')
            content=stream.getvalue();mime='application/vnd.apache.parquet';extension='parquet'
        else:
            raise ValueError('export format must be parquet, pgn, report or manifest')
        return content,mime,f'personal-study-v{v["version"]}.{extension}',hashlib.sha256(content).hexdigest()
