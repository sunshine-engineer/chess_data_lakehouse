"""Private study service: writes only its isolated schema and artifact volume."""
import calendar
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
from uuid import uuid4

import chess
from flask import Flask,jsonify,request,Response
from psycopg.types.json import Jsonb
from werkzeug.exceptions import HTTPException

from . import repository as db,reports,research,engine
from .pgn import replay,position_key
from .settings import load
from .assistant import answer
from .validation import validate
from ..runtime import load as runtime, secret


def clean(value):
    """Convert supported response values into finite JSON-compatible private API data."""
    return json.loads(json.dumps(value,default=str,allow_nan=False))


def create_app(config=None):
    """Create the compatible legacy player-month API with its authentication and resource limits."""
    app=Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=11*1024*1024,WORKER=runtime().workers.embedded,
        SETTINGS=load(os.environ.get('STUDY_CONFIG','config/study.toml')),
        TOKEN=secret('STUDY_API_TOKEN',''))
    app.config.update(config or {})
    settings=app.config['SETTINGS']
    if not app.config['TOKEN']:
        raise ValueError('STUDY_API_TOKEN is required')

    def result(value,status=200):
        return jsonify(clean(value)),status

    def body():
        value=request.get_json()
        if not isinstance(value,dict):
            raise ValueError('personal requests must contain a JSON object')
        return value

    @app.before_request
    def authorize():
        if request.path!='/healthz' and not hmac.compare_digest(request.headers.get('Authorization',''),
                'Bearer '+app.config['TOKEN']):
            return result({'error':'Study service authentication required'},401)
        if request.method=='POST' and request.path!='/imports':
            if not request.is_json or not isinstance(request.get_json(),dict):
                raise ValueError('personal requests must contain a JSON object')

    @app.errorhandler(Exception)
    def failure(exc):
        if isinstance(exc,HTTPException):
            return result({'error':exc.name},exc.code)
        if isinstance(exc,ValueError):
            return result({'error':str(exc)},400)
        app.logger.exception('Personal study service failure: %s',type(exc).__name__)
        return result({'error':'Personal study service unavailable; check its container logs.'},503)

    @app.get('/healthz')
    def health():
        with db.connect() as c:
            c.execute('SELECT 1 FROM study.workspaces LIMIT 1')
        return result({'status':'ok'})

    @app.get('/context')
    def context():
        year,month=map(int,settings.month.split('-'))
        start=datetime(year,month,1,tzinfo=timezone.utc)
        end=datetime(year+int(month==12),month%12+1,1,tzinfo=timezone.utc)
        link=f'https://lichess.org/api/games/user/{settings.player}?since={int(start.timestamp()*1000)}&until={int(end.timestamp()*1000)-1}&max=500&moves=true&clocks=true&evals=true&opening=true&finished=true&ongoing=false&perfType=ultraBullet,bullet,blitz,rapid,classical,correspondence'
        with db.connect() as c:
            w=db.workspace(c,settings)
            versions=c.execute('SELECT id,version,game_count,created_at,dataset_sha256 FROM study.imports WHERE workspace_id=%s ORDER BY version DESC',
                (w['id'],)).fetchall() if w else []
            cohorts=c.execute('SELECT c.* FROM study.cohorts c JOIN study.imports i ON i.id=c.import_id WHERE i.workspace_id=%s ORDER BY c.created_at DESC',
                (w['id'],)).fetchall() if w else []
        return result(dict(player=settings.player,month=settings.month,max_games=settings.max_games,
            max_upload_bytes=settings.max_upload_bytes,max_analyzed_games=settings.max_analyzed_games,
            active_import_id=str(w['active_import']) if w and w['active_import'] else None,
            versions=versions,cohorts=cohorts,manual_export_url=link,
            synthetic_fixture=settings.synthetic_fixture,
            scope='SYNTHETIC SOFTWARE DEMO — not real player games.' if settings.synthetic_fixture else
                'Private local personal study. Manual uploads only; archive streaming remains paused.'))

    @app.post('/imports')
    def import_games():
        if request.form.get('approved')!='1':
            raise ValueError('confirm the approved player-month before importing')
        file=request.files.get('file')
        if not file:
            raise ValueError('choose a PGN file')
        content=file.stream.read(settings.max_upload_bytes+1)
        return result(db.publish(content,settings,request.form.get('player',''),request.form.get('month',''),request.form.get('mode','append')))

    @app.post('/report')
    def report():
        value=body()
        return result(reports.report(settings,value.get('import_id'),value.get('filters',{})))

    @app.get('/games/<game_id>')
    def game(game_id):
        with db.connect() as c:
            v=db.version(c,settings,request.args.get('import_id'))
            row=db.payload(v,game_id)
            if not row:
                raise ValueError('game does not belong to the selected study version')
        return result(dict(replay(row),import_id=str(v['id']),version=v['version']))

    @app.post('/positions')
    def positions():
        body=request.get_json()
        if not isinstance(body,dict):
            raise ValueError('personal requests must contain a JSON object')
        filters=reports.validate_filters(body.get('filters',{}),settings)
        with db.connect() as c:
            v=db.version(c,settings,body.get('import_id'))
            row=db.payload(v,body.get('game_id'))
            if not row:
                raise ValueError('unknown game')
            ply=body.get('ply',0)
            if isinstance(ply,bool) or not isinstance(ply,int) or not 0<=ply<=len(row['moves']):
                raise ValueError('invalid replay ply')
            board=chess.Board(row['initial_fen'])
            for uci in row['moves'][:ply]:
                board.push_uci(uci)
            selected={g['game_id']:g for g in reports.select_games(c,v['id'],filters)}
            matches=c.execute('''SELECT DISTINCT ON(game_id) game_id,ply,next_uci,next_san FROM study.positions
                WHERE import_id=%s AND position_hash=%s ORDER BY game_id,ply''',(v['id'],position_key(board))).fetchall()
            moves={}
            examples=[]
            for match in matches:
                g=selected.get(match['game_id'])
                if not g:
                    continue
                label=match['next_san'] or 'Game ended'
                moves.setdefault(label,[]).append(g)
                examples.append(dict(game_id=g['game_id'],ply=match['ply'],next_move=label))
        return result(dict(import_id=str(v['id']),fen=board.fen(),position_hash=position_key(board).hex(),
            games=len(examples),alternatives=[dict(move=name,**reports.summary(group)) for name,group in sorted(moves.items())],
            examples=examples[:20],interpretation='First occurrence per game in your selected personal corpus. Frequency is not move quality.'))

    @app.post('/cohorts')
    def cohort():
        body=request.get_json()
        return result(research.save(settings,body.get('import_id'),body.get('name'),body.get('filters',{})))

    @app.post('/review')
    def review():
        body=request.get_json()
        if body.get('status') not in ('queued','reviewed','practiced'):
            raise ValueError('unsupported study-list status')
        note=body.get('note','')
        if not isinstance(note,str) or len(note)>2000:
            raise ValueError('notes must be at most 2000 characters')
        with db.connect() as c:
            v=db.version(c,settings,body.get('import_id'))
            game=c.execute('SELECT ply_count FROM study.games WHERE import_id=%s AND game_id=%s',(v['id'],body.get('game_id'))).fetchone()
            ply=body.get('ply',0)
            if not game or isinstance(ply,bool) or not isinstance(ply,int) or not 0<=ply<=game['ply_count']:
                raise ValueError('invalid game or ply for study-list item')
            c.execute('''INSERT INTO study.review_items(import_id,game_id,ply,status,note) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(import_id,game_id,ply) DO UPDATE SET status=EXCLUDED.status,note=EXCLUDED.note,updated_at=now()''',
                (v['id'],body['game_id'],ply,body['status'],note))
        return result({'status':body['status']})

    @app.post('/analysis')
    def analysis():
        body=request.get_json()
        return result(engine.enqueue(settings,body.get('import_id'),body.get('game_id')))

    @app.post('/assistant')
    def assistant():
        body=request.get_json()
        return result(answer(settings,body.get('import_id'),body.get('question'),body.get('filters',{}),body.get('game_id'),body.get('ply')))

    @app.post('/ml-validation')
    def ml_validation():
        body=request.get_json()
        return result(validate(settings,body.get('import_id')))

    @app.get('/export')
    def export():
        content,mime,name,sha=research.export(settings,request.args.get('import_id'),request.args.get('cohort_id'),request.args.get('format','parquet'))
        return Response(content,mimetype=mime,headers={'Content-Disposition':f'attachment; filename="{name}"','X-Content-SHA256':sha,'Cache-Control':'no-store'})

    if app.config['WORKER']:
        engine.start_worker(settings)
    return app
