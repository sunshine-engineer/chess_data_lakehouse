"""Metadata-first, add-only Lichess sync with atomic checkpoints and late finishes.

The first sync inventories lightweight IDs, including unfinished games, without
redownloading old movetext. Later scans use an overlapping start-time window.
Unfinished IDs are checked separately even after their window has passed.
"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime,timezone

from . import storage as db

OVERLAP_MS=24*60*60*1000
SEMANTIC_FILTERS={'since','until','max','vs','rated','perfType','color','analysed'}


def eligible(record):
    from .imports import lichess_url
    return record['source_kind']=='lichess' and record['mode']=='append' and not SEMANTIC_FILTERS.intersection(lichess_url(record['source_url'])[2])


def download(record,config,opener=None):
    from . import imports
    owner,key=record['owner_id'],record['id']
    _,player,options=imports.lichess_url(record['source_url'])
    with db.connect() as c:
        lib=db.library(c,owner,player=player,create=True)
        prior=c.execute('SELECT watermark_ms FROM study.library_sync_state WHERE owner_id=%s AND library_id=%s',(owner,lib['id'])).fetchone()
        c.execute('''INSERT INTO study.import_game_queue(owner_id,job_id,game_id,pending)
            SELECT owner_id,%s,game_id,true FROM study.sync_pending_games WHERE owner_id=%s AND library_id=%s
            ON CONFLICT DO NOTHING''',(key,owner,lib['id']))
    until=int(time.time()*1000);since=max(1356998400070,prior['watermark_ms']-OVERLAP_MS) if prior else None
    options.update(moves='false',clocks='false',evals='false',opening='false',ongoing='true',finished='true',sort='dateAsc',until=str(until))
    if since:options['since']=str(since)
    imports.update(owner,key,library_id=lib['id'],sync_mode='incremental' if prior else 'initial_metadata',sync_since_ms=since,sync_until_ms=until,
        phase='Checking new game IDs' if prior else 'Connecting library: checking game IDs without old moves')
    request=urllib.request.Request('https://lichess.org/api/games/user/'+urllib.parse.quote(player)+'?'+urllib.parse.urlencode(options),
        headers={'Accept':'application/x-ndjson','User-Agent':'ChessAtlas-PersonalStudy/2.0'})
    metadata=old=0;batch=[]
    def flush():
        nonlocal old,batch
        if not batch:return
        if imports.cancelled(owner,key):raise imports.Cancelled()
        with db.connect() as c:
            active=db.library(c,owner,lib['id'])['active_snapshot']
            known={r['game_id'] for r in c.execute('SELECT game_id FROM study.snapshot_games WHERE owner_id=%s AND snapshot_id=%s AND game_id=ANY(%s)',
                (owner,active,[r[0] for r in batch])).fetchall()} if active else set()
            from .chronology import save
            for identity,pending,created in batch:
                save(c,owner,identity,datetime.fromtimestamp(created/1000,timezone.utc),'lichess')
                if identity in known:old+=1;continue
                c.execute('INSERT INTO study.import_game_queue(owner_id,job_id,game_id,pending) VALUES(%s,%s,%s,%s) ON CONFLICT(job_id,game_id) DO UPDATE SET pending=EXCLUDED.pending',
                    (owner,key,identity,pending))
        imports.update(owner,key,metadata_games=metadata,skipped_old_games=old);batch=[]
    with imports.open_response(record,request,opener) as response:
        while line:=response.readline(config.max_record_bytes+1):
            if len(line)>config.max_record_bytes:raise ValueError('Lichess metadata record exceeds the configured bound')
            if not line.strip():continue
            try:value=json.loads(line)
            except (ValueError,UnicodeError):raise ValueError('Lichess returned invalid game metadata') from None
            if not isinstance(value,dict):raise ValueError('Lichess returned invalid game metadata')
            identity=value.get('id');created=value.get('createdAt')
            if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9]{8}',identity) or type(created) is not int:
                raise ValueError('Lichess metadata is missing a game ID or start timestamp')
            # Never advance past an unbounded/incorrect upstream response.
            if created>until or since and created<since:raise ValueError('Lichess metadata fell outside the requested time window')
            variant=value.get('variant','standard');variant=variant.get('key') if isinstance(variant,dict) else variant
            metadata+=1
            if variant=='standard':batch.append((identity,value.get('status') in ('created','started'),created))
            if len(batch)>=config.batch_games or metadata%config.batch_games==0:flush()
            if metadata%config.batch_games==0 and imports.cancelled(owner,key):raise imports.Cancelled()
        flush()
    path=db.root()/('upload-'+str(key)+'.pgn');received=0;last=''
    try:
        with path.open('wb') as out:
            while True:
                with db.connect() as c:ids=[r['game_id'] for r in c.execute('SELECT game_id FROM study.import_game_queue WHERE owner_id=%s AND job_id=%s AND game_id>%s ORDER BY game_id LIMIT 100',(owner,key,last)).fetchall()]
                if not ids:break
                last=ids[-1]
                request=urllib.request.Request('https://lichess.org/api/games/export/_ids?moves=true&tags=true&clocks=true&evals=true&opening=true',
                    data=','.join(ids).encode(),headers={'Accept':'application/x-chess-pgn','Content-Type':'text/plain','User-Agent':'ChessAtlas-PersonalStudy/2.0'},method='POST')
                with imports.open_response(record,request,opener) as response:
                    while block:=response.read(65536):
                        if imports.cancelled(owner,key):raise imports.Cancelled()
                        imports.check_space(config,len(block));out.write(block);received+=len(block)
                out.write(b'\n\n')
                imports.update(owner,key,bytes_received=received,phase='Downloading moves for new or unfinished games')
        imports.update(owner,key,metadata_games=metadata,skipped_old_games=old)
        with db.connect() as c:c.execute('UPDATE study.import_jobs SET input_path=%s WHERE owner_id=%s AND id=%s',(str(path),owner,key))
        return path
    except Exception:path.unlink(missing_ok=True);raise


def publish(c,record):
    if not record['sync_mode']:return
    owner,library,key=record['owner_id'],record['library_id'],record['id']
    # A missing PGN must not silently advance the checkpoint.
    unresolved=c.execute('SELECT count(*) AS n FROM study.import_game_queue WHERE owner_id=%s AND job_id=%s AND NOT pending AND NOT resolved',(owner,key)).fetchone()['n']
    if unresolved:raise ValueError('Lichess omitted a requested completed game; retry sync before advancing its checkpoint')
    c.execute('DELETE FROM study.sync_pending_games WHERE owner_id=%s AND library_id=%s',(owner,library))
    c.execute('''INSERT INTO study.sync_pending_games SELECT owner_id,%s,game_id FROM study.import_game_queue
        WHERE owner_id=%s AND job_id=%s AND pending AND NOT resolved''',(library,owner,key))
    c.execute('''INSERT INTO study.library_sync_state(owner_id,library_id,watermark_ms,last_job_id) VALUES(%s,%s,%s,%s)
        ON CONFLICT(library_id) DO UPDATE SET watermark_ms=greatest(study.library_sync_state.watermark_ms,EXCLUDED.watermark_ms),
        last_job_id=EXCLUDED.last_job_id,updated_at=now()''',(owner,library,record['sync_until_ms'],key))
    c.execute('UPDATE study.import_jobs SET pending_games=(SELECT count(*) FROM study.sync_pending_games WHERE owner_id=%s AND library_id=%s) WHERE owner_id=%s AND id=%s',(owner,library,owner,key))
    c.execute('DELETE FROM study.import_game_queue WHERE owner_id=%s AND job_id=%s',(owner,key))
