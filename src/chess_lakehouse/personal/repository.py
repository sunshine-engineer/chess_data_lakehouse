"""Study-only PostgreSQL access and immutable private Parquet publications."""
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import UUID,uuid4

import chess
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pyarrow as pa
import pyarrow.parquet as pq

from .pgn import PAYLOAD_SCHEMA,dataset_hash,parse,position_key,valid_game_id


@contextmanager
def connect():
    with psycopg.connect('',row_factory=dict_row,connect_timeout=5,
            options='-c statement_timeout=20000 -c idle_in_transaction_session_timeout=60000') as conn:
        yield conn


def uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError,TypeError,AttributeError):
        raise ValueError('invalid study version or resource ID') from None


def root():
    path=Path(os.environ.get('STUDY_ROOT','/study'))
    path.mkdir(parents=True,exist_ok=True)
    return path


def workspace(conn,settings,create=False):
    row=conn.execute('SELECT * FROM study.workspaces WHERE player_normalized=%s AND month=%s',
        (settings.normalized_player,settings.month_date)).fetchone()
    if not row and create:
        row=conn.execute('INSERT INTO study.workspaces(id,player,player_normalized,month) VALUES(%s,%s,%s,%s) RETURNING *',
            (str(uuid4()),settings.player,settings.normalized_player,settings.month_date)).fetchone()
    return row


def version(conn,settings,identifier=None):
    w=workspace(conn,settings)
    if not w or not w['active_import']:
        raise ValueError('import the approved personal PGN before requesting a study')
    identifier=uuid(identifier or w['active_import'])
    row=conn.execute('SELECT * FROM study.imports WHERE id=%s AND workspace_id=%s',(identifier,w['id'])).fetchone()
    if not row:
        raise ValueError('study version does not belong to the approved player-month')
    return row


def payloads(import_row):
    folder=root()/uuid(import_row['id'])
    file=folder/'games.parquet'
    manifest=import_row['manifest']
    if hashlib.sha256(file.read_bytes()).hexdigest()!=manifest['files']['games.parquet']:
        raise ValueError('personal Parquet checksum mismatch')
    rows=pq.read_table(file,schema=PAYLOAD_SCHEMA).to_pylist()
    for row in rows:
        row['identity_source']=row.get('identity_source') or 'source'
        row['metadata_defaults']=row.get('metadata_defaults') or []
    return rows


def payload(import_row,game_id):
    if not valid_game_id(game_id):
        raise ValueError('invalid game ID')
    rows=payloads(import_row)
    return next((r for r in rows if r['game_id']==game_id),None)


def publish(content,settings,player,month,mode='append'):
    if player.casefold().strip()!=settings.normalized_player or month!=settings.month:
        raise ValueError('player and month must match the explicit personal-study approval')
    if mode not in ('append','replace'):
        raise ValueError('import mode must be append or replace')
    incoming,duplicate_count=parse(content,settings)
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext('personal-study-publication'))")
        c.execute("SELECT pg_advisory_xact_lock(hashtext('personal-import:' || %s))",(settings.normalized_player+settings.month,))
        w=workspace(c,settings,create=True)
        previous=version(c,settings) if w['active_import'] else None
        existing=payloads(previous) if previous and mode=='append' else []
        merged={r['game_id']:r for r in existing}
        corrected=sum(r['game_id'] in merged and r!=merged[r['game_id']] for r in incoming)
        repeated=sum(r['game_id'] in merged and r==merged[r['game_id']] for r in incoming)
        merged.update({r['game_id']:r for r in incoming})
        rows=sorted(merged.values(),key=lambda r:r['game_id'])
        total_plies=sum(len(r['moves']) for r in rows)
        if len(rows)>settings.max_games or total_plies>settings.max_total_plies:
            raise ValueError('combined personal version exceeds the game or ply limit')
        sha=dataset_hash(rows)
        if previous and sha==previous['dataset_sha256']:
            return dict(import_id=str(previous['id']),version=previous['version'],games=len(rows),
                duplicate_games=duplicate_count+repeated,corrected_games=0,unchanged=True)
        next_version=1 if previous is None else previous['version']+1
        if next_version>settings.max_versions:
            raise ValueError('personal version limit reached; archive this workspace before further imports')
        identifier=str(uuid4())
        folder=root()/identifier
        temporary=root()/('.'+identifier)
        temporary.mkdir(mode=0o700)
        committed=False
        commit_started=False
        try:
            (temporary/'source.pgn').write_bytes(content)
            pq.write_table(pa.Table.from_pylist(rows,schema=PAYLOAD_SCHEMA),temporary/'games.parquet',
                compression='zstd',row_group_size=1)
            manifest=dict(format_version=2,import_id=identifier,parent_id=str(previous['id']) if previous else None,
                player=settings.player,month=settings.month,game_count=len(rows),ply_count=total_plies,
                metadata_defaults={r['game_id']:r['metadata_defaults'] for r in rows if r['metadata_defaults']},
                local_identity_games=sum(r['identity_source']=='local_content' for r in rows),
                dataset_sha256=sha,files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in temporary.iterdir()})
            (temporary/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
            temporary.rename(folder)
            c.execute('''INSERT INTO study.imports(id,workspace_id,version,parent_id,source_sha256,dataset_sha256,
                game_count,ply_count,artifact_path,manifest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (identifier,w['id'],next_version,previous['id'] if previous else None,
                 hashlib.sha256(content).hexdigest(),sha,len(rows),total_plies,identifier,Jsonb(manifest)))
            with c.cursor().copy('''COPY study.games(import_id,game_id,game_date,player_color,player_rating,
                opponent_rating,opponent_band,result,points,speed,eco,opening,ply_count,payload_index) FROM STDIN''') as copy:
                for i,r in enumerate(rows):
                    copy.write_row((identifier,r['game_id'],r['game_date'],r['player_color'],r['player_rating'],
                        r['opponent_rating'],r['opponent_band'],r['outcome'],r['points'],r['speed'],r['eco'],r['opening'],len(r['moves']),i))
            with c.cursor().copy('COPY study.positions(import_id,game_id,ply,position_hash,next_uci,next_san) FROM STDIN') as copy:
                for r in rows:
                    board=chess.Board(r['initial_fen'])
                    for ply,uci in enumerate(r['moves']):
                        move=chess.Move.from_uci(uci)
                        copy.write_row((identifier,r['game_id'],ply,position_key(board),uci,board.san(move)))
                        board.push(move)
                    copy.write_row((identifier,r['game_id'],len(r['moves']),position_key(board),None,None))
            c.execute('UPDATE study.workspaces SET active_import=%s WHERE id=%s',(identifier,w['id']))
            commit_started=True
            c.commit()
            committed=True
        finally:
            shutil.rmtree(temporary,ignore_errors=True)
            # An uncertain COMMIT outcome must never remove potentially published files.
            if not committed and not commit_started:
                shutil.rmtree(folder,ignore_errors=True)
        return dict(import_id=identifier,version=next_version,games=len(rows),
            duplicate_games=duplicate_count+repeated,corrected_games=corrected,unchanged=False,
            defaulted_games=sum(bool(r['metadata_defaults']) for r in rows))
