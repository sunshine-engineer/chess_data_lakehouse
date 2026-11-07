"""Explicit engine reviews and evidence-linked questions, scoped to an account."""
from datetime import date,timedelta
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from .. import engine,pgn
from ..assistant import template_route
from . import storage as db,insights


def enqueue(owner,library_id,snapshot_id,game_id,config):
    _,budget,fingerprint=engine.settings(config)
    with db.connect() as c:
        snap=db.snapshot(c,owner,library_id,snapshot_id);row=db.game(c,owner,snap,game_id)
        c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('analysis:'+str(owner),))
        existing=c.execute('SELECT * FROM study.workspace_analysis WHERE owner_id=%s AND revision_id=%s AND settings_hash=%s AND player=%s',
            (owner,row['id'],fingerprint,snap['player'])).fetchone()
        if existing:
            if existing['status']=='failed':c.execute("UPDATE study.workspace_analysis SET status='queued',error=NULL,finished_at=NULL WHERE owner_id=%s AND id=%s",(owner,existing['id']))
            return {'id':str(existing['id']),'status':'queued' if existing['status']=='failed' else existing['status'],'cached':existing['status']=='complete'}
        active=c.execute("SELECT (SELECT count(*) FROM study.workspace_analysis WHERE owner_id=%s AND status IN ('queued','running'))+(SELECT count(*) FROM study.position_analysis WHERE owner_id=%s AND status IN ('queued','running')) AS n",(owner,owner)).fetchone()['n']
        if active>=config.engine_queue_limit:raise ValueError('Finish a queued review before adding more. Your game library has no analysis-history cap.')
        key=str(uuid4())
        c.execute("INSERT INTO study.workspace_analysis(id,owner_id,revision_id,settings_hash,player,status,evidence) VALUES(%s,%s,%s,%s,%s,'queued',%s)",
            (key,owner,row['id'],fingerprint,snap['player'],Jsonb({'budget':budget})))
        return {'id':key,'status':'queued','cached':False}


def game(owner,library_id,snapshot_id,game_id):
    with db.connect() as c:
        snap=db.snapshot(c,owner,library_id,snapshot_id);revision=db.game(c,owner,snap,game_id)
        row=db.perspective(revision['payload'],snap['player'])
        jobs=c.execute('SELECT id,status,evidence,error FROM study.workspace_analysis WHERE owner_id=%s AND revision_id=%s AND player=%s ORDER BY created_at DESC',(owner,revision['id'],snap['player'])).fetchall()
        notes=c.execute('SELECT ply,status,note FROM study.workspace_reviews WHERE owner_id=%s AND revision_id=%s ORDER BY ply',(owner,revision['id'])).fetchall()
        return dict(pgn.replay(row),library_id=str(snap['library_id']),snapshot_id=str(snap['id']),revision_id=str(revision['id']),
            version=snap['version'],game_date=row['game_date'],analysis_jobs=jobs,notes=notes,
            opponent=row['black'] if row['player_color']=='white' else row['white'],
            speed=row['speed'],outcome=row['outcome'])


def note(owner,library_id,snapshot_id,game_id,ply,status,text):
    if status not in ('queued','reviewed','practiced'):raise ValueError('Choose To review, Reviewed or Practiced')
    if not isinstance(text,str) or len(text)>2000:raise ValueError('A review note can contain up to 2000 characters')
    with db.connect() as c:
        snap=db.snapshot(c,owner,library_id,snapshot_id);row=db.game(c,owner,snap,game_id)
        if isinstance(ply,bool) or not isinstance(ply,int) or not 0<=ply<=row['ply_count']:raise ValueError('Select a valid played position')
        c.execute('''INSERT INTO study.workspace_reviews(owner_id,revision_id,ply,status,note) VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(owner_id,revision_id,ply) DO UPDATE SET status=EXCLUDED.status,note=EXCLUDED.note,updated_at=now()''',
            (owner,row['id'],ply,status,text))
    return {'status':status}


def positions(owner,library_id,snapshot_id,game_id,ply,selected):
    selected=insights.filters(selected or {})
    with db.connect() as c:
        snap=db.snapshot(c,owner,library_id,snapshot_id);row=db.game(c,owner,snap,game_id)
        if isinstance(ply,bool) or not isinstance(ply,int) or not 0<=ply<=row['ply_count']:raise ValueError('Select a valid played position')
        target=chess_board(row['payload'],ply);key=pgn.position_key(target)
        clause,params=insights.where(owner,snap,selected)
        # Use the shared revision's position index, independent of library size.
        matches=c.execute('''WITH matches AS (SELECT DISTINCT ON(f.game_id) f.game_id,f.points,f.result,p.ply,
            coalesce(p.next_san,'Game ended') AS move
            FROM study.workspace_game_facts f JOIN study.workspace_positions p ON p.revision_id=f.revision_id AND p.owner_id=f.owner_id
            WHERE '''+clause+''' AND p.position_hash=%s ORDER BY f.game_id,p.ply)
            SELECT move,count(*) AS games,avg(points)::float8 AS score_rate,count(*) FILTER(WHERE result='win') AS wins,
                min(game_id) AS example_game,sum(count(*)) OVER()::bigint AS total_games
            FROM matches GROUP BY move ORDER BY count(*) DESC,move LIMIT 8''',params+[key]).fetchall()
        pending=c.execute('''SELECT count(*) AS n FROM study.workspace_game_facts f JOIN study.game_revisions r ON r.id=f.revision_id
            WHERE '''+clause+' AND NOT r.positions_ready',params).fetchone()['n']
        alternatives=[]
        for value in matches:
            example=c.execute('''SELECT p.ply FROM study.workspace_positions p JOIN study.workspace_game_facts f
                ON f.revision_id=p.revision_id AND f.owner_id=p.owner_id WHERE '''+clause+
                ' AND p.position_hash=%s AND f.game_id=%s ORDER BY p.ply LIMIT 1',params+[key,value['example_game']]).fetchone()
            alternatives.append(dict(move=value['move'],games=value['games'],score_rate=value['score_rate'],
                example={'game_id':value['example_game'],'ply':example['ply']},win_interval=insights.wilson(value['wins'],value['games'])))
        return {'fen':target.fen(),'games':matches[0]['total_games'] if matches else 0,'alternatives':alternatives,
            'pending_games':pending,'explanation':('Position indexing is still running for '+str(pending)+' games; these counts are partial. ' if pending else '')+
                'First occurrence per game. These are observed choices, not an engine ranking.'}


def chess_board(payload,ply):
    import chess
    board=chess.Board(payload['initial_fen'])
    for uci in payload['moves'][:ply]:board.push_uci(uci)
    return board


def answer(owner,library_id,snapshot_id,question,selected,config,game_id=None,ply=None):
    if not isinstance(question,str) or not question.strip() or len(question)>500:raise ValueError('Ask a question of up to 500 characters')
    applied=insights.filters(selected or {})
    question=re.sub(r'\blast (?:few|seven) days\b','last 7 days',question,flags=re.I)
    question=re.sub(r'\blast few months\b','last 3 months',question,flags=re.I)
    question=re.sub(r'\blast week\b','last 7 days',question,flags=re.I)
    # Dates are analytical filters; they never determine whether a file may be imported.
    relative=re.search(r'\blast\s+(\d{1,3})\s+days?\b',question,re.I)
    if relative:
        days=int(relative[1])
        if not 1<=days<=366:raise ValueError('Choose a period between 1 and 366 days, or use custom dates')
        applied.update(from_date=(date.today()-timedelta(days=days-1)).isoformat(),to_date=date.today().isoformat())
        routed=question[:relative.start()]+question[relative.end():]
    else:routed=question
    months=re.search(r'\blast\s+(\d{1,2})\s+months?\b',routed,re.I)
    if months:
        count=int(months[1])
        if not 1<=count<=60:raise ValueError('Choose 1–60 calendar months, or use custom dates')
        end=date.today().replace(day=1)-timedelta(days=1)
        first=end.year*12+end.month-count
        applied.update(from_date=date(first//12,first%12+1,1).isoformat(),to_date=end.isoformat())
        routed=routed[:months.start()]+routed[months.end():]
    if re.search(r'\bthis month\b',routed,re.I):
        applied.update(from_date=date.today().replace(day=1).isoformat(),to_date=date.today().isoformat())
        routed=re.sub(r'\bthis month\b','',routed,flags=re.I)
    routed=re.sub(r'\blast\s+month\b','',routed,flags=re.I) if re.search(r'\blast\s+month\b',routed,re.I) else routed
    if re.search(r'\blast\s+month\b',question,re.I):
        end=date.today().replace(day=1)-timedelta(days=1);applied.update(from_date=end.replace(day=1).isoformat(),to_date=end.isoformat())
    intent,explicit,refusal=template_route(routed+' my games' if re.search(r'improv|focus|work on|study next|what should i study',routed,re.I) else routed)
    if not refusal and re.search(r'what should i study|study next|improv|focus|work on',routed,re.I):intent='plan'
    if intent=='speed' and len(re.findall(r'\b(?:bullet|blitz|rapid|classical)\b',routed,re.I))>1:applied.pop('speed',None)
    if intent=='color' and re.search('white',routed,re.I) and re.search('black',routed,re.I):applied.pop('color',None)
    applied.update(explicit)
    report=insights.report(owner,library_id,snapshot_id,applied,config)
    common={'snapshot_id':report['snapshot_id'],'version':report['version'],'dataset_sha256':report['dataset_sha256'],
        'filters':report['filters'],'intent':intent,'routing':'computed operations','references':[]}
    if refusal:return dict(common,answer=refusal,supported=False,metrics={})
    summary=report['summary']
    if not summary['games']:return dict(common,answer='No games match that period and those filters. Import more games or widen the analysis period.',supported=False,metrics=summary)
    refs=report['suggested_games'][:3]
    if intent=='review' and re.search(r'best move|this position|this move|\bhere\b',question,re.I):
        if not game_id:return dict(common,answer='Open a game and position first. A tactical answer needs an engine review of that exact game.',supported=False,metrics={})
        with db.connect() as c:
            snap=db.snapshot(c,owner,library_id,report['snapshot_id']);clause,params=insights.where(owner,snap,report['filters'])
            if not c.execute('SELECT 1 FROM study.workspace_game_facts f WHERE '+clause+' AND game_id=%s',params+[game_id]).fetchone():
                raise ValueError('The selected game is outside these analysis filters')
        replay=game(owner,library_id,report['snapshot_id'],game_id)
        completed=next((j for j in replay['analysis_jobs'] if j['status']=='complete'),None)
        evaluations=completed['evidence'].get('positions',[]) if completed else []
        if isinstance(ply,bool) or not isinstance(ply,int) or not 0<=ply<len(evaluations):
            return dict(common,answer='This position has no completed engine evidence. Request a review and check its coverage.',supported=False,metrics={})
        value=evaluations[ply];line=value['pv_san']
        return dict(common,answer=('The bounded Stockfish search prefers '+line[0]+'. Compare the line '+ ' '.join(line)+'. Check all forcing replies before choosing a move.') if line else
            'The game has ended here; the engine has no continuation.',supported=True,metrics=value,references=[{'game_id':game_id,'ply':ply}])
    if intent=='overview':
        text=f"Across {summary['games']} matching games: {summary['wins']} wins, {summary['draws']} draws and {summary['losses']} losses. Your score was {100*summary['score_rate']:.1f}% of available points. Review a linked loss before drawing a conclusion about its cause."
        metrics=summary
    elif intent in ('speed','color','opponents'):
        values=report[{'speed':'by_speed','color':'by_color','opponents':'by_opponent'}[intent]]
        text='; '.join(f"{g['label']}: {g['games']} games, {100*g['score_rate']:.1f}% score" for g in values)+'. Compare the linked games; samples and opponents differ.'
        metrics={'groups':values}
    elif intent=='openings':
        item=next((o for o in report['openings'] if o['review_candidate']),report['openings'][0] if report['openings'] else None)
        if item is None:return dict(common,answer='No opening data is available in this selection.',supported=False,metrics={})
        if re.search(r'best|strongest|highest',question,re.I):item=max(report['openings'],key=lambda o:o['score_rate'])
        text=f"Among your five most represented openings, inspect {item['opening']}: {item['games']} games and {100*item['score_rate']:.1f}% score. {item['explanation']} Start with the linked games and request an engine review to identify a specific move to practice."
        metrics=item;refs=item['games_to_review']
    elif intent=='plan':
        findings=report['findings'][:2]
        if not findings:return dict(common,answer='Start by importing games or widening the period so a supported study focus can be found.',supported=False,metrics={})
        text=' '.join(f"{index+1}. {f['title']}. {f['detail']} Next: {f['action']}" for index,f in enumerate(findings))
        metrics={'findings':findings};refs=[]
        seen=set()
        for finding in findings:
            for ref in finding['games']:
                if ref['game_id'] not in seen:refs.append(ref);seen.add(ref['game_id'])
        refs=refs[:3]
    else:
        finding=next((f for f in report['findings'] if f['kind']=='engine'),None)
        text=(finding['detail']+' '+finding['action']) if finding else 'Choose one linked loss and request an engine review. A game result or opening score alone cannot establish a tactical mistake.'
        metrics=finding or {'engine_candidates':0};refs=finding['games'] if finding else refs
    return dict(common,answer=text,supported=True,metrics=metrics,references=[{'game_id':r['game_id'],'ply':r.get('ply',0)} for r in refs])
