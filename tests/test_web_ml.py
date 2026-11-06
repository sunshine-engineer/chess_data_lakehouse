"""Real reader queries, isolated temporal ML and application contracts in Docker."""
from datetime import date,timedelta
import hashlib
import importlib.metadata
import json
import os
from uuid import uuid4

import pytest
np=pytest.importorskip('numpy')
pytest.importorskip('sklearn')
import joblib
import psycopg
from psycopg import sql

from chess_lakehouse.ml.features import FEATURES,prediction_input,vector
from chess_lakehouse.ml.train import fit_models,temporal_split,train
from chess_lakehouse.ml.artifacts import Artifacts
from chess_lakehouse.web import data
from chess_lakehouse.web.app import create_app
from chess_lakehouse.optimized.migrate import admin_connect,migrate


def synthetic_rows():
    rng=np.random.default_rng(42)
    rows=[]
    for day in range(30):
        for j in range(35):
            w,b=map(int,rng.integers(800,2400,2))
            rows.append(dict(game_id=f'{day:02}{j:06}',date=(date(2013,1,1)+timedelta(days=day)).isoformat(),
                white_rating=w,black_rating=b,initial_seconds=300,increment_seconds=0,speed='blitz',
                result=('1-0','0-1','1/2-1/2')[j%3],move_count=20+j))
    players=[dict(mean_rating=900+i*30,mean_moves=20+i%8,win_rate=.3+i%4*.1,draw_rate=.03,
        bullet_share=(i%3)/3,blitz_share=.2,rapid_share=.2) for i in range(40)]
    return rows,players


def test_pre_game_contract_excludes_post_game_information():
    assert not set(FEATURES)&{'result','move_count','ply_count','opening','termination','latest_title'}
    payload=dict(white_rating=1500,black_rating=1400,initial_seconds=10,increment_seconds=0)
    assert prediction_input(payload)['speed']=='ultrabullet'
    assert len(vector(prediction_input(payload)))==len(FEATURES)
    for bad in (float('nan'),True,-1,5000):
        with pytest.raises(ValueError):prediction_input({**payload,'white_rating':bad})
    with pytest.raises(ValueError):prediction_input({'result':'1-0'})


def test_split_keeps_days_disjoint_and_rejects_insufficient_coverage():
    rows,_=synthetic_rows(); groups=temporal_split(rows)
    days=[{rows[i]['date'] for i in g} for g in groups]
    assert max(days[0])<min(days[1])<=max(days[1])<min(days[2])
    assert sum(map(len,groups))==len(rows)
    with pytest.raises(ValueError):temporal_split(rows[:20])


def test_models_evaluate_real_baselines_and_all_classes():
    rows,players=synthetic_rows(); models,report=fit_models(rows,players)
    assert report['split']['test']['rows']==210
    assert report['outcome']['test']['classes']==['0-1','1-0','1/2-1/2']
    assert np.isfinite(report['outcome']['test']['log_loss'])
    assert report['length']['baseline']['mae_moves']>0
    assert sum(g['players'] for g in report['clusters']['groups'])==40
    assert set(models)=={'outcome','length','clusters'}
    x=np.array([vector({**rows[0],'white_rating':None,'initial_seconds':None})])
    assert np.isclose(models['outcome'].predict_proba(x).sum(),1)


@pytest.fixture
def warehouse(monkeypatch):
    admin=admin_connect();admin.autocommit=True
    name='chess_web_test_'+uuid4().hex[:10]
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        monkeypatch.setenv('PGDATABASE',name);migrate()
        rows,_=synthetic_rows()
        with psycopg.connect(host=os.environ['PGHOST'],dbname=name,user=os.environ['PGADMIN_USER'],password=os.environ['PGADMIN_PASSWORD']) as c:
            s='warehouse_python'
            c.execute("INSERT INTO control.pipeline_runs(run_id,engine,source_month,source_sha256,benchmark_group,config_hash,code_hash,image_id,settings,runtime,status) VALUES('fixture','python','2013-01-01',%s,'fixture','','','',%s,%s,'complete')",('a'*64,'{}','{}'))
            c.execute("SELECT control.ensure_partition('python','2013-01-01')")
            c.execute(f"INSERT INTO {s}.dim_player(player_hash,is_unknown) VALUES(%s,false),(%s,true)",(b'a'*32,b'b'*32))
            c.execute(f"INSERT INTO {s}.dim_opening(opening_hash,eco,family) VALUES(%s,'C20','Test opening')",(b'c'*32,))
            c.execute(f"INSERT INTO {s}.dim_time_control(control_code,initial_seconds,increment_seconds,speed) VALUES('300+0',300,0,'blitz')")
            for r in rows:
                c.execute(f"INSERT INTO {s}.dim_date VALUES(%s,2013,1,1,0) ON CONFLICT DO NOTHING",(r['date'],))
                c.execute(f"INSERT INTO {s}.game_identity VALUES(%s,'2013-01-01')",(r['game_id'],))
                c.execute(f"""INSERT INTO {s}.fact_game(source_month,game_id,date_key,white_player_sk,black_player_sk,
                    opening_sk,time_control_sk,white_rating,black_rating,average_rating,rating_difference,result,
                    winner_color,ply_count,move_count,speed,source_record_number,run_id)
                    VALUES('2013-01-01',%s,%s,1,2,1,1,%s,%s,%s,%s,%s,%s,%s,%s,'blitz',1,'fixture')""",
                    (r['game_id'],r['date'],r['white_rating'],r['black_rating'],(r['white_rating']+r['black_rating'])/2,
                     r['white_rating']-r['black_rating'],r['result'],{'1-0':'white','0-1':'black','1/2-1/2':'draw'}[r['result']],r['move_count']*2,r['move_count']))
        monkeypatch.setenv('PGUSER','chess_reader');monkeypatch.setenv('PGPASSWORD',os.environ['READER_PASSWORD'])
        yield name
    finally:
        admin.execute(sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(name)));admin.close()


def test_reader_aggregates_pagination_and_sql_boundaries(warehouse):
    assert data.overview({})['summary']['games']==1050
    assert data.overview({'speed':'rapid'})['summary']['games']==0
    page=data.games({'offset':25,'limit':25})
    assert len(page['rows'])==25 and page['has_more']
    assert data.games({'min_rating':4000})['rows']==[]
    for args in ({'engine':'python;DROP SCHEMA public'},{'month':"2013-01' OR true--"},{'speed':'evil'},{'offset':10001},{'limit':1000},{'min_rating':2000,'max_rating':1000}):
        with pytest.raises(ValueError):data.games(args)
    with data.connect() as c:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):c.execute('DELETE FROM warehouse_python.fact_game')


def test_app_pages_errors_missing_models_and_evidence(warehouse,tmp_path):
    app=create_app({'TESTING':True,'ML_ROOT':tmp_path,'EVIDENCE_ROOT':tmp_path})
    c=app.test_client()
    assert c.get('/').status_code==200
    assert c.get('/static/app.js').status_code==200
    assert c.get('/healthz').json=={'status':'ok'}
    assert c.get('/api/overview').json['summary']['games']==1050
    assert c.get('/api/benchmarks').json['available'] is False
    assert c.get('/api/models').json['available'] is False
    assert c.get('/api/games?limit=5000').status_code==400
    assert c.post('/api/predict/outcome',json={'white_rating':float('inf')}).status_code==400
    assert c.post('/api/predict/not-real',json={}).status_code==404
    assert c.get('/case-studies/outcome').status_code==200
    assert c.get('/case-studies/unknown').status_code==404
    assert "frame-ancestors 'none'" in c.get('/').headers['Content-Security-Policy']
    assert c.post('/api/predict/outcome',data='x'*5000,content_type='application/json').status_code==413


def test_artifacts_detect_corruption_and_path_escape(tmp_path):
    (tmp_path/'latest.json').write_text(json.dumps({'run_id':'../../etc'}))
    with pytest.raises(ValueError):Artifacts(tmp_path).report()
    run_id='20130101T000000Z-aaaaaaaa';p=tmp_path/run_id;p.mkdir()
    files=['report.json','outcome.joblib','length.joblib','clusters.joblib']
    for f in files:(p/f).write_text('bad')
    (tmp_path/'latest.json').write_text(json.dumps({'run_id':run_id}))
    (p/'manifest.json').write_text(json.dumps({'run_id':run_id,'files':{f:'0'*64 for f in files}}))
    with pytest.raises(ValueError,match='corrupt'):Artifacts(tmp_path).report()


def test_saved_models_inference_stale_publication_and_corrupt_artifacts(warehouse,tmp_path):
    rows,players=synthetic_rows();models,report=fit_models(rows,players)
    run_id='20130101T000000Z-bbbbbbbb';folder=tmp_path/run_id;folder.mkdir()
    with data.connect() as c:signature=data.source_signature(c,'python','2013-01')
    report.update(schema_version=1,run_id=run_id,engine='python',month='2013-01',source_signature=signature,
        versions={p:importlib.metadata.version(p) for p in ('scikit-learn','numpy','joblib','scipy')})
    for name,model in models.items():joblib.dump(model,folder/(name+'.joblib'))
    (folder/'report.json').write_text(json.dumps(report))
    manifest={'run_id':run_id,'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}}
    (folder/'manifest.json').write_text(json.dumps(manifest));(tmp_path/'latest.json').write_text(json.dumps({'run_id':run_id}))
    c=create_app({'TESTING':True,'ML_ROOT':tmp_path}).test_client()
    assert c.get('/api/models').json['fresh']
    payload=dict(white_rating=1500,black_rating=1400,initial_seconds=300,increment_seconds=0)
    probabilities=c.post('/api/predict/outcome',json=payload).json['probabilities']
    assert abs(sum(probabilities.values())-1)<1e-9
    assert c.post('/api/predict/length',json=payload).json['estimated_moves']>0
    with admin_connect() as admin:
        admin.execute("INSERT INTO control.pipeline_runs(run_id,engine,source_month,source_sha256,benchmark_group,config_hash,code_hash,image_id,settings,runtime,status) VALUES('replacement','python','2013-01-01',%s,'fixture','','','','{}','{}','complete')",('b'*64,))
        admin.execute("UPDATE warehouse_python.fact_game SET run_id='replacement'")
    assert c.get('/api/models').json['fresh'] is False
    assert c.post('/api/predict/outcome',json=payload).status_code==409
    (folder/'outcome.joblib').write_bytes(b'corrupt')
    assert c.get('/api/models').status_code==503


def test_training_month_and_row_limits_cannot_expand_source(tmp_path):
    with pytest.raises(ValueError,match='January'):train(month='2013-02',root=tmp_path)
    with pytest.raises(ValueError,match='max_games'):train(max_games=1000000,root=tmp_path)
