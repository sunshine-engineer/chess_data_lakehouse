"""Source UTC times stored beside immutable games; bounded metadata-only backfill."""
from datetime import datetime,timezone
import json
import urllib.request
from . import storage as db


def pgn_time(headers):
    """Read an explicitly recorded PGN UTC start time without inventing missing clock precision."""
    try:
        return datetime.strptime(headers['UTCDate']+' '+headers['UTCTime'],'%Y.%m.%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except (KeyError,TypeError,ValueError):return None


def save(c,owner,identity,started,source):
    """Store auxiliary game chronology without changing the immutable semantic game payload."""
    if not identity or not started:return
    c.execute('''INSERT INTO study.game_times(owner_id,game_id,started_at,source) VALUES(%s,%s,%s,%s)
        ON CONFLICT(owner_id,game_id) DO UPDATE SET started_at=EXCLUDED.started_at,source=EXCLUDED.source
        WHERE study.game_times.source<>'lichess' OR EXCLUDED.source='lichess' ''',(owner,identity,started,source))


def backfill(account,player,opener=None):
    """Read only IDs/start times. Never import games or move a sync checkpoint."""
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{2,30}',player):raise ValueError('Use a Lichess player name')
    with db.connect() as c:
        row=c.execute('SELECT id FROM study.accounts WHERE lower(username)=lower(%s)',(account,)).fetchone()
        if not row:raise ValueError('App account not found')
        owner=row['id'];db.library(c,owner,player=player)
    request=urllib.request.Request('https://lichess.org/api/games/user/'+player+'?moves=false&clocks=false&evals=false&opening=false&ongoing=true&finished=true',headers={'Accept':'application/x-ndjson'})
    count=matched=0;batch=[]
    def flush():
        nonlocal matched
        with db.connect() as c:
            owned={r['game_id'] for r in c.execute('SELECT DISTINCT game_id FROM study.game_revisions WHERE owner_id=%s AND game_id=ANY(%s)',(owner,[i for i,_ in batch]))}
            for identity,started in batch:
                if identity in owned:save(c,owner,identity,started,'lichess');matched+=1
        batch.clear()
    from .imports import NoRedirect
    transport=opener or urllib.request.build_opener(NoRedirect())
    # A failed backfill is safe to repeat: it only fills auxiliary chronology.
    with transport.open(request,timeout=30) as response:
        while line:=response.readline(1024*1024+1):
            if len(line)>1024*1024:raise ValueError('Metadata record is too large')
            if not line.strip():continue
            value=json.loads(line);identity=value['id'];stamp=value['createdAt']
            if not re.fullmatch(r'[A-Za-z0-9]{8}',identity) or type(stamp) is not int:raise ValueError('Invalid source chronology')
            batch.append((identity,datetime.fromtimestamp(stamp/1000,timezone.utc)));count+=1
            if len(batch)>=100:flush()
            if count%1000==0:print(json.dumps({'checked':count,'owned_times':matched}),flush=True)
        if batch:flush()
    return {'status':'passed','metadata_checked':count,'owned_times':matched,'moves_downloaded':False,'checkpoints_changed':False}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--account',required=True);parser.add_argument('--player',required=True)
    args=parser.parse_args();print(json.dumps(backfill(args.account,args.player)))
