"""Explicit bounded training, immutable artifacts and honest temporal evaluation."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

import joblib
import numpy as np
from psycopg import sql
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, adjusted_rand_score, balanced_accuracy_score,
    confusion_matrix, f1_score, log_loss, mean_absolute_error, mean_squared_error,
    r2_score, silhouette_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .features import FEATURES, vector
from .calibration import reliability
from ..web.data import ENGINES, connect, source_signature, filters

SEED=42
CLUSTER_FEATURES=('mean_rating','mean_moves','win_rate','draw_rate','bullet_share','blitz_share','rapid_share')


def digest(path):
    """Compute an artifact file checksum for the trusted model publication manifest."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def temporal_split(rows):
    """Split observations chronologically to keep later evaluation games outside training."""
    days=sorted(set(r['date'] for r in rows))
    if len(days)<10:
        raise ValueError('at least ten distinct game dates are required')
    a,b=max(1,int(len(days)*.6)),max(2,int(len(days)*.8))
    groups=[[i for i,r in enumerate(rows) if r['date'] in set(part)] for part in (days[:a],days[a:b],days[b:])]
    if min(map(len,groups))<100:
        raise ValueError('at least 100 observations are required in every chronological split')
    return [np.array(g) for g in groups]


def extract(engine,month,max_games):
    """Read the selected historical features and targets for explicit local model training."""
    filters({'engine':engine,'month':month})
    with connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        signature=source_signature(c,engine,month)
        schema=sql.Identifier('warehouse_'+engine)
        rows=c.execute(sql.SQL('''SELECT f.game_id,f.date_key::text AS date,f.white_rating,f.black_rating,
            f.result,f.move_count,f.speed,t.initial_seconds,t.increment_seconds
            FROM {}.fact_game f JOIN {}.dim_time_control t ON t.time_control_sk=f.time_control_sk
            WHERE f.source_month=%s ORDER BY md5(f.game_id),f.game_id LIMIT %s''').format(schema,schema),
            (month+'-01',max_games)).fetchall()
        # Player IDs are only used inside SQL aggregation and are not exported.
        players=c.execute(sql.SQL('''WITH observations AS (
            SELECT white_player_sk AS player_sk,white_rating AS rating,move_count,speed,
                (result='1-0')::int AS win,(result='1/2-1/2')::int AS draw
            FROM {}.fact_game WHERE source_month=%s
            UNION ALL SELECT black_player_sk,black_rating,move_count,speed,
                (result='0-1')::int,(result='1/2-1/2')::int FROM {}.fact_game WHERE source_month=%s)
            SELECT avg(rating)::float AS mean_rating,avg(move_count)::float AS mean_moves,
                avg(win)::float AS win_rate,avg(draw)::float AS draw_rate,
                avg((speed IN ('bullet','ultrabullet'))::int)::float AS bullet_share,
                avg((speed='blitz')::int)::float AS blitz_share,avg((speed='rapid')::int)::float AS rapid_share,
                count(*) AS games FROM observations x JOIN {}.dim_player p USING(player_sk)
            WHERE NOT p.is_unknown GROUP BY p.player_hash HAVING count(*)>=10 AND count(rating)>=5
            ORDER BY md5(encode(p.player_hash,'hex')) LIMIT 20000''').format(schema,schema,schema),
            (month+'-01',month+'-01')).fetchall()
        return rows,players,signature


def classification_metrics(model,x,y):
    """Compute classification quality, class-level results and probability diagnostics."""
    pred=model.predict(x)
    return {'accuracy':float(accuracy_score(y,pred)),
        'balanced_accuracy':float(balanced_accuracy_score(y,pred)),
        'macro_f1':float(f1_score(y,pred,labels=model.classes_,average='macro',zero_division=0)),
        'log_loss':float(log_loss(y,model.predict_proba(x),labels=model.classes_)),
        'classes':list(model.classes_),'confusion_matrix':confusion_matrix(y,pred,labels=model.classes_).tolist(),
        'calibration':reliability(model,x,y),
        'class_support':{str(label):int(sum(np.asarray(y)==label)) for label in model.classes_},
        'balanced_accuracy_scope':'Mean recall over classes observed in this evaluation sample.'}


def regression_metrics(model,x,y):
    """Compute held-out regression errors and relevant baseline comparisons."""
    pred=model.predict(x)
    return {'mae_moves':float(mean_absolute_error(y,pred)),
        'rmse_moves':float(np.sqrt(mean_squared_error(y,pred))),'r2':float(r2_score(y,pred))}


def fit_models(rows,players):
    """Fit the bounded case-study estimators using the specified historical training split."""
    tr,va,te=temporal_split(rows)
    x=np.array([vector(r) for r in rows],dtype=float)
    y=np.array([r['result'] for r in rows])
    length=np.array([r['move_count'] for r in rows],dtype=float)
    if any(len(set(y[part]))!=3 for part in (tr,va,te)):
        raise ValueError('all three result classes must occur in every temporal split')
    outcome_candidates=[]
    for strength in (.1,1.0):
        model=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler(),
            LogisticRegression(C=strength,max_iter=1000,random_state=SEED))
        model.fit(x[tr],y[tr])
        outcome_candidates.append((log_loss(y[va],model.predict_proba(x[va]),labels=model.classes_),strength,model))
    _,strength,outcome=min(outcome_candidates,key=lambda v:v[0])
    ratings_candidates=[]
    for strength_ratings in (.1,1.0):
        ratings=make_pipeline(ColumnTransformer([('ratings','passthrough',[0,1])]),
            SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler(),
            LogisticRegression(C=strength_ratings,max_iter=1000,random_state=SEED))
        ratings.fit(x[tr],y[tr])
        ratings_candidates.append((log_loss(y[va],ratings.predict_proba(x[va]),labels=ratings.classes_),strength_ratings,ratings))
    _,ratings_strength,ratings=min(ratings_candidates,key=lambda v:v[0])
    length_candidates=[]
    for depth in (6,10):
        model=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),
            RandomForestRegressor(n_estimators=60,max_depth=depth,min_samples_leaf=20,n_jobs=2,random_state=SEED))
        model.fit(x[tr],length[tr])
        length_candidates.append((mean_absolute_error(length[va],model.predict(x[va])),depth,model))
    _,depth,regressor=min(length_candidates,key=lambda v:v[0])
    fit=np.concatenate([tr,va])
    outcome.fit(x[fit],y[fit]); regressor.fit(x[fit],length[fit])
    ratings.fit(x[fit],y[fit])
    prior=DummyClassifier(strategy='prior').fit(x[fit],y[fit])
    median=DummyRegressor(strategy='median').fit(x[fit],length[fit])
    split={name:{'rows':len(index),'first_date':min(rows[i]['date'] for i in index),
        'last_date':max(rows[i]['date'] for i in index),
        'class_counts':{str(label):int(sum(y[index]==label)) for label in sorted(set(y))}}
        for name,index in zip(('train','validation','test'),(tr,va,te))}
    report={'split':split,'features':list(FEATURES),'outcome':{
        'algorithm':'Multinomial logistic regression','selection':{'C':strength,'validation_log_loss':[
            {'C':c,'log_loss':float(score)} for score,c,_ in outcome_candidates]},
        'test':classification_metrics(outcome,x[te],y[te]),'baseline':classification_metrics(prior,x[te],y[te]),
        'ratings_baseline':classification_metrics(ratings,x[te],y[te]),
        'ratings_selection':{'C':ratings_strength,'validation_log_loss':[
            {'C':c,'log_loss':float(score)} for score,c,_ in ratings_candidates]},
        'rating_difference_coefficients':{label:float(coef[2]) for label,coef in zip(outcome.classes_,outcome[-1].coef_)}},
        'length':{'algorithm':'Random forest regression','selection':{'max_depth':depth,'validation_mae':[
            {'max_depth':d,'mae_moves':float(score)} for score,d,_ in length_candidates]},
        'test':regression_metrics(regressor,x[te],length[te]),'baseline':regression_metrics(median,x[te],length[te]),
        'feature_importance':dict(zip(FEATURES,map(float,regressor[-1].feature_importances_)))}}
    # Evaluate slices on the untouched final days to reveal speed-specific weaknesses.
    report['test_slices']=[]
    for speed in sorted(set(rows[i]['speed'] for i in te)):
        idx=np.array([i for i in te if rows[i]['speed']==speed])
        report['test_slices'].append({'speed':speed,'rows':len(idx),
            'accuracy':float(accuracy_score(y[idx],outcome.predict(x[idx]))),
            'mae_moves':float(mean_absolute_error(length[idx],regressor.predict(x[idx])))})
    if len(players)<20:
        raise ValueError('at least 20 eligible players are required for clustering')
    px=np.array([[r[f] for f in CLUSTER_FEATURES] for r in players])
    scaler=StandardScaler().fit(px); z=scaler.transform(px)
    candidates=[]
    for k in (2,3,4):
        model=KMeans(n_clusters=k,n_init=10,random_state=SEED).fit(z)
        score=silhouette_score(z,model.labels_,sample_size=min(2000,len(z)),random_state=SEED)
        candidates.append((score,k,model))
    score,k,km=max(candidates,key=lambda v:v[0])
    repeat=KMeans(n_clusters=k,n_init=10,random_state=SEED+1).fit(z)
    report['clusters']={'algorithm':'Standardized K-means','players':len(players),'minimum_games':10,
        'features':list(CLUSTER_FEATURES),'selected_k':k,'silhouette':float(score),
        'seed_stability_ari':float(adjusted_rand_score(km.labels_,repeat.labels_)),
        'one_cluster_inertia':float(np.square(z-z.mean(axis=0)).sum()),
        'candidates':[{'k':n,'silhouette':float(s),'inertia':float(m.inertia_)} for s,n,m in candidates],
        'groups':[{'cluster':j+1,'players':int(sum(km.labels_==j)),
            **{f:float(px[km.labels_==j,i].mean()) for i,f in enumerate(CLUSTER_FEATURES)}} for j in range(k)]}
    return {'outcome':outcome,'ratings':ratings,'prior':prior,'length':regressor,
        'length_baseline':median,'clusters':make_pipeline(scaler,km)},report


def train(engine='python',month='2013-01',max_games=120000,root=None):
    """Train and atomically publish versioned local models with honest evaluation and provenance."""
    if not os.environ.get('CHESS_CONTAINER'):
        raise RuntimeError('train inside Docker')
    if not 1000<=max_games<=150000:
        raise ValueError('max_games must be between 1000 and 150000')
    # Preserve the explicitly approved real-source boundary.
    if month!='2013-01':
        raise ValueError('ML training is restricted to the approved January 2013 archive')
    from ..runtime import load as runtime
    root=Path(root or runtime().paths.models); root.mkdir(parents=True,exist_ok=True)
    with (root/'.training.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        start=time.monotonic()
        rows,players,signature=extract(engine,month,max_games)
        with threadpool_limits(limits=2):
            models,report=fit_models(rows,players)
        run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
        temporary=root/('.'+run_id); temporary.mkdir()
        try:
            for name,model in models.items():
                joblib.dump(model,temporary/(name+'.joblib'),compress=3)
            report.update({'schema_version':2,'run_id':run_id,'created_at':datetime.now(timezone.utc).isoformat(),
                'engine':engine,'month':month,'source_signature':signature,'sample_rows':len(rows),
                'sampling':'lowest md5(game_id), deterministic and independent of targets','max_games':max_games,
                'seed':SEED,'elapsed_seconds':time.monotonic()-start,
                'versions':{p:importlib.metadata.version(p) for p in ('scikit-learn','numpy','scipy','joblib')},
                'training_code_sha256':hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))).hexdigest(),
                'limitations':['One historical source month; not validated for modern games.',
                    'Game dates provide day-level chronology; players can overlap across splits.',
                    'Opening and post-game attributes are excluded from predictors.',
                    'Clustering is retrospective description, not player skill or cheating detection.',
                    'No causal, fairness, unseen-player or production generalization claim.']})
            (temporary/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
            manifest={'run_id':run_id,'files':{p.name:digest(p) for p in temporary.iterdir()}}
            (temporary/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
            temporary.rename(root/run_id)
            latest=root/('.latest-'+uuid4().hex)
            latest.write_text(json.dumps({'run_id':run_id})+'\n'); latest.replace(root/'latest.json')
        except BaseException:
            shutil.rmtree(temporary,ignore_errors=True)
            raise
        print(json.dumps({'event':'ml_training_complete','run_id':run_id,'rows':len(rows),'elapsed_seconds':report['elapsed_seconds']}),flush=True)
        return report


def main():
    """Parse the explicit container training command and its publication options."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',choices=ENGINES,default='python')
    parser.add_argument('--month',default='2013-01')
    parser.add_argument('--max-games',type=int,default=120000)
    args=parser.parse_args(); report=train(args.engine,args.month,args.max_games)
    destination=Path('/reports/ml-evaluation.json')
    destination.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
