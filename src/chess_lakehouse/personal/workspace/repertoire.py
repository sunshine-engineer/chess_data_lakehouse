"""Full-collection, restartable exercise mining. One game per engine-worker turn.

Positive best score >=100cp and deterioration >=150cp are search-based exercise
criteria. Losing defensive alternatives are excluded. Solutions accept searched
moves within 30cp of the best score (and any immediate checkmate), not arbitrary
legal moves. No claim of tournament-quality puzzle uniqueness is made.
"""
from dataclasses import replace
from uuid import uuid4

import chess
from psycopg.types.json import Jsonb

from . import storage as db
from .. import engine
from ...runtime import load as runtime


def settings(config):
    """Derive the configured per-game mining budget without increasing the global engine limits."""
    return replace(config,engine_game_seconds=min(config.engine_game_seconds,runtime().workers.repertoire_game_seconds),engine_nodes=min(config.engine_nodes,10000))


def start(owner,value):
    """Create or resume an owner-scoped build covering every game in a pinned collection."""
    fallback=value.get('archive_fallback',True)
    if type(fallback) is not bool:raise ValueError('Archive fallback must be true or false')
    with db.connect() as c:
        snap=db.snapshot(c,owner,value.get('library_id'),value.get('snapshot_id'))
        c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('repertoire:'+str(owner),))
        row=c.execute('SELECT * FROM study.repertoire_builds WHERE owner_id=%s AND snapshot_id=%s',(owner,snap['id'])).fetchone()
        if row:
            c.execute("UPDATE study.repertoire_builds SET status='running',archive_fallback=%s,error=NULL,updated_at=now() WHERE id=%s",(fallback,row['id']))
            c.execute("UPDATE study.repertoire_tasks SET status='queued',error=NULL WHERE build_id=%s AND status='failed'",(row['id'],))
            key=row['id']
        else:
            key=str(uuid4())
            c.execute('INSERT INTO study.repertoire_builds(id,owner_id,snapshot_id,archive_fallback) VALUES(%s,%s,%s,%s)',(key,owner,snap['id'],fallback))
            c.execute('''INSERT INTO study.repertoire_tasks(build_id,source_key,revision_id,player)
                SELECT %s,m.game_id,m.revision_id,%s FROM study.snapshot_games m WHERE m.owner_id=%s AND m.snapshot_id=%s''',(key,snap['player'],owner,snap['id']))
    return status(owner,{'build_id':str(key)})


def owned(c,owner,key):
    """Resolve a repertoire build by both account and resource identifier."""
    row=c.execute('SELECT * FROM study.repertoire_builds WHERE owner_id=%s AND id=%s',(owner,db.identifier(key))).fetchone()
    if not row:raise ValueError('Repertoire build not found in your account')
    return row


def status(owner,value):
    """Report full build coverage, source stages, failures, partial searches and phase counts."""
    with db.connect() as c:
        if value.get('build_id'):build=owned(c,owner,value['build_id'])
        else:
            snap=db.snapshot(c,owner,value.get('library_id'),value.get('snapshot_id'))
            build=c.execute('SELECT * FROM study.repertoire_builds WHERE owner_id=%s AND snapshot_id=%s',(owner,snap['id'])).fetchone()
        if not build:return {'build':None,'counts':[],'phases':[]}
        counts=c.execute('SELECT status,source_month,count(*) AS games FROM study.repertoire_tasks WHERE build_id=%s GROUP BY status,source_month ORDER BY source_month NULLS FIRST,status',(build['id'],)).fetchall()
        phases=c.execute('SELECT phase,count(*) AS exercises FROM study.repertoire_exercises WHERE owner_id=%s AND build_id=%s GROUP BY phase ORDER BY phase',(owner,build['id'])).fetchall()
        build['partial_reviews']=c.execute("SELECT count(*) AS n FROM study.repertoire_tasks WHERE build_id=%s AND status='complete' AND coverage->>'complete_coverage'='false'",(build['id'],)).fetchone()['n']
    return dict(build=build,counts=counts,phases=phases,method='Every game in the pinned collection; then available archive frames, newest month first. One Stockfish thread; up to 30 seconds/game. Advantage >=1 pawn; missed score >=1.5 pawns. Phase is a ply/material heuristic. Pause takes effect after the current game; interactive requests run before the next game.')


def control(owner,value):
    """Pause or resume an owned repertoire build without deleting completed exercises."""
    if value.get('action') not in ('pause','resume'):raise ValueError('Choose pause or resume')
    with db.connect() as c:
        build=owned(c,owner,value.get('build_id'))
        c.execute('UPDATE study.repertoire_builds SET status=%s,updated_at=now() WHERE id=%s',('paused' if value['action']=='pause' else 'running',build['id']))
    return status(owner,value)


def phase(board,ply):
    """Classify a position using the documented opening-ply and remaining-material heuristic."""
    pieces=sum(len(board.pieces(p,c))*v for p,v in ((chess.KNIGHT,3),(chess.BISHOP,3),(chess.ROOK,5),(chess.QUEEN,9)) for c in (True,False))
    return 'opening' if ply<20 else 'endgame' if pieces<=20 else 'middlegame'


def extract(payload,evidence):
    """Yield legal missed-advantage exercises supported by the selected player's searched evidence."""
    positions={};board=chess.Board(payload['initial_fen'])
    # These boards are used only for legal root moves and phase/FEN labels.
    # Keep one board per position, without copying an ever-growing move stack.
    for ply,uci in enumerate(payload['moves']):positions[ply]=board.copy(stack=False);board.push_uci(uci)
    for candidate in evidence.get('candidates',[]):
        ply=candidate['ply']-1;board=positions.get(ply)
        if board is None or board.turn!=(payload['player_color']=='white') or candidate.get('loss_cp',0)<150:continue
        alternatives=candidate.get('alternatives',[])
        if not alternatives:continue
        best=alternatives[0];score=best.get('score_value',best.get('score_cp'))
        if score is None or score<100:continue
        accepted=[]
        for line in alternatives:
            value=line.get('score_value',line.get('score_cp'))
            if value is not None and value>=score-30 and line.get('pv_uci'):
                move=chess.Move.from_uci(line['pv_uci'][0])
                if move in board.legal_moves and move.uci()!=candidate['played_uci']:accepted.append(move.uci())
        if not accepted:continue
        yield dict(ply=ply,phase=phase(board,ply),fen=board.fen(),evidence=dict(candidate,accepted=accepted,player_color=payload['player_color'],engine=evidence.get('engine'),budget=evidence.get('budget'),coverage=evidence.get('coverage_explanation')))


def run_one(config):
    """Process one queued repertoire game and atomically retain its exercises and coverage."""
    with db.connect() as c:
        task=c.execute('''SELECT t.*,b.owner_id,b.snapshot_id FROM study.repertoire_tasks t JOIN study.repertoire_builds b ON b.id=t.build_id
            WHERE b.status='running' AND t.status='queued' ORDER BY b.updated_at,
            EXISTS(SELECT 1 FROM study.workspace_analysis a WHERE a.owner_id=b.owner_id AND a.revision_id=t.revision_id AND a.player=t.player AND a.status='complete') DESC,
            t.source_month NULLS FIRST,t.created_at,t.source_key LIMIT 1 FOR UPDATE OF t SKIP LOCKED''').fetchone()
        if not task:
            c.execute("""UPDATE study.repertoire_builds b SET source_stage=CASE WHEN archive_fallback THEN 'archive' ELSE 'finished' END,
                status=CASE WHEN archive_fallback THEN status ELSE 'complete' END WHERE status='running' AND source_stage IN ('personal','finished')
                AND NOT EXISTS(SELECT 1 FROM study.repertoire_tasks t WHERE t.build_id=b.id AND t.status IN ('queued','running'))""")
            return False
        c.execute("UPDATE study.repertoire_tasks SET status='running' WHERE build_id=%s AND source_key=%s",(task['build_id'],task['source_key']))
        revision=c.execute('SELECT payload FROM study.game_revisions WHERE owner_id=%s AND id=%s',(task['owner_id'],task['revision_id'])).fetchone() if task['revision_id'] else None
        cached=c.execute("SELECT evidence FROM study.workspace_analysis WHERE owner_id=%s AND revision_id=%s AND player=%s AND status='complete' ORDER BY finished_at DESC LIMIT 1",(task['owner_id'],task['revision_id'],task['player'])).fetchone() if revision else None
    try:
        payload=db.perspective(revision['payload'],task['player']) if revision else task['payload']
        evidence=cached['evidence'] if cached else engine.analyze(payload,settings(config))
        exercises=list(extract(payload,evidence))
        with db.connect() as c:
            for item in exercises:
                item['evidence']['source']=dict(game_id=payload.get('game_id'),month=task['source_month'],record=task['source_key'])
                c.execute('''INSERT INTO study.repertoire_exercises(id,owner_id,build_id,source_key,ply,phase,fen,evidence)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(build_id,source_key,ply) DO NOTHING''',
                    (str(uuid4()),task['owner_id'],task['build_id'],task['source_key'],item['ply'],item['phase'],item['fen'],Jsonb(item['evidence'])))
            coverage={k:evidence.get(k) for k in ('complete_coverage','player_moves_reviewed','player_moves_total','analyzed_plies','total_plies','budget')}
            c.execute("UPDATE study.repertoire_tasks SET status='complete',payload=NULL,coverage=%s WHERE build_id=%s AND source_key=%s",(Jsonb(coverage),task['build_id'],task['source_key']))
            c.execute('UPDATE study.repertoire_builds SET updated_at=now(),error=NULL WHERE id=%s',(task['build_id'],))
    except Exception as exc:
        import logging
        logging.exception('Repertoire game failed')
        with db.connect() as c:c.execute("UPDATE study.repertoire_tasks SET status='failed',error=%s WHERE build_id=%s AND source_key=%s",(str(exc)[:300] if isinstance(exc,ValueError) else 'Review failed; resume to retry.',task['build_id'],task['source_key']))
    return True


def listing(owner,value):
    """Return a bounded phase-filtered repertoire page and the owner's attempt statistics."""
    phase_filter=value.get('phase','')
    if phase_filter not in ('','opening','middlegame','endgame'):raise ValueError('Choose an exercise phase')
    offset=value.get('offset',0)
    if type(offset) is not int or not 0<=offset<=10000000:raise ValueError('Invalid exercise page')
    with db.connect() as c:
        build=owned(c,owner,value.get('build_id'));params=[owner,build['id']]
        clause='e.owner_id=%s AND e.build_id=%s'
        if phase_filter:clause+=' AND e.phase=%s';params.append(phase_filter)
        rows=c.execute('''SELECT e.id,e.source_key,e.ply,e.phase,e.fen,t.source_month,
            count(a.attempt_id) AS attempts,count(a.attempt_id) FILTER(WHERE a.correct) AS successes
            FROM study.repertoire_exercises e JOIN study.repertoire_tasks t ON t.build_id=e.build_id AND t.source_key=e.source_key
            LEFT JOIN study.repertoire_attempts a ON a.owner_id=e.owner_id AND a.exercise_id=e.id
            WHERE '''+clause+''' GROUP BY e.id,t.source_month ORDER BY e.created_at,e.id LIMIT 21 OFFSET %s''',params+[offset]).fetchall()
    return {'exercises':rows[:20],'has_more':len(rows)>20,'offset':offset,'snapshot_id':build['snapshot_id']}


def exercise(owner,value,reveal=False):
    """Return an owner-checked playable position, revealing searched solutions only when requested."""
    from .board import describe
    with db.connect() as c:row=c.execute('''SELECT e.*,b.snapshot_id,s.library_id,t.source_month FROM study.repertoire_exercises e
        JOIN study.repertoire_builds b ON b.id=e.build_id AND b.owner_id=e.owner_id
        JOIN study.library_snapshots s ON s.id=b.snapshot_id AND s.owner_id=e.owner_id
        JOIN study.repertoire_tasks t ON t.build_id=e.build_id AND t.source_key=e.source_key
        WHERE e.owner_id=%s AND e.id=%s''',(owner,db.identifier(value.get('exercise_id')))).fetchone()
    if not row:raise ValueError('Exercise not found in your account')
    result=dict(describe(chess.Board(row['fen'])),id=row['id'],phase=row['phase'],ply=row['ply'],source_key=row['source_key'],
        source_month=row['source_month'],library_id=str(row['library_id']),snapshot_id=str(row['snapshot_id']),
        game_id=row['source_key'] if not row['source_month'] else row['evidence'].get('source',{}).get('game_id'))
    if reveal:result['evidence']=row['evidence']
    return result


def attempt(owner,value):
    """Grade a legal move against searched solutions and save an idempotent private attempt."""
    row=exercise(owner,value,True);board=chess.Board(row['fen'])
    try:move=chess.Move.from_uci(value.get('move',''))
    except (ValueError,TypeError):raise ValueError('Choose a legal move') from None
    if move not in board.legal_moves:raise ValueError('That move is not legal here')
    san=board.san(move);board.push(move);correct=move.uci() in row['evidence']['accepted'] or board.is_checkmate()
    with db.connect() as c:
        key=db.identifier(value.get('attempt_id'))
        c.execute('INSERT INTO study.repertoire_attempts(owner_id,attempt_id,exercise_id,move,correct) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(owner,key,row['id'],move.uci(),correct))
        old=c.execute('SELECT exercise_id,move FROM study.repertoire_attempts WHERE owner_id=%s AND attempt_id=%s',(owner,key)).fetchone()
        if str(old['exercise_id'])!=str(row['id']) or old['move']!=move.uci():raise ValueError('This attempt ID has already been used')
    return dict(correct=correct,fen=board.fen(),san=san,evidence=row['evidence'],saved=True,feedback='Accepted searched move.' if correct else 'Legal move, but outside the searched solution set. Try again or compare the continuation; other good moves may exist.')
