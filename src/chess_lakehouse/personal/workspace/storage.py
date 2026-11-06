"""Owner-scoped SQL access and shared immutable game payloads."""
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID
import hashlib
import json
import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@contextmanager
def connect():
    with psycopg.connect('',row_factory=dict_row,connect_timeout=5,
            options='-c statement_timeout=60000 -c idle_in_transaction_session_timeout=120000') as c:
        yield c


def identifier(value):
    try:return str(UUID(str(value)))
    except (ValueError,TypeError,AttributeError):raise ValueError('Invalid resource identifier') from None


def root():
    path=Path(os.environ.get('STUDY_ROOT','/study'))/'workspace'
    path.mkdir(parents=True,exist_ok=True)
    return path


def library(c,owner,library_id=None,player=None,create=False):
    if not library_id and not player:raise ValueError('Choose a player library first')
    if library_id:
        row=c.execute('SELECT * FROM study.libraries WHERE owner_id=%s AND id=%s',(owner,identifier(library_id))).fetchone()
    else:
        row=c.execute('SELECT * FROM study.libraries WHERE owner_id=%s AND player_key=%s',(owner,player.casefold())).fetchone()
        if not row and create:
            from uuid import uuid4
            c.execute('INSERT INTO study.libraries(id,owner_id,player,player_key) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,player_key) DO NOTHING',
                (str(uuid4()),owner,player,player.casefold()))
            return library(c,owner,player=player)
    if not row:raise ValueError('Library not found in your account')
    return row


def snapshot(c,owner,library_id,snapshot_id=None):
    lib=library(c,owner,library_id)
    key=snapshot_id or lib['active_snapshot']
    if not key:raise ValueError('Import games before opening this library')
    row=c.execute('SELECT s.*,l.player FROM study.library_snapshots s JOIN study.libraries l ON l.id=s.library_id WHERE s.owner_id=%s AND s.library_id=%s AND s.id=%s',
        (owner,lib['id'],identifier(key))).fetchone()
    if not row:raise ValueError('Collection version not found in your account')
    return row


def game(c,owner,snap,game_id):
    from ..pgn import valid_game_id
    if not valid_game_id(game_id):raise ValueError('Invalid game identifier')
    row=c.execute('''SELECT r.* FROM study.snapshot_games m JOIN study.game_revisions r ON r.id=m.revision_id
        WHERE m.owner_id=%s AND m.snapshot_id=%s AND m.game_id=%s''',(owner,snap['id'],game_id)).fetchone()
    if not row:raise ValueError('Game not found in this collection')
    return row


def canonical(row):
    # Imported annotations can change; identical semantic payloads are stored once.
    shared={k:v for k,v in row.items() if k not in ('player_color','player_rating','opponent_rating','opponent_band','outcome','points')}
    return json.dumps(shared,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def perspective(payload,player):
    row=dict(payload)
    color='white' if row['white'].casefold()==player.casefold() else 'black'
    if row[color].casefold()!=player.casefold():raise ValueError('Game does not contain the selected player')
    own=row[color+'_rating'];other=row[('black' if color=='white' else 'white')+'_rating']
    outcome='draw' if row['result']=='1/2-1/2' else 'win' if row['result']==('1-0' if color=='white' else '0-1') else 'loss'
    row.update(player_color=color,player_rating=own,opponent_rating=other,outcome=outcome,
        opponent_band='unknown' if own is None or other is None else 'stronger' if other-own>200 else 'weaker' if other-own < -200 else 'similar',
        points={'win':1.,'draw':.5,'loss':0.}[outcome])
    return row


def save_revision(c,owner,row):
    from uuid import uuid4
    text=canonical(row);sha=hashlib.sha256(text.encode()).hexdigest()
    existing=c.execute('SELECT id,positions_ready FROM study.game_revisions WHERE owner_id=%s AND game_id=%s AND payload_sha256=%s',
        (owner,row['game_id'],sha)).fetchone()
    if existing:
        if not existing['positions_ready']:index_positions(c,owner,existing['id'],row)
        return str(existing['id'])
    key=str(uuid4())
    result=c.execute('''INSERT INTO study.game_revisions(id,owner_id,game_id,payload_sha256,payload,
        game_date,white_name,black_name,white_rating,black_rating,game_result,speed,opening,eco,ply_count)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(owner_id,game_id,payload_sha256) DO UPDATE SET payload_sha256=EXCLUDED.payload_sha256 RETURNING id''',
        (key,owner,row['game_id'],sha,Jsonb(json.loads(text)),row['game_date'],row['white'],row['black'],row['white_rating'],
         row['black_rating'],row['result'],row['speed'],row['opening'],row['eco'],len(row['moves']))).fetchone()
    index_positions(c,owner,result['id'],row)
    return str(result['id'])


def index_positions(c,owner,revision,row):
    import chess
    from ..pgn import position_key
    # A row lock makes the build idempotent when an import and recovery worker meet.
    current=c.execute('SELECT positions_ready FROM study.game_revisions WHERE owner_id=%s AND id=%s FOR UPDATE',(owner,revision)).fetchone()
    if current['positions_ready']:return
    c.execute('DELETE FROM study.workspace_positions WHERE owner_id=%s AND revision_id=%s',(owner,revision))
    board=chess.Board(row['initial_fen'])
    with c.cursor().copy('COPY study.workspace_positions(owner_id,revision_id,ply,position_hash,next_uci,next_san) FROM STDIN') as copy:
        for ply,uci in enumerate(row['moves']):
            move=chess.Move.from_uci(uci)
            copy.write_row((owner,revision,ply,position_key(board),uci,board.san(move)));board.push(move)
        copy.write_row((owner,revision,len(row['moves']),position_key(board),None,None))
    c.execute('UPDATE study.game_revisions SET positions_ready=true WHERE owner_id=%s AND id=%s',(owner,revision))
