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

CASES=content.CASES


def create_app(config=None):
    app=Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=None,JSON_SORT_KEYS=False,
        ML_ROOT=os.environ.get('ML_ROOT','/models'),EVIDENCE_ROOT=os.environ.get('EVIDENCE_ROOT','/evidence'),
        DOCS_ROOT=os.environ.get('DOCS_ROOT','/app/docs'))
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
    def health():
        with data.connect() as c:
            c.execute('SELECT 1')
        return jsonify(status='ok')

    @app.get('/api/overview')
    def overview():
        return jsonify(data.overview(request.args))

    @app.get('/api/catalog')
    def catalog():return jsonify(data.catalog())

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
        file=Path(app.config['EVIDENCE_ROOT'])/'comparison-v2.json'
        if not file.exists():
            return jsonify(available=False,message='Run the pipeline benchmark to publish evidence.')
        report=measured.benchmark(app.config['EVIDENCE_ROOT'])
        return jsonify(available=True,benchmark_group=report['benchmark_group'],
            measurement=report['measurement'],summaries=report['summary'],equality=report['equality'],
            histories=report['histories'],samples={engine:[{k:sample.get(k) for k in ('run_id','elapsed_seconds','input_games','container_peak_memory_mib','container_cpu_seconds','phases','source_month','code_hash','cpu_limit','memory_limit_mib')}
                for sample in samples] for engine,samples in report.get('samples',{}).items()})

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
        elif name=='benchmarks':value=measured.benchmark(app.config['EVIDENCE_ROOT'])
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
