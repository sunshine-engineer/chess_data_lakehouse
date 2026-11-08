"""Explicit frozen-model evaluation on a later approved personal version."""
import os
from uuid import uuid4

import numpy as np
from psycopg.types.json import Jsonb

from . import repository as db
from ..ml.artifacts import Artifacts
from ..ml.features import vector
from ..ml.train import classification_metrics,regression_metrics


def validate(settings,identifier):
    """Check a legacy private import against its approval, payload and publication contract."""
    artifacts=Artifacts(os.environ.get('ML_ROOT','/models'))
    model_report=artifacts.report()
    if model_report['schema_version']!=2:
        raise ValueError('Retrain the January benchmark models to add the ratings-only baseline and reliability diagnostics.')
    with db.connect() as c:
        v=db.version(c,settings,identifier)
        previous=c.execute('SELECT * FROM study.validations WHERE import_id=%s AND model_run_id=%s',
            (v['id'],model_report['run_id'])).fetchone()
        if previous:
            return dict(id=str(previous['id']),report=previous['report'],cached=True)
        rows=db.payloads(v)
        if any('game_date' in r.get('metadata_defaults',[]) for r in rows):
            raise ValueError('Later-data model validation needs actual game dates; this version contains month-defaulted dates.')
        if len(rows)<2:
            raise ValueError('Model evaluation needs at least two dated games; a single game can still be imported and studied.')
        if min(r['game_date'] for r in rows)<=model_report['split']['test']['last_date']:
            raise ValueError('Personal evaluation games must be strictly later than every benchmark train/validation/test date.')
        # Evaluation is frozen: no fitting, tuning or threshold selection on personal games.
        x=np.array([vector(r) for r in rows],dtype=float)
        y=np.array([r['result'] for r in rows])
        length=np.array([(len(r['moves'])+1)//2 for r in rows],dtype=float)
        metrics={name:classification_metrics(artifacts.model(model_report['run_id'],name),x,y)
            for name in ('outcome','ratings','prior')}
        report=dict(import_id=str(v['id']),dataset_sha256=v['dataset_sha256'],rows=len(rows),
            small_sample=len(rows)<20,
            sample_warning='Fewer than 20 games: descriptive metrics only; too little evidence to estimate generalization.' if len(rows)<20 else
                'One player-month: results do not establish population generalization.',
            player=settings.player,month=settings.month,model_run_id=model_report['run_id'],
            benchmark_last_date=model_report['split']['test']['last_date'],
            first_personal_date=min(r['game_date'] for r in rows),last_personal_date=max(r['game_date'] for r in rows),
            models=metrics,length={name:regression_metrics(artifacts.model(model_report['run_id'],name),x,length)
                for name in ('length','length_baseline')},
            scope='Single approved player-month. This is a later-data transfer check, not a representative modern-population benchmark or proof of improvement.',
            source='Manually imported personal games; frozen historical models; no model fitting on this version.')
        identifier=str(uuid4())
        c.execute('INSERT INTO study.validations(id,import_id,model_run_id,report) VALUES(%s,%s,%s,%s) ON CONFLICT(import_id,model_run_id) DO NOTHING',
            (identifier,v['id'],model_report['run_id'],Jsonb(report)))
        return dict(id=identifier,report=report,cached=False)
