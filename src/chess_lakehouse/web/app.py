"""Local WSGI app: read-only SQL analytics, historical evidence and ML inference."""
import json
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request
import markdown
import numpy as np
import psycopg
from werkzeug.exceptions import HTTPException

from . import data
from ..ml.artifacts import Artifacts, ArtifactError
from ..ml.features import prediction_input, vector
from ..personal.proxy import register as register_personal

CASES={'outcome':('Predicting game outcomes','ml-outcome.md'),
    'length':('Estimating game length','ml-length.md'),
    'clusters':('Describing player groups','ml-clusters.md')}


def create_app(config=None):
    app=Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=4096,JSON_SORT_KEYS=False,
        ML_ROOT=os.environ.get('ML_ROOT','/models'),EVIDENCE_ROOT=os.environ.get('EVIDENCE_ROOT','/evidence'),
        DOCS_ROOT=os.environ.get('DOCS_ROOT','/app/docs'))
    app.config.update(config or {})
    artifacts=Artifacts(app.config['ML_ROOT'])
    register_personal(app)

    @app.after_request
    def headers(response):
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Cache-Control':'no-store' if request.path.startswith('/api/') else 'no-cache'})
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc,HTTPException):
            return jsonify(error=exc.name),exc.code
        if isinstance(exc,ArtifactError):
            return jsonify(error=str(exc)),503
        if isinstance(exc,ValueError):
            return jsonify(error=str(exc)),400
        if isinstance(exc,(psycopg.Error,OSError,KeyError,json.JSONDecodeError)):
            logging.warning('Dashboard dependency unavailable: %s',type(exc).__name__)
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

    @app.get('/api/games')
    def games():
        return jsonify(data.games(request.args))

    @app.get('/api/benchmarks')
    def benchmarks():
        file=Path(app.config['EVIDENCE_ROOT'])/'comparison-v2.json'
        if not file.exists():
            return jsonify(available=False,message='Run the pipeline benchmark to publish evidence.')
        report=json.loads(file.read_text())
        return jsonify(available=True,benchmark_group=report['benchmark_group'],
            measurement=report['measurement'],summaries=report['summary'],equality=report['equality'],
            histories=report['histories'])

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
            scope='Historical January 2013 demo; probabilities are not guarantees.')

    @app.get('/case-studies/<name>')
    def case_study(name):
        if name not in CASES:
            return jsonify(error='Unknown case study'),404
        title,filename=CASES[name]
        content=(Path(app.config['DOCS_ROOT'])/filename).read_text()
        # Checked-in documentation is trusted, never user-supplied Markdown.
        return render_template('case.html',title=title,content=markdown.markdown(content,extensions=['tables','fenced_code']))

    return app
