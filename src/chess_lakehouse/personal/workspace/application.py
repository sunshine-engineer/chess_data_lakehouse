"""Private account/session API behind the same-origin web proxy."""
import hashlib
import hmac
import json
import os
from uuid import uuid4

from flask import Flask,jsonify,request,g,send_file
from werkzeug.exceptions import HTTPException

from . import storage as db,accounts,imports,insights,analysis,exports,worker,board,learning,repertoire,archives
from .config import load


def create_app(config=None):
    app=Flask(__name__)
    app.config.update(TOKEN=os.environ.get('STUDY_API_TOKEN',''),WORKER=os.environ.get('STUDY_WORKER')=='1',
        SETTINGS=load(),MAX_CONTENT_LENGTH=None,MAX_FORM_MEMORY_SIZE=128*1024)
    app.config.update(config or {})
    if not app.config['TOKEN']:raise ValueError('STUDY_API_TOKEN is required')
    settings=app.config['SETTINGS']

    def result(value,status=200):return jsonify(json.loads(json.dumps(value,default=str,allow_nan=False))),status

    def body():
        value=request.get_json()
        if not isinstance(value,dict):raise ValueError('Send a JSON object')
        return value

    @app.before_request
    def authorize():
        if request.path=='/healthz':return None
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+app.config['TOKEN']):return result({'error':'Private service authentication required'},401)
        g.user=accounts.session(request.cookies.get(accounts.COOKIE))
        if request.path not in ('/session','/signup','/login'):
            if not g.user:return result({'error':'Sign in to open your private game library'},401)
            if request.method=='POST' and not accounts.csrf_matches(g.user,request.headers.get('X-CSRF-Token')):
                return result({'error':'Refresh the page before saving changes'},403)
        if request.path!='/imports/file':request.max_content_length=32768

    @app.errorhandler(Exception)
    def failure(e):
        if isinstance(e,HTTPException):return result({'error':e.name},e.code)
        if isinstance(e,ValueError):return result({'error':str(e)},400)
        app.logger.exception('Workspace request failed: %s',type(e).__name__)
        return result({'error':'The study service could not complete this request. Check its container logs.'},503)

    @app.get('/healthz')
    def health():
        with db.connect() as c:c.execute('SELECT 1 FROM study.accounts LIMIT 1')
        return result({'status':'ok'})

    @app.get('/session')
    def session():
        return result({'authenticated':bool(g.user),'user':g.user,'unlimited_games':not settings.max_library_games,
            'max_library_games':settings.max_library_games or None})

    def sign_in(user):
        token,csrf=accounts.new_session(user['id'])
        response=jsonify({'authenticated':True,'user':{'id':str(user['id']),'username':user['username'],'csrf':csrf}})
        response.set_cookie(accounts.COOKIE,token,max_age=14*86400,httponly=True,samesite='Lax',secure=os.environ.get('STUDY_SECURE_COOKIES')=='1',path='/api/personal')
        return response

    @app.post('/signup')
    def signup():
        value=body();user=accounts.create(value.get('username'),value.get('password'))
        return sign_in(user)

    @app.post('/login')
    def login():
        value=body();return sign_in(accounts.login(value.get('username'),value.get('password')))

    @app.post('/logout')
    def logout():
        accounts.logout(request.cookies.get(accounts.COOKIE,''));response=jsonify({'authenticated':False})
        response.delete_cookie(accounts.COOKIE,path='/api/personal');return response

    @app.post('/password')
    def password():
        from werkzeug.security import generate_password_hash
        value=body();accounts.login(g.user['username'],value.get('current_password'))
        accounts.credentials(g.user['username'],value.get('new_password'))
        with db.connect() as c:
            c.execute('UPDATE study.accounts SET password_hash=%s WHERE id=%s',(generate_password_hash(value['new_password'],method='scrypt'),g.user['id']))
            c.execute('DELETE FROM study.sessions WHERE owner_id=%s',(g.user['id'],))
        return sign_in(g.user)

    @app.get('/libraries')
    def libraries():
        with db.connect() as c:
            rows=c.execute('''SELECT l.id,l.player,l.active_snapshot,s.version,coalesce(s.game_count,0) AS games,s.created_at
                FROM study.libraries l LEFT JOIN study.library_snapshots s ON s.id=l.active_snapshot
                WHERE l.owner_id=%s ORDER BY lower(l.player)''',(g.user['id'],)).fetchall()
        return result({'libraries':rows})

    @app.get('/context')
    def context():return libraries()

    @app.post('/libraries/<library_id>/sync')
    def sync_library(library_id):
        import re
        with db.connect() as c:lib=db.library(c,g.user['id'],library_id)
        if not re.fullmatch(r'[A-Za-z0-9_-]{2,30}',lib['player']):raise ValueError('This player name cannot be synced from Lichess. Import a PGN file instead.')
        return result({'job_id':imports.create(g.user['id'],'lichess',lib['player'],'append',url='https://lichess.org/api/games/user/'+lib['player'])},202)

    @app.get('/libraries/<library_id>/versions')
    def versions(library_id):
        with db.connect() as c:
            lib=db.library(c,g.user['id'],library_id)
            rows=c.execute('SELECT id,version,game_count,created_at FROM study.library_snapshots WHERE owner_id=%s AND library_id=%s ORDER BY version DESC',
                (g.user['id'],lib['id'])).fetchall()
        return result({'library':lib,'versions':rows})

    @app.post('/imports/file')
    def file_import():
        file=request.files.get('file')
        if not file:raise ValueError('Choose a PGN file')
        path=db.root()/('upload-'+str(uuid4())+'.pgn');size=0
        try:
            with path.open('wb') as out:
                while block:=file.stream.read(65536):
                    imports.check_space(settings,len(block));out.write(block);size+=len(block)
            if not size:raise ValueError('The PGN file is empty')
            key=imports.create(g.user['id'],'file',request.form.get('player',''),request.form.get('mode','append'),path=path)
            imports.update(g.user['id'],key,bytes_received=size)
            return result({'job_id':key},202)
        except Exception:
            path.unlink(missing_ok=True);raise

    @app.post('/imports/lichess')
    def api_import():
        value=body();key=imports.create(g.user['id'],'lichess',value.get('player',''),value.get('mode','append'),url=value.get('url'))
        return result({'job_id':key},202)

    @app.post('/imports/preview')
    def preview():
        url,player,options=imports.lichess_url(body().get('url'))
        return result({'url':url,'player':player,'maximum':int(options['max']) if 'max' in options else None,
            'explanation':'Import '+('up to '+options['max'] if 'max' in options else 'all available')+' completed public games for '+player+'. Any date, opponent or clock filters in the link are kept.'})

    @app.get('/imports')
    def jobs():
        with db.connect() as c:
            rows=c.execute('SELECT * FROM study.import_jobs WHERE owner_id=%s ORDER BY created_at DESC LIMIT 10',(g.user['id'],)).fetchall()
        return result({'jobs':[public_job(r) for r in rows]})

    def public_job(row):
        from .import_status import describe
        return dict({k:v for k,v in row.items() if k not in ('input_path','owner_id')},outcome=describe(row) if row['status']=='complete' else None)

    @app.get('/imports/<key>')
    def job(key):
        with db.connect() as c:return result(public_job(imports.job(c,g.user['id'],key)))

    @app.post('/imports/<key>/cancel')
    def cancel(key):
        with db.connect() as c:
            row=imports.job(c,g.user['id'],key)
            if row['status'] in ('queued','running'):
                c.execute('UPDATE study.import_jobs SET cancel_requested=true WHERE owner_id=%s AND id=%s',(g.user['id'],row['id']))
        return result({'cancel_requested':True})

    @app.post('/imports/<key>/retry')
    def retry(key):
        with db.connect() as c:row=imports.job(c,g.user['id'],key)
        if row['source_kind']!='lichess':raise ValueError('Choose the PGN file again to retry a file import')
        if row['status'] not in ('failed','cancelled'):raise ValueError('This import does not need a retry')
        return result({'job_id':imports.create(g.user['id'],'lichess',row['player'],row['mode'],url=row['source_url'])},202)

    def selection(value):return value.get('library_id'),value.get('snapshot_id')

    @app.post('/report')
    def report():
        value=body();return result(insights.report(g.user['id'],*selection(value),value.get('filters',{}),settings))

    @app.post('/games')
    def games():
        value=body();return result(insights.games(g.user['id'],*selection(value),value.get('filters',{}),value.get('offset',0),value.get('limit',10),value.get('order','newest')))

    @app.get('/games/<game_id>')
    def game(game_id):return result(analysis.game(g.user['id'],request.args.get('library_id'),request.args.get('snapshot_id'),game_id))

    @app.post('/analysis')
    def analyze():
        value=body();return result(analysis.enqueue(g.user['id'],*selection(value),value.get('game_id'),settings),202)

    @app.post('/positions')
    def positions():
        value=body();return result(analysis.positions(g.user['id'],*selection(value),value.get('game_id'),value.get('ply'),value.get('filters',{})))

    @app.route('/board/preferences',methods=['GET','POST'])
    def board_preferences():return result(board.preferences(g.user['id'],body() if request.method=='POST' else None))

    @app.post('/board/position')
    def board_position():return result(board.position(g.user['id'],body()))

    @app.post('/board/variations')
    def board_variation():return result(board.save(g.user['id'],body()),201)

    @app.post('/board/variations/remove')
    def board_remove():return result(board.remove(g.user['id'],body()))

    @app.post('/board/analysis')
    def board_analysis():return result(board.enqueue(g.user['id'],body(),settings),202)

    @app.get('/board/analysis/<key>')
    def board_analysis_result(key):
        import chess
        row=board.result(g.user['id'],key)
        if row['status']=='complete':
            position=chess.Board(row['fen']);fens=[position.fen()]
            for uci in row['evidence'].get('pv_uci',[]):
                move=chess.Move.from_uci(uci)
                if move not in position.legal_moves:break
                position.push(move);fens.append(position.fen())
            row['evidence']['positions']=fens
        return result(row)

    @app.post('/learning/examples')
    def learning_examples():return result(learning.personal(g.user['id'],body()))

    @app.post('/repertoire/start')
    def repertoire_start():return result(repertoire.start(g.user['id'],body()),202)

    @app.post('/repertoire/status')
    def repertoire_status():return result(repertoire.status(g.user['id'],body()))

    @app.post('/repertoire/control')
    def repertoire_control():return result(repertoire.control(g.user['id'],body()))

    @app.post('/repertoire/list')
    def repertoire_list():return result(repertoire.listing(g.user['id'],body()))

    @app.post('/repertoire/exercise')
    def repertoire_exercise():return result(repertoire.exercise(g.user['id'],body()))

    @app.post('/repertoire/reveal')
    def repertoire_reveal():return result(repertoire.exercise(g.user['id'],body(),True))

    @app.post('/repertoire/attempt')
    def repertoire_attempt():return result(repertoire.attempt(g.user['id'],body()))

    @app.get('/archives/catalog')
    def archive_catalog():return result(archives.catalog())

    @app.route('/archives/jobs',methods=['GET','POST'])
    def archive_jobs():return result(archives.create(g.user['id'],body()),202) if request.method=='POST' else result(archives.jobs(g.user['id']))

    @app.post('/archives/control')
    def archive_control():return result(archives.control(g.user['id'],body()))

    @app.post('/archives/subscription')
    def archive_subscription():return result(archives.subscribe(g.user['id'],body()))

    @app.get('/learning/progress')
    def learning_progress():return result(learning.progress(g.user['id']))

    @app.post('/learning/attempt')
    def learning_attempt():return result(learning.attempt(g.user['id'],body()))

    @app.post('/review')
    def review():
        value=body();return result(analysis.note(g.user['id'],*selection(value),value.get('game_id'),value.get('ply'),value.get('status'),value.get('note','')))

    @app.post('/assistant')
    def question():
        value=body();return result(analysis.answer(g.user['id'],*selection(value),value.get('question'),value.get('filters',{}),settings,value.get('game_id'),value.get('ply'),value.get('moves'),value.get('profile','balanced')))

    @app.get('/studies')
    def saved():return result({'studies':exports.studies(g.user['id'])})

    @app.post('/studies')
    def save():
        value=body();return result(exports.save(g.user['id'],*selection(value),value.get('name'),value.get('filters',{})))

    @app.get('/export')
    def export():
        selected={key:request.args[key] for key in ('color','speed','opponent_band','opening','from_date','to_date','period','last_games','result','opponent') if key in request.args}
        path,mime,name,sha=exports.prepare(g.user['id'],request.args.get('library_id'),request.args.get('snapshot_id'),request.args.get('study_id'),selected,request.args.get('format','parquet'),settings)
        response=send_file(path,mimetype=mime,as_attachment=True,download_name=name)
        # Werkzeug then closes the file wrapper and runs cleanup after streaming.
        response.direct_passthrough=False
        response.headers['X-Content-SHA256']=sha;response.headers['Cache-Control']='no-store'
        response.call_on_close(lambda:path.unlink(missing_ok=True))
        return response

    if app.config['WORKER']:worker.start(settings)
    return app
