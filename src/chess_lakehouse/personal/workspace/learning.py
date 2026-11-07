"""Private practice progress and bounded, owner-checked worked game examples."""
import hashlib
import json

from . import storage as db
from ... import academy


def personal(owner,value):
    with db.connect() as c:
        snap=db.snapshot(c,owner,value.get('library_id'),value.get('snapshot_id'))
        common=c.execute('''SELECT opening,count(*) AS games,min(game_id) AS game_id
            FROM study.workspace_game_facts WHERE owner_id=%s AND snapshot_id=%s
            GROUP BY opening ORDER BY count(*) DESC,opening LIMIT 1''',(owner,snap['id'])).fetchone()
        reviews=c.execute('''SELECT a.evidence,r.game_id FROM study.workspace_analysis a
            JOIN study.game_revisions r ON r.id=a.revision_id AND r.owner_id=a.owner_id
            JOIN study.snapshot_games m ON m.revision_id=r.id AND m.owner_id=r.owner_id
            WHERE a.owner_id=%s AND m.snapshot_id=%s AND a.player=%s AND a.status='complete'
            ORDER BY a.finished_at DESC LIMIT 3''',(owner,snap['id'],snap['player'])).fetchall()
        recommendations=[]
        if common:
            recommendations.append(dict(lesson_id='opening-development',title='Revisit a familiar opening',
                explanation=f"{common['opening'] or 'Unknown opening'} appears in {common['games']} of {snap['game_count']} games in this collection. Use the development checklist on a linked game. Frequency alone does not establish a weakness.",
                source='Full selected library: opening frequency',game_id=common['game_id'],ply=0))
        for review in reviews:
            candidates=review['evidence'].get('candidates',[])
            if not candidates:continue
            item=candidates[0]
            recommendations.append(dict(lesson_id='middlegame-checks',title=item.get('motif','Revisit a reviewed decision'),
                explanation=item.get('explanation',item.get('summary','Inspect the recorded engine evidence.'))+' This recommendation uses a bounded engine review, not a proven error.',
                source='Completed review in this collection',game_id=review['game_id'],ply=max(0,item['ply']-1)))
            break
        # Key order is reproducible and index-backed. Explicitly a worked-example
        # sample, never a statistical or skill estimate of the entire library.
        rows=c.execute('''SELECT r.game_id,r.payload FROM study.snapshot_games m
            JOIN study.game_revisions r ON r.id=m.revision_id AND r.owner_id=m.owner_id
            WHERE m.owner_id=%s AND m.snapshot_id=%s ORDER BY m.game_id LIMIT 30''',(owner,snap['id'])).fetchall()
        examples=[]
        for row in rows:
            examples.extend(dict(p,game_id=row['game_id'],source='personal',library_id=str(snap['library_id']),snapshot_id=str(snap['id'])) for p in academy.phase_positions(row['payload']))
    return {'examples':examples,'games_examined':len(rows),'library_games':snap['game_count'],
        'recommendations':recommendations,'library_id':str(snap['library_id']),'snapshot_id':str(snap['id']),
        'method':'First 30 game IDs in the selected collection. Phase heuristic: first 20 half-moves are opening; later non-pawn material <=20 is endgame. Examples illustrate questions, not diagnosed weaknesses.'}


def progress(owner):
    with db.connect() as c:rows=c.execute('''SELECT lesson_id,count(*) AS attempts,count(*) FILTER(WHERE correct) AS successes,max(created_at) AS last_practiced
        FROM study.lesson_attempts WHERE owner_id=%s GROUP BY lesson_id ORDER BY lesson_id''',(owner,)).fetchall()
    return {'lessons':rows}


def attempt(owner,value):
    identity=value.get('lesson_id');item=academy.lesson(identity);move=value.get('move')
    result=academy.grade(identity,move);key=db.identifier(value.get('attempt_id'))
    challenge=hashlib.sha256(json.dumps(item,sort_keys=True).encode()).hexdigest()
    with db.connect() as c:
        c.execute('INSERT INTO study.lesson_attempts(owner_id,attempt_id,lesson_id,challenge_hash,move,correct) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (owner,key,identity,challenge,move,result['correct']))
        row=c.execute('SELECT * FROM study.lesson_attempts WHERE owner_id=%s AND attempt_id=%s',(owner,key)).fetchone()
        if (row['lesson_id'],row['challenge_hash'],row['move'])!=(identity,challenge,move):raise ValueError('This attempt ID was already used; start a new attempt')
    return dict(result,saved=True)
