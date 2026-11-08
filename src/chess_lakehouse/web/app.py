"""Local WSGI app: read-only SQL analytics, historical evidence and ML inference."""
import json
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request,send_file
import numpy as np
import psycopg
from werkzeug.exceptions import HTTPException

from . import data,downloads,predictions,content,evidence as measured
from ..ml.artifacts import Artifacts, ArtifactError
from ..ml.features import prediction_input, vector
from ..personal.proxy import register as register_personal
from .. import academy
from ..runtime import load as runtime

CASES=content.CASES


def create_app(config=None):
    """Create the read-only analytics app and same-origin private-service proxy."""
    app=Flask(__name__)
    paths=runtime().paths
    app.config.update(MAX_CONTENT_LENGTH=None,JSON_SORT_KEYS=False,
        ML_ROOT=str(paths.models),EVIDENCE_ROOT=str(paths.evidence),DOCS_ROOT=str(paths.docs))
    app.config.update(config or {})
    artifacts=Artifacts(app.config['ML_ROOT'])
    register_personal(app)

    @app.before_request
    def limit_archive_requests():
        if not request.path.startswith('/api/personal/'):
            request.max_content_length=4096

    @app.after_request
    def headers(response):
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Cache-Control':'no-store' if request.path.startswith('/api/') else 'no-cache'})
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc,HTTPException):
            if not request.path.startswith(('/api/','/healthz','/static/')):
                return render_template('error.html',code=exc.code,title=exc.name),exc.code
            return jsonify(error=exc.name),exc.code
        if isinstance(exc,(ArtifactError,measured.EvidenceError)):
            return jsonify(error=str(exc)),503
        if isinstance(exc,data.PublicationChanged):return jsonify(error=str(exc)),409
        if isinstance(exc,ValueError):
            return jsonify(error=str(exc)),400
        if isinstance(exc,(psycopg.Error,OSError,KeyError,json.JSONDecodeError)):
            logging.warning('Dashboard dependency unavailable: %s',type(exc).__name__)
            if not request.path.startswith(('/api/','/healthz','/static/')):
                return render_template('error.html',code=503,title='Page temporarily unavailable'),503
            return jsonify(error='Data or model evidence is unavailable. Check the Docker setup.'),503
        app.logger.exception('Unexpected dashboard failure')
        return jsonify(error='Unable to complete request'),500

    @app.get('/')
    def home():
        return render_template('index.html')

    @app.get('/healthz')
    @app.get('/readyz')
    def health():
        with data.connect() as c:
            c.execute('SELECT 1')
        return jsonify(status='ok')

    @app.get('/livez')
    def live():return jsonify(status='ok')

    @app.get('/api/overview')
    def overview():
        return jsonify(data.overview(request.args))

    @app.get('/api/catalog')
    def catalog():return jsonify(data.catalog())

    @app.get('/api/archive-plan')
    def archive_plan():
        from ..optimized.settings import load
        months=request.args.get('months','2013-02,2013-03').split(',')
        if not 1<=len(months)<=6 or len(set(months))!=len(months):raise ValueError('Select one to six unique allowlisted months')
        sources=[load('config/archive-demo.toml',m).source for m in months]
        return jsonify(months=months,input_games=sum(s.expected_games for s in sources),
            sources=[dict(month=s.month,sha256=s.sha256,url=s.url,expected_games=s.expected_games,max_download_bytes=s.max_download_bytes) for s in sources],
            command='bash scripts/import-archives.sh '+','.join(months)+' python_mp',all_months_enabled=False,
            explanation='Operator command runs in Docker. One selected month at a time, with checksum/count and free-disk checks. Re-importing replaces only those month publications. The web app does not execute this command.')

    @app.get('/api/learn')
    def lessons():return jsonify(lessons=academy.LESSONS,scope='Original foundation drills plus labelled personal and historical worked examples.')

    @app.post('/api/learn/board')
    def lesson_board():
        import chess
        from ..personal.workspace.board import describe
        value=request.get_json()
        if not isinstance(value,dict):raise ValueError('Send a lesson and moves')
        board=chess.Board(academy.lesson(value.get('lesson_id'))['fen'])
        moves=value.get('moves',[])
        if not isinstance(moves,list) or len(moves)>80:raise ValueError('Use up to 80 half-moves')
        for text in moves:
            if not isinstance(text,str):raise ValueError('Invalid move')
            move=chess.Move.from_uci(text)
            if move not in board.legal_moves or board.is_game_over():raise ValueError('That move is not legal')
            board.push(move)
        return jsonify(describe(board))

    @app.post('/api/learn/attempt')
    def lesson_attempt():
        value=request.get_json()
        if not isinstance(value,dict):raise ValueError('Send a lesson and move')
        return jsonify(**academy.grade(value.get('lesson_id'),value.get('move')),saved=False)

    @app.get('/api/learn/examples')
    def historical_examples():
        with data.connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            datasets=c.execute('''SELECT source_month::text AS source_month,source_sha256,input_games,
                sample_games,method,created_at::text AS created_at FROM academy.datasets ORDER BY source_month''').fetchall()
            examples=c.execute('SELECT source_month::text,game_id,payload FROM academy.examples ORDER BY source_month DESC,game_id LIMIT 1800').fetchall()
        available=sum(d['sample_games'] for d in datasets)
        return jsonify(datasets=datasets,examples=examples,published_sample_games=available,
            returned_games=len(examples),truncated=len(examples)<available,
            selection='At most 1800 teaching games, most recent source months then game ID.')

    @app.get('/api/games')
    def games():
        return jsonify(data.games(request.args))

    @app.get('/api/games/<identity>')
    def game(identity):
        row=data.detail(identity,request.args)
        return (jsonify(row),200) if row else (jsonify(error='This game is not in the selected archive publication.'),404)

    @app.get('/api/games.csv')
    def export_games():
        stream,info=downloads.archive(request.args)
        response=send_file(stream,mimetype='text/csv; charset=utf-8',as_attachment=True,download_name='chess-atlas-'+request.args.get('month','2013-01')+'.csv')
        response.direct_passthrough=False;response.content_length=info['bytes']
        response.headers.update({'X-Export-Rows':str(info['rows']),'X-Export-Total':str(info['total']),
            'X-Export-Truncated':str(info['truncated']).lower(),'X-Content-SHA256':info['sha256']})
        response.call_on_close(stream.close);return response

    @app.get('/api/benchmarks')
    def benchmarks():
        root=benchmark_root()
        file=root/'comparison-v2.json'
        if not file.exists():
            return jsonify(available=False,message='Run the pipeline benchmark to publish evidence.')
        report=measured.benchmark(root)
        return jsonify(available=True,benchmark_group=report['benchmark_group'],
            measurement=report['measurement'],summaries=report['summary'],equality=report['equality'],
            histories=report['histories'],samples={engine:[{k:sample.get(k) for k in ('run_id','elapsed_seconds','input_games','container_peak_memory_mib','container_cpu_seconds','phases','source_month','code_hash','cpu_limit','memory_limit_mib')}
                for sample in samples] for engine,samples in report.get('samples',{}).items()})

    def benchmark_root():
        workload=request.args.get('workload','baseline')
        if workload not in ('baseline','scale'):raise ValueError('Choose baseline or scale workload')
        root=Path(app.config['EVIDENCE_ROOT'])
        return root/'scale' if workload=='scale' else root

    def model_status():
        report=artifacts.report()
        with data.connect() as c:
            signature=data.source_signature(c,report['engine'],report['month'])
        return report,signature==report['source_signature']

    @app.get('/api/models')
    def models():
        if not (Path(app.config['ML_ROOT'])/'latest.json').exists():
            return jsonify(available=False,message='Train the documented models to add measured results.')
        report,fresh=model_status()
        return jsonify(available=True,fresh=fresh,report=report)

    @app.post('/api/predictions')
    def predict_scenario():
        row=prediction_input(request.get_json());report,fresh=model_status()
        if not fresh:return jsonify(error='The archive publication changed. Estimates are paused until a matching model is published.'),409
        return jsonify(predictions.evaluate(artifacts,report,row))

    @app.get('/api/evidence/<name>')
    def evidence(name):
        if name=='models':value=artifacts.report()
        elif name=='benchmarks':value=measured.benchmark(benchmark_root())
        else:return jsonify(error='Evidence report not found'),404
        response=app.response_class(json.dumps(value,indent=2,allow_nan=False)+'\n',mimetype='application/json')
        response.headers['Content-Disposition']='attachment; filename="chess-atlas-'+name+'.json"';return response

    @app.get('/api/case-studies')
    def cases():return jsonify(cases=[dict(value,id=key) for key,value in CASES.items()])

    @app.post('/api/predict/<name>')
    def predict(name):
        if name not in ('outcome','length'):
            return jsonify(error='Unknown model'),404
        row=prediction_input(request.get_json())
        report,fresh=model_status()
        if not fresh:
            return jsonify(error='Models describe a previous warehouse publication. Retrain before prediction.'),409
        model=artifacts.model(report['run_id'],name)
        x=np.array([vector(row)],dtype=float)
        if name=='outcome':
            result={'probabilities':{str(label):float(p) for label,p in zip(model.classes_,model.predict_proba(x)[0])}}
        else:
            result={'estimated_moves':float(model.predict(x)[0])}
        return jsonify(**result,run_id=report['run_id'],speed=row['speed'],
            scope='Historical archive estimates from '+report['month']+'; probabilities are not guarantees.')

    @app.get('/case-studies/<name>')
    def case_study(name):
        if name not in CASES:
            return render_template('error.html',code=404,title='Case study not found'),404
        case=CASES[name];source=(Path(app.config['DOCS_ROOT'])/case['document']).read_text()
        # Checked-in documentation is trusted, never user-supplied Markdown.
        return render_template('case.html',title=case['title'],case=case,content=content.render(source))

    @app.get('/guides/<name>')
    def guide(name):
        if name not in content.GUIDES:return render_template('error.html',code=404,title='Guide not found'),404
        path=Path(app.config['DOCS_ROOT'])/(name+'.md')
        if not path.is_file():return render_template('error.html',code=404,title='Guide not found'),404
        source=path.read_text();title=source.splitlines()[0].lstrip('# ')
        return render_template('case.html',title=title,case=None,content=content.render(source))

    return app
