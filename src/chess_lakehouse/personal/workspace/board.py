"""Legal move exploration, private preferences/variations and bounded engine jobs.

Every trial starts from an owner-checked immutable game position. Client supplied
FENs are never accepted for private analysis. The imported mainline is not edited.
"""
import hashlib
import json
from uuid import uuid4

import chess
import chess.engine
from psycopg.types.json import Jsonb

from . import storage as db,analysis
from .. import engine

DEFAULTS = dict(theme='forest',pieces='classic',size='comfortable',orientation='player',coordinates=True,highlights=True)
PROFILES = {'quick':(10000,.3),'balanced':(50000,1.),'deeper':(150000,3.)}


def preferences(owner,value=None):
    """Read or validate and save the account's supported board appearance settings."""
    if value is not None:
        if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('Unknown board preference')
        value={**DEFAULTS,**value}
        for key,allowed in {'theme':('forest','walnut','slate'),'pieces':('classic','outline'),
                'size':('compact','comfortable','large'),'orientation':('player','white','black')}.items():
            if value[key] not in allowed:raise ValueError('Choose a supported '+key)
        if any(type(value[k]) is not bool for k in ('coordinates','highlights')):raise ValueError('Board toggles must be boolean')
    with db.connect() as c:
        if value is not None:c.execute('''INSERT INTO study.board_preferences(owner_id,preferences) VALUES(%s,%s)
            ON CONFLICT(owner_id) DO UPDATE SET preferences=EXCLUDED.preferences,updated_at=now()''',(owner,Jsonb(value)))
        row=c.execute('SELECT preferences FROM study.board_preferences WHERE owner_id=%s',(owner,)).fetchone()
    return row['preferences'] if row else dict(DEFAULTS)


def resolve(c,owner,value):
    """Rebuild an owner-checked played position and validate a bounded legal trial line."""
    snap=db.snapshot(c,owner,value.get('library_id'),value.get('snapshot_id'))
    row=db.game(c,owner,snap,value.get('game_id'));ply=value.get('ply',0)
    if type(ply) is not int or not 0<=ply<=row['ply_count']:raise ValueError('Select a played position')
    board=analysis.chess_board(row['payload'],ply)
    moves=value.get('moves',[])
    if not isinstance(moves,list) or len(moves)>80:raise ValueError('A trial line can contain up to 80 half-moves')
    sans=[]
    for text in moves:
        if not isinstance(text,str):raise ValueError('Moves must use UCI notation')
        try:move=chess.Move.from_uci(text)
        except ValueError:raise ValueError('Invalid chess move') from None
        if move not in board.legal_moves or board.is_game_over():raise ValueError('That move is not legal in this position')
        sans.append(board.san(move));board.push(move)
    return board,row,ply,moves,sans


def describe(board):
    """Describe a board's FEN, side to move, legal choices and terminal result."""
    return dict(fen=board.fen(),turn='white' if board.turn else 'black',check=chess.square_name(board.king(board.turn)) if board.is_check() else None,
        legal_moves=[] if board.is_game_over() else [m.uci() for m in board.legal_moves],
        result=board.result() if board.is_game_over() else None)


def position(owner,value):
    """Return legal board state and saved variations for an owned game position."""
    with db.connect() as c:
        board,row,ply,moves,sans=resolve(c,owner,value)
        saved=c.execute('SELECT id,ply,moves,label FROM study.board_variations WHERE owner_id=%s AND revision_id=%s ORDER BY created_at DESC LIMIT 30',
            (owner,row['id'])).fetchall()
    return dict(describe(board),moves=moves,san=sans,variations=saved,ply=ply)


def save(owner,value):
    """Save a named legal trial variation separately from the immutable played game."""
    label=value.get('label','').strip() if isinstance(value.get('label',''),str) else ''
    if not 1<=len(label)<=80:raise ValueError('Name the variation using 1–80 characters')
    with db.connect() as c:
        _,row,ply,moves,_=resolve(c,owner,value)
        if not moves:raise ValueError('Try at least one move before saving a variation')
        c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('board:'+str(owner),))
        if c.execute('SELECT count(*) AS n FROM study.board_variations WHERE owner_id=%s AND revision_id=%s',(owner,row['id'])).fetchone()['n']>=30:
            raise ValueError('This game has 30 saved variations; remove one before adding another')
        key=str(uuid4());c.execute('INSERT INTO study.board_variations(id,owner_id,revision_id,ply,moves,label) VALUES(%s,%s,%s,%s,%s,%s)',(key,owner,row['id'],ply,Jsonb(moves),label))
    return {'id':key}


def remove(owner,value):
    """Remove only a saved variation belonging to the authenticated account."""
    with db.connect() as c:c.execute('DELETE FROM study.board_variations WHERE owner_id=%s AND id=%s',(owner,db.identifier(value.get('id'))))
    return {'deleted':True}


def budget(config,profile):
    """Resolve an explicit search profile and fingerprint its implementation and bounded settings."""
    if profile not in PROFILES:raise ValueError('Choose quick, balanced or deeper analysis')
    binary,base,_=engine.settings(config)
    nodes,seconds=PROFILES[profile]
    values=dict(binary_sha256=base['binary_sha256'],threads=1,hash_mb=32,nodes=nodes,seconds=seconds,
        code_sha256=hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest())
    return binary,values,hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def enqueue(owner,value,config):
    """Queue or reuse a position search bound to its full legal game-history context."""
    profile=value.get('profile','balanced');_,options,fingerprint=budget(config,profile)
    with db.connect() as c:
        board,revision,ply,moves,_=resolve(c,owner,value)
        context={'initial_fen':revision['payload']['initial_fen'],'moves':revision['payload']['moves'][:ply]+moves}
        fingerprint=search_key(fingerprint,context)
        c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('analysis:'+str(owner),))
        existing=c.execute('SELECT * FROM study.position_analysis WHERE owner_id=%s AND fen=%s AND settings_hash=%s',(owner,board.fen(),fingerprint)).fetchone()
        if existing and existing['status']!='failed':return existing
        active=c.execute("SELECT (SELECT count(*) FROM study.position_analysis WHERE owner_id=%s AND status IN ('queued','running'))+(SELECT count(*) FROM study.workspace_analysis WHERE owner_id=%s AND status IN ('queued','running')) AS n",(owner,owner)).fetchone()['n']
        if active>=config.engine_queue_limit:raise ValueError('Finish a queued analysis before adding more')
        if existing:
            c.execute("UPDATE study.position_analysis SET status='queued',error=NULL,finished_at=NULL WHERE id=%s",(existing['id'],));return {'id':existing['id'],'status':'queued'}
        key=str(uuid4());c.execute("INSERT INTO study.position_analysis(id,owner_id,fen,settings_hash,profile,status,evidence,context) VALUES(%s,%s,%s,%s,%s,'queued',%s,%s)",
            (key,owner,board.fen(),fingerprint,profile,Jsonb({'budget':options}),Jsonb(context)))
    return {'id':key,'status':'queued'}


def result(owner,key):
    """Read a position-search job exclusively within its requesting account."""
    with db.connect() as c:row=c.execute('SELECT * FROM study.position_analysis WHERE owner_id=%s AND id=%s',(owner,db.identifier(key))).fetchone()
    if not row:raise ValueError('Analysis not found in your account')
    return row


def search_key(fingerprint,context):
    """Bind a search fingerprint to the initial position and complete played/trial history."""
    return hashlib.sha256(json.dumps([fingerprint,context],sort_keys=True).encode()).hexdigest()


def run_one(config):
    """Process one queued legal position search and persist its bounded engine evidence."""
    with db.connect() as c:
        row=c.execute("SELECT * FROM study.position_analysis WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not row:return False
        c.execute("UPDATE study.position_analysis SET status='running' WHERE id=%s",(row['id'],))
    try:
        binary,options,fingerprint=budget(config,row['profile'])
        if not row['context'] or row['settings_hash']!=search_key(fingerprint,row['context']):raise ValueError('Engine settings changed; request this position again')
        board=chess.Board(row['context']['initial_fen'])
        for uci in row['context']['moves']:board.push_uci(uci)
        if board.fen()!=row['fen']:raise ValueError('Position context changed; request this position again')
        with chess.engine.SimpleEngine.popen_uci(str(binary),timeout=5) as search:
            search.configure({'Threads':1,'Hash':32})
            info=search.analyse(board,chess.engine.Limit(nodes=options['nodes'],time=options['seconds']))
            score=info['score'].white();pv=[];uci=[]
            for move in info.get('pv',[])[:12]:
                if move not in board.legal_moves:break
                pv.append(board.san(move));uci.append(move.uci());board.push(move)
            evidence=dict(engine=search.id.get('name','Stockfish'),budget=options,cp=score.score(),mate=score.mate(),
                depth=info.get('depth'),nodes=info.get('nodes'),pv_san=pv,pv_uci=uci,
                explanation='White-centric bounded search. Positive favors White; mate is shown separately. This is an estimate, not proof of a best move.')
        with db.connect() as c:c.execute("UPDATE study.position_analysis SET status='complete',evidence=%s,finished_at=now() WHERE id=%s",(Jsonb(evidence),row['id']))
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else 'Position search failed; request it again'
        with db.connect() as c:c.execute("UPDATE study.position_analysis SET status='failed',error=%s,finished_at=now() WHERE id=%s",(message,row['id']))
    return True
