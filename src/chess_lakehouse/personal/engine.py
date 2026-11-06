"""Explicit single-worker Stockfish review jobs with fixed resource budgets."""
from dataclasses import asdict
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import chess
import chess.engine
from psycopg.types.json import Jsonb

from . import repository as db


def settings(settings):
    binary=Path(os.environ.get('STOCKFISH_PATH','/usr/games/stockfish'))
    if not binary.is_file():
        raise ValueError('Stockfish is unavailable in this study-service image')
    values=dict(binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        analysis_version=2,analysis_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        threads=1,hash_mb=settings.engine_hash_mb,nodes=settings.engine_nodes,
        game_seconds=settings.engine_game_seconds,review_loss_cp=150)
    fingerprint=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    return binary,values,fingerprint


def enqueue(config,identifier,game_id):
    _,options,fingerprint=settings(config)
    with db.connect() as c:
        v=db.version(c,config,identifier)
        if not c.execute('SELECT 1 FROM study.games WHERE import_id=%s AND game_id=%s',(v['id'],game_id)).fetchone():
            raise ValueError('game does not belong to this personal version')
        c.execute("SELECT pg_advisory_xact_lock(hashtext('personal-analysis:' || %s))",(str(v['id']),))
        existing=c.execute('SELECT * FROM study.analysis_jobs WHERE import_id=%s AND game_id=%s AND settings_hash=%s',
            (v['id'],game_id,fingerprint)).fetchone()
        if existing:
            if existing['status']=='failed':
                c.execute("UPDATE study.analysis_jobs SET status='queued',error=NULL,started_at=NULL,finished_at=NULL WHERE id=%s",(existing['id'],))
                existing['status']='queued'
            return dict(id=str(existing['id']),status=existing['status'],cached=existing['status']=='complete')
        count=c.execute('SELECT count(DISTINCT game_id) AS n FROM study.analysis_jobs WHERE import_id=%s',(v['id'],)).fetchone()['n']
        known=c.execute('SELECT 1 FROM study.analysis_jobs WHERE import_id=%s AND game_id=%s LIMIT 1',(v['id'],game_id)).fetchone()
        if count>=config.max_analyzed_games and not known:
            raise ValueError('this version has reached its three-game engine review budget')
        job_id=str(uuid4())
        c.execute('''INSERT INTO study.analysis_jobs(id,import_id,game_id,status,settings_hash,evidence)
            VALUES(%s,%s,%s,'queued',%s,%s)''',(job_id,v['id'],game_id,fingerprint,Jsonb({'budget':options})))
        return dict(id=job_id,status='queued',cached=False)


def analyze(row,config):
    binary,options,_=settings(config)
    start=time.monotonic()
    board=chess.Board(row['initial_fen'])
    positions=[board.copy()]
    for uci in row['moves']:
        board.push_uci(uci);positions.append(board.copy())
    scores=[]
    with chess.engine.SimpleEngine.popen_uci(str(binary),timeout=5) as engine:
        engine.configure({'Threads':1,'Hash':config.engine_hash_mb})
        name=engine.id.get('name','Stockfish')
        for board in positions:
            remaining=config.engine_game_seconds-(time.monotonic()-start)
            if remaining<=.1:
                break
            info=engine.analyse(board,chess.engine.Limit(nodes=config.engine_nodes,time=min(.25,remaining)),
                info=chess.engine.INFO_ALL)
            score=info['score'].white()
            pv=info.get('pv',[])[:8]
            line=board.copy();san=[];uci=[]
            for move in pv:
                if move not in line.legal_moves:
                    break
                san.append(line.san(move));uci.append(move.uci());line.push(move)
            scores.append(dict(cp=score.score(mate_score=100000),mate=score.mate(),
                depth=info.get('depth'),nodes=info.get('nodes'),pv_uci=uci,pv_san=san))
    candidates=[]
    sign=1 if row['player_color']=='white' else -1
    for i in range(min(len(row['moves']),len(scores)-1)):
        before=positions[i]
        if ('white' if before.turn else 'black')!=row['player_color']:
            continue
        a,b=scores[i],scores[i+1]
        loss=max(0,int(sign*(a['cp']-b['cp'])))
        suggested=a['pv_uci'][0] if a['pv_uci'] else None
        if loss<150 or not suggested or suggested==row['moves'][i]:
            continue
        played_san=before.san(chess.Move.from_uci(row['moves'][i]))
        suggested_san=before.san(chess.Move.from_uci(suggested))
        mate_transition=a['mate'] is not None or b['mate'] is not None
        change='a changed mating evaluation' if mate_transition else f'an evaluation drop of about {loss/100:.2f} pawns'
        candidates.append(dict(ply=i+1,played_uci=row['moves'][i],played_san=played_san,
            suggested_uci=suggested,suggested_san=suggested_san,loss_cp=loss,
            before_cp=a['cp'],after_cp=b['cp'],before_mate=a['mate'],after_mate=b['mate'],
            depth_before=a['depth'],depth_after=b['depth'],pv_uci=a['pv_uci'],pv_san=a['pv_san'],
            summary=f'After {played_san}, the search recorded {change}; compare {suggested_san}.',
            practice='Replay the position before this move. Compare checks, captures and threats, then examine the engine line.'))
        item=candidates[-1]
        actual_after=positions[i+1]
        reply=chess.Move.from_uci(b['pv_uci'][0]) if b['pv_uci'] else None
        if reply and reply in actual_after.legal_moves:
            reply_san=actual_after.san(reply)
            continuation=actual_after.copy();continuation.push(reply)
            if continuation.is_checkmate():
                item['motif']='Allows checkmate in one'
                item['explanation']=f'After {played_san}, {reply_san} is a legal reply that checkmates. Practice checking the opponent’s forcing replies before committing to the move.'
            elif actual_after.is_capture(reply):
                captured=actual_after.piece_at(reply.to_square)
                piece=chess.piece_name(captured.piece_type) if captured else 'pawn'
                item['motif']='Material capture in the engine reply'
                item['explanation']=f'The engine continuation starts with {reply_san}, capturing a {piece}. Review whether the compensation is sufficient; the capture alone does not prove a material blunder.'
            elif actual_after.gives_check(reply):
                item['motif']='Checking reply in the engine line'
                item['explanation']=f'The engine reply {reply_san} gives check. Compare the forcing reply with the alternative before your move.'
        item.setdefault('motif','Evaluation-loss review candidate')
        item.setdefault('explanation','The bounded engine search prefers another move. Inspect both continuations; no specific tactical motif is established.')
        for label,start_board,line in (('preferred',before,a['pv_uci']),('reply',actual_after,b['pv_uci'])):
            preview=start_board.copy();fens=[preview.fen()]
            for uci in line:
                move=chess.Move.from_uci(uci)
                if move not in preview.legal_moves:
                    break
                preview.push(move);fens.append(preview.fen())
            item[label+'_positions']=fens
        item['reply_pv_san']=b['pv_san']
    candidates.sort(key=lambda r:-r['loss_cp'])
    return dict(engine=name,budget=options,elapsed_seconds=time.monotonic()-start,
        analyzed_plies=max(0,len(scores)-1),total_plies=len(row['moves']),
        complete_coverage=len(scores)==len(positions),candidates=candidates[:20],positions=scores,
        interpretation='Search-limited review candidates, not proof of an objective mistake. Mate scores are not pawn estimates.',
        source='Local Stockfish analysis of this immutable imported game')


def run_one(config):
    with db.connect() as c:
        w=db.workspace(c,config)
        if not w:
            return False
        job=c.execute("""SELECT j.* FROM study.analysis_jobs j JOIN study.imports i ON i.id=j.import_id
            WHERE i.workspace_id=%s AND j.status='queued' ORDER BY j.created_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED""",(w['id'],)).fetchone()
        if not job:
            return False
        if job['settings_hash']!=settings(config)[2]:
            c.execute("UPDATE study.analysis_jobs SET status='failed',error='Analysis code or configuration changed; request a new review.',finished_at=now() WHERE id=%s",(job['id'],))
            return True
        v=db.version(c,config,str(job['import_id']))
        row=db.payload(v,job['game_id'])
        c.execute("UPDATE study.analysis_jobs SET status='running',started_at=now() WHERE id=%s",(job['id'],))
    try:
        evidence=analyze(row,config)
        with db.connect() as c:
            c.execute("UPDATE study.analysis_jobs SET status='complete',evidence=%s,finished_at=now() WHERE id=%s",
                (Jsonb(evidence),job['id']))
    except Exception as exc:
        with db.connect() as c:
            c.execute("UPDATE study.analysis_jobs SET status='failed',error=%s,finished_at=now() WHERE id=%s",
                ('Engine review failed ('+type(exc).__name__+'); retry this explicit job.',job['id']))
    return True


def start_worker(config):
    def loop():
        # Shared-volume flock prevents duplicate workers, and releases on process death.
        with (db.root()/'.engine-worker.lock').open('w') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with db.connect() as c:
                w=db.workspace(c,config)
                if w:
                    c.execute("""UPDATE study.analysis_jobs SET status='failed',error='Worker restarted; retry the explicit review.',finished_at=now()
                        WHERE status='running' AND import_id IN (SELECT id FROM study.imports WHERE workspace_id=%s)""",(w['id'],))
            while True:
                try:
                    worked=run_one(config)
                except Exception:
                    worked=False
                if not worked:
                    time.sleep(2)
    worker=threading.Thread(target=loop,name='personal-engine-worker',daemon=True)
    worker.start()
    return worker
