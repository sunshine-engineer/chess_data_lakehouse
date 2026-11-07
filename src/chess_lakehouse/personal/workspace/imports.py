"""Disk-spooled, cancellable imports with shared revisions and atomic publication."""
from dataclasses import dataclass
import hashlib
import html
import io
import json
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

import chess.pgn

from .. import pgn
from . import storage as db


@dataclass(frozen=True)
class ParseContext:
    player: str
    max_upload_bytes: int
    max_game_plies: int
    max_total_plies: int
    max_games: int = 1
    flexible_dates: bool = True
    month: object = None
    month_date: object = None

    @property
    def normalized_player(self):return self.player.casefold()


class Cancelled(Exception):pass


def player_name(value):
    value=(value or '').strip()
    if value and (len(value)>50 or any(ord(char)<32 for char in value)):
        raise ValueError('Enter a player name of up to 50 visible characters')
    return value


def lichess_url(value):
    """Never fetch arbitrary URLs, userinfo, redirects or non-game endpoints."""
    if not isinstance(value,str) or len(value)>4096:raise ValueError('Paste a Lichess games API link')
    value=html.unescape(value.strip()).replace('\\&','&')
    try:u=urllib.parse.urlsplit(value)
    except ValueError:raise ValueError('Invalid Lichess API link') from None
    match=re.fullmatch(r'/api/games/user/([A-Za-z0-9_-]{2,30})/?',u.path)
    if u.scheme!='https' or u.netloc.casefold()!='lichess.org' or not match or u.fragment:
        raise ValueError('Use https://lichess.org/api/games/user/USERNAME — only the official games endpoint is supported')
    pairs=urllib.parse.parse_qsl(u.query,keep_blank_values=False)
    allowed={'since','until','max','vs','rated','perfType','color','analysed','moves','tags','clocks','evals','opening','ongoing','finished','literate','sort'}
    if any(k not in allowed for k,v in pairs) or len(dict(pairs))!=len(pairs):raise ValueError('This link contains an unsupported or repeated API option')
    q=dict(pairs)
    for key in ('since','until','max'):
        if key in q and (not q[key].isdigit() or int(q[key])<1):raise ValueError('API '+key+' must be a positive number')
    if 'since' in q and 'until' in q and int(q['since'])>int(q['until']):raise ValueError('The API start date is after its end date')
    if q.get('color') not in (None,'white','black'):raise ValueError('API color must be white or black')
    if q.get('sort') not in (None,'dateAsc','dateDesc'):raise ValueError('Invalid API sort option')
    for key in ('rated','analysed','moves','tags','clocks','evals','opening','ongoing','finished','literate'):
        if q.get(key) not in (None,'true','false'):raise ValueError('API '+key+' must be true or false')
    q.update(tags='true',moves='true',opening='true',clocks='true',evals='true',ongoing='false',finished='true')
    return 'https://lichess.org/api/games/user/'+match[1]+'?'+urllib.parse.urlencode(q),match[1],q


def job(c,owner,key):
    value=c.execute('SELECT * FROM study.import_jobs WHERE owner_id=%s AND id=%s',(owner,db.identifier(key))).fetchone()
    if not value:raise ValueError('Import not found in your account')
    return value


def create(owner,kind,player,mode,path=None,url=None):
    if mode not in ('append','merge','replace'):raise ValueError('Choose new games, refresh, or replacement')
    player=player_name(player)
    if kind=='lichess':
        url,inferred,_=lichess_url(url)
        if player and player.casefold()!=inferred.casefold():raise ValueError('The selected player does not match the API link')
        player=inferred
    key=str(uuid4())
    with db.connect() as c:
        active=c.execute("SELECT count(*) AS n FROM study.import_jobs WHERE owner_id=%s AND status IN ('queued','running')",(owner,)).fetchone()['n']
        if active>=3:raise ValueError('You already have three imports waiting or running. Finish or cancel one first.')
        c.execute('''INSERT INTO study.import_jobs(id,owner_id,source_kind,source_url,input_path,player,mode,status)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'queued')''',(key,owner,kind,url,str(path) if path else None,player or None,mode))
    return key


def check_space(config,additional=0):
    if shutil.disk_usage(db.root()).free < config.min_free_disk_bytes+additional:
        raise ValueError('There is not enough free disk space to continue. Free space or configure storage before retrying.')


def cancelled(owner,key):
    with db.connect() as c:return job(c,owner,key)['cancel_requested']


def update(owner,key,**values):
    allowed={'phase','bytes_received','games_processed','new_games','changed_games','duplicate_games','rejected_games','player','library_id','source_sha256',
        'sync_mode','sync_since_ms','sync_until_ms','metadata_games','skipped_old_games'}
    if set(values)-allowed:raise ValueError('invalid job update')
    with db.connect() as c:
        c.execute('UPDATE study.import_jobs SET '+','.join(k+'=%s' for k in values)+' WHERE owner_id=%s AND id=%s',list(values.values())+[owner,key])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('The Lichess endpoint redirected unexpectedly; use the official games API link')


def download(record,config,opener=None):
    normalized,_,_=lichess_url(record['source_url'])
    path=db.root()/('upload-'+str(record['id'])+'.pgn')
    request=urllib.request.Request(normalized,headers={'Accept':'application/x-chess-pgn','User-Agent':'ChessAtlas-PersonalStudy/1.0'})
    received=0
    try:
        with open_response(record,request,opener) as response,path.open('wb') as out:
            while block:=response.read(65536):
                if cancelled(record['owner_id'],record['id']):raise Cancelled()
                check_space(config,len(block));out.write(block);received+=len(block)
                update(record['owner_id'],record['id'],bytes_received=received,phase='Downloading games from Lichess')
    except Exception:
        path.unlink(missing_ok=True);raise
    with db.connect() as c:c.execute('UPDATE study.import_jobs SET input_path=%s WHERE owner_id=%s AND id=%s',(str(path),record['owner_id'],record['id']))
    return path


def open_response(record,request,opener=None):
    opener=opener or urllib.request.build_opener(NoRedirect())
    response=None
    for attempt in range(4):
        if cancelled(record['owner_id'],record['id']):raise Cancelled()
        try:response=opener.open(request,timeout=30);break
        except urllib.error.HTTPError as e:
            if e.code==429 and attempt<3:
                update(record['owner_id'],record['id'],phase='Lichess rate limit: waiting one minute before retrying')
                # Official API guidance requires a full minute, with cancellation checked each second.
                for _ in range(60):
                    if cancelled(record['owner_id'],record['id']):raise Cancelled()
                    time.sleep(1)
                continue
            raise ValueError('Lichess could not export these games (HTTP '+str(e.code)+'). Check the username and link.') from None
        except (urllib.error.URLError,TimeoutError):raise ValueError('Lichess download was interrupted. Retry the import when the connection is available.') from None
    if response is None:raise ValueError('Lichess is still rate limiting this request; retry later')
    return response


class RecordReader:
    """Limit one PGN record without limiting the number of records in a file."""
    def __init__(self,stream,limit):self.stream=stream;self.limit=limit;self.used=0
    def readline(self,size=-1):
        line=self.stream.readline(self.limit-self.used+1)
        self.used+=len(line.encode('utf-8'))
        if self.used>self.limit:raise ValueError('One game exceeds the configured per-game record size')
        return line


def detect_player(path,config,cancel_check=None):
    common=None
    with path.open(encoding='utf-8-sig') as stream:
        number=0
        while True:
            number+=1
            if cancel_check and number%100==1 and cancel_check():raise Cancelled()
            h=chess.pgn.read_headers(RecordReader(stream,config.max_record_bytes))
            if h is None:break
            names={v.strip().casefold():v.strip() for v in (h.get('White',''),h.get('Black','')) if not pgn.missing(v)}
            common=names if common is None else {k:common[k] for k in common if k in names}
            if not common:break
    if not common or len(common)!=1:raise ValueError('A single player could not be identified. Enter the player to study and try again.')
    return player_name(next(iter(common.values())))


def source_id(headers):
    """Use the parser's URL/GameId rules, including /game/ID and player URLs.

    Skipped or empty records still need their source ID for checkpoint safety.
    A generic local-content hash is not a remote export identity.
    """
    identity,origin,_=pgn.game_identity(headers,{},None,'',[],set())
    return identity if origin=='source' else None


def iterate(path,player,config,known=None):
    context=ParseContext(player,config.max_record_bytes,config.max_game_plies,config.max_game_plies)
    builder=type('WorkspaceBuilder',(pgn.StrictBuilder,),{'max_plies':config.max_game_plies})
    with path.open(encoding='utf-8-sig') as stream:
        number=0
        while True:
            number+=1
            try:
                if known:
                    position=stream.tell();headers=chess.pgn.read_headers(RecordReader(stream,config.max_record_bytes))
                    if headers is None:break
                    identity=source_id(headers)
                    if identity and known(identity):
                        yield number,{'_existing_id':identity};continue
                    stream.seek(position)
                game=chess.pgn.read_game(RecordReader(stream,config.max_record_bytes),Visitor=builder)
                if game is None:break
                game_id=source_id(game.headers)
                variant=game.headers.get('Variant','Standard')
                if not pgn.missing(variant) and variant not in ('Standard','Chess'):
                    yield number,{'_game_id':game_id,'_skip_reason':'Unsupported variant: '+variant[:40]};continue
                names=[game.headers.get(k,'').strip() for k in ('White','Black')]
                if all(not pgn.missing(name) for name in names) and all(name.casefold()!=player.casefold() for name in names):
                    yield number,{'_game_id':game_id,'_skip_reason':'Game does not contain the selected player'};continue
                if not any(game.mainline_moves()):
                    yield number,{'_game_id':game_id,'_skip_reason':'Empty or aborted game with no moves'};continue
                text=game.accept(chess.pgn.StringExporter(headers=True,variations=False,comments=True))
                try:rows,_=pgn.parse(text.encode(),context)
                except ValueError as error:
                    if 'game result is missing' in str(error):
                        yield number,{'_game_id':game_id,'_skip_reason':'Unfinished game with no completed result'};continue
                    raise
                yield number,rows[0]
            except (ValueError,IndexError,UnicodeError) as e:
                # A malformed record has uncertain boundaries: stop atomically, rather than skip another game.
                raise ValueError('Game '+str(number)+': '+str(e)) from None


def publish(owner,key,config):
    """Publish a set of references; identical games never create another payload."""
    with db.connect() as c:
        # Keep publication consistent with the database/artifact backup snapshot.
        c.execute("SELECT pg_advisory_xact_lock(hashtext('personal-study-publication'))")
        record=job(c,owner,key)
        if record['cancel_requested']:raise Cancelled()
        lib=db.library(c,owner,record['library_id'])
        c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('library:'+str(lib['id']),))
        current=db.snapshot(c,owner,lib['id']) if lib['active_snapshot'] else None
        # The common no-new-games sync publishes only its checkpoint, without
        # copying or rehashing an unchanged library's memberships.
        staged=c.execute('SELECT 1 FROM study.import_members WHERE owner_id=%s AND job_id=%s LIMIT 1',(owner,key)).fetchone()
        if current and record['mode']=='append' and not staged:
            from . import sync
            sync.publish(c,record)
            c.execute("UPDATE study.import_jobs SET status='complete',phase='No new completed games',snapshot_id=%s,finished_at=now(),input_path=NULL WHERE owner_id=%s AND id=%s",(current['id'],owner,key))
            return str(current['id'])
        c.execute('CREATE TEMP TABLE desired_games(game_id TEXT PRIMARY KEY,revision_id UUID NOT NULL) ON COMMIT DROP')
        if current and record['mode'] in ('merge','append'):
            c.execute('INSERT INTO desired_games SELECT game_id,revision_id FROM study.snapshot_games WHERE owner_id=%s AND snapshot_id=%s',(owner,current['id']))
        conflict='DO NOTHING' if record['mode']=='append' else 'DO UPDATE SET revision_id=EXCLUDED.revision_id'
        c.execute('''INSERT INTO desired_games SELECT game_id,revision_id FROM study.import_members WHERE owner_id=%s AND job_id=%s
            ON CONFLICT(game_id) '''+conflict,(owner,key))
        count=c.execute('SELECT count(*) AS n FROM desired_games').fetchone()['n']
        if config.max_library_games and count>config.max_library_games:raise ValueError('This import exceeds the configured library game quota')
        if not count and not record['sync_mode']:raise ValueError('No completed standard games were found in this import')
        digest=hashlib.sha256(lib['player_key'].encode())
        with c.cursor(name='snapshot_digest') as cursor:
            cursor.execute('SELECT d.game_id,r.payload_sha256 FROM desired_games d JOIN study.game_revisions r ON r.id=d.revision_id ORDER BY d.game_id')
            for entry in cursor:digest.update((entry['game_id']+':'+entry['payload_sha256']+'\n').encode())
        sha=digest.hexdigest()
        if not count:snapshot_id=None
        elif current and current['dataset_sha256']==sha:
            snapshot_id=current['id']
        else:
            snapshot_id=str(uuid4());version=current['version']+1 if current else 1
            c.execute('''INSERT INTO study.library_snapshots(id,owner_id,library_id,version,parent_id,game_count,dataset_sha256,source_sha256)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(snapshot_id,owner,lib['id'],version,current['id'] if current else None,count,sha,record['source_sha256']))
            c.execute('INSERT INTO study.snapshot_games SELECT %s,%s,game_id,revision_id FROM desired_games',(owner,snapshot_id))
            c.execute('UPDATE study.libraries SET active_snapshot=%s WHERE owner_id=%s AND id=%s',(snapshot_id,owner,lib['id']))
        c.execute("UPDATE study.import_jobs SET status='complete',phase='Ready to study',snapshot_id=%s,finished_at=now(),input_path=NULL WHERE owner_id=%s AND id=%s",(snapshot_id,owner,key))
        c.execute('DELETE FROM study.import_members WHERE owner_id=%s AND job_id=%s',(owner,key))
        from . import sync
        sync.publish(c,record)
        if record['mode']=='replace':
            c.execute('DELETE FROM study.sync_pending_games WHERE owner_id=%s AND library_id=%s',(owner,lib['id']))
            c.execute('DELETE FROM study.library_sync_state WHERE owner_id=%s AND library_id=%s',(owner,lib['id']))
        return str(snapshot_id) if snapshot_id else None


def cleanup_staging(owner,key):
    with db.connect() as c:
        c.execute('DELETE FROM study.import_members WHERE owner_id=%s AND job_id=%s',(owner,key))
        c.execute('DELETE FROM study.import_game_queue WHERE owner_id=%s AND job_id=%s',(owner,key))
        # No duplicate/orphan payload is retained after a failed or cancelled import.
        c.execute('''DELETE FROM study.game_revisions r WHERE r.owner_id=%s
            AND NOT EXISTS(SELECT 1 FROM study.snapshot_games m WHERE m.revision_id=r.id)
            AND NOT EXISTS(SELECT 1 FROM study.import_members m WHERE m.revision_id=r.id)
            AND NOT EXISTS(SELECT 1 FROM study.workspace_analysis a WHERE a.revision_id=r.id)
            AND NOT EXISTS(SELECT 1 FROM study.workspace_reviews v WHERE v.revision_id=r.id)''',(owner,))


def process(record,config,opener=None):
    owner=record['owner_id'];key=record['id'];path=Path(record['input_path']) if record.get('input_path') else None
    try:
        if cancelled(owner,key):raise Cancelled()
        from . import sync
        is_sync=sync.eligible(record)
        path=sync.download(record,config,opener) if is_sync else download(record,config,opener) if record['source_kind']=='lichess' else Path(record['input_path'])
        player=record['player'] or detect_player(path,config,lambda:cancelled(owner,key))
        with db.connect() as c:lib=db.library(c,owner,player=player,create=True)
        sha=hashlib.sha256()
        with path.open('rb') as stream:
            while block:=stream.read(1048576):
                if cancelled(owner,key):raise Cancelled()
                sha.update(block)
        update(owner,key,player=player,library_id=lib['id'],source_sha256=sha.hexdigest(),phase='Reading and deduplicating games')
        total=new=changed=duplicates=rejected=0;batch=[];batch_bytes=0;diagnostics=[]
        def flush():
            nonlocal new,changed,duplicates,batch,batch_bytes
            if not batch:return
            if cancelled(owner,key):raise Cancelled()
            check_space(config)
            with db.connect() as c:
                active=db.library(c,owner,lib['id'])['active_snapshot']
                for row in batch:
                    previous=c.execute('SELECT revision_id FROM study.snapshot_games WHERE owner_id=%s AND snapshot_id=%s AND game_id=%s',(owner,active,row['game_id'])).fetchone() if active else None
                    if record['mode']=='append' and previous:duplicates+=1;continue
                    repeated=c.execute('SELECT revision_id FROM study.import_members WHERE owner_id=%s AND job_id=%s AND game_id=%s',(owner,key,row['game_id'])).fetchone()
                    revision=db.save_revision(c,owner,row)
                    if repeated and str(repeated['revision_id'])!=revision:raise ValueError('The file contains conflicting copies of game '+row['game_id'])
                    if repeated or previous and str(previous['revision_id'])==revision:duplicates+=1
                    elif previous:changed+=1
                    else:new+=1
                    c.execute('INSERT INTO study.import_members VALUES(%s,%s,%s,%s) ON CONFLICT(job_id,game_id) DO NOTHING',(owner,key,row['game_id'],revision))
                if config.max_storage_bytes:
                    size=c.execute('SELECT coalesce(sum(pg_column_size(payload)),0) AS n FROM study.game_revisions WHERE owner_id=%s',(owner,)).fetchone()['n']
                    if size>config.max_storage_bytes:raise ValueError('This import exceeds the configured account storage quota')
                c.execute('''UPDATE study.import_jobs SET games_processed=%s,new_games=%s,changed_games=%s,duplicate_games=%s
                    WHERE owner_id=%s AND id=%s''',(total,new,changed,duplicates,owner,key))
            batch=[];batch_bytes=0
        def known(identity):
            with db.connect() as c:
                active=db.library(c,owner,lib['id'])['active_snapshot']
                return bool(active and c.execute('SELECT 1 FROM study.snapshot_games WHERE owner_id=%s AND snapshot_id=%s AND game_id=%s',(owner,active,identity)).fetchone())
        for _,row in iterate(path,player,config,known if record['mode']=='append' else None):
            total+=1
            if is_sync:
                if row.get('_skip_reason')=='Game does not contain the selected player':raise ValueError('Lichess returned a requested game for a different player; retry sync')
                identity=row.get('game_id') or row.get('_game_id') or row.get('_existing_id')
                with db.connect() as c:
                    queued=c.execute('SELECT pending FROM study.import_game_queue WHERE owner_id=%s AND job_id=%s AND game_id=%s',(owner,key,identity)).fetchone()
                    if not queued:raise ValueError('Lichess returned a game outside this sync request')
                    unfinished=row.get('_skip_reason','').startswith('Unfinished') or queued['pending'] and row.get('_skip_reason','').startswith('Empty')
                    c.execute('UPDATE study.import_game_queue SET resolved=%s,pending=%s WHERE owner_id=%s AND job_id=%s AND game_id=%s',
                        (not unfinished,unfinished,owner,key,identity))
            if '_existing_id' in row:duplicates+=1;continue
            if '_skip_reason' in row:
                rejected+=1
                if len(diagnostics)<10:diagnostics.append({'game_number':total,'reason':row['_skip_reason']})
                continue
            batch.append(row);batch_bytes+=len(db.canonical(row).encode())
            if len(batch)>=config.batch_games or batch_bytes>=4*1024*1024:flush()
        flush()
        with db.connect() as c:
            from psycopg.types.json import Jsonb
            c.execute('UPDATE study.import_jobs SET games_processed=%s,new_games=%s,changed_games=%s,duplicate_games=%s,rejected_games=%s,diagnostics=%s WHERE owner_id=%s AND id=%s',
                (total,new,changed,duplicates,rejected,Jsonb(diagnostics),owner,key))
        publish(owner,key,config)
    except Cancelled:
        cleanup_staging(owner,key)
        with db.connect() as c:c.execute("UPDATE study.import_jobs SET status='cancelled',phase='Cancelled',finished_at=now(),input_path=NULL WHERE owner_id=%s AND id=%s",(owner,key))
    except Exception as e:
        cleanup_staging(owner,key)
        message=str(e)[:500] if isinstance(e,ValueError) else 'Import failed ('+type(e).__name__+'). Check the file or retry.'
        with db.connect() as c:c.execute("UPDATE study.import_jobs SET status='failed',phase='Needs attention',error=%s,finished_at=now(),input_path=NULL WHERE owner_id=%s AND id=%s",(message,owner,key))
    finally:
        if path:path.unlink(missing_ok=True)
