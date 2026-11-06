"""Account isolation, unbounded/chunked imports and evidence-backed workspace behavior."""
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path

import pytest
pytest.importorskip('flask')
import pyarrow.parquet as pq

from test_personal import database,games_bytes
from chess_lakehouse.personal.workspace import storage as db,accounts,imports,insights,analysis,exports,worker
from chess_lakehouse.personal.workspace.config import Config
from chess_lakehouse.personal.workspace.application import create_app


@pytest.fixture
def config():return replace(Config(),batch_games=17,min_free_disk_bytes=0,engine_nodes=1000,engine_game_seconds=20)


def owner(name='DemoAccount'):
    return str(accounts.create(name,'a-valid-test-password')['id'])


def imported(owner_id,content,config,player='DemoStudent',mode='merge'):
    path=db.root()/('test-'+__import__('uuid').uuid4().hex+'.pgn');path.write_bytes(content)
    key=imports.create(owner_id,'file',player,mode,path)
    with db.connect() as c:record=imports.job(c,owner_id,key)
    imports.process(record,config)
    with db.connect() as c:record=imports.job(c,owner_id,key)
    assert record['status']=='complete',record['error']
    return record


def client(app,name):
    c=app.test_client();h={'Authorization':'Bearer internal-test'}
    response=c.post('/signup',json={'username':name,'password':'a-valid-test-password'},headers=h)
    assert response.status_code==200,response.json
    cookie=c.get_cookie(accounts.COOKIE,path='/api/personal')
    # Internal API paths are unprefixed; the real proxy keeps the browser cookie's public path.
    c.set_cookie(accounts.COOKIE,cookie.value,path='/')
    h['X-CSRF-Token']=response.json['user']['csrf']
    return c,h,response.json['user']['id']


def test_accounts_sessions_and_tenant_resources(database,config):
    app=create_app({'TOKEN':'internal-test','WORKER':False,'SETTINGS':config,'TESTING':True})
    anonymous=app.test_client()
    assert anonymous.get('/libraries',headers={'Authorization':'Bearer internal-test'}).status_code==401
    a,h,first=client(app,'FirstAccount');b,hb,second=client(app,'SecondAccount')
    record=imported(first,games_bytes(),config)
    lib=str(record['library_id']);snap=str(record['snapshot_id'])
    assert a.get('/libraries',headers=h).json['libraries'][0]['games']==8
    assert b.get('/libraries',headers=hb).json['libraries']==[]
    for route,body in [('report',{'library_id':lib}),('games',{'library_id':lib}),
            ('analysis',{'library_id':lib,'game_id':'demo0000'}),
            ('positions',{'library_id':lib,'game_id':'demo0000','ply':0}),
            ('review',{'library_id':lib,'game_id':'demo0000','ply':0,'status':'reviewed'}),
            ('assistant',{'library_id':lib,'question':'Summarize my games'}),
            ('studies',{'library_id':lib,'name':'stolen','filters':{}})]:
        assert b.post('/'+route,json=body,headers=hb).status_code==400,route
    assert b.get('/imports/'+str(record['id']),headers=hb).status_code==400
    assert b.get('/games/demo0000?library_id='+lib+'&snapshot_id='+snap,headers=hb).status_code==400
    assert b.get('/export?library_id='+lib,headers=hb).status_code==400
    folder=db.root()/'exports';before=set(folder.glob('*'))
    response=a.get('/export?library_id='+lib+'&format=pgn',headers=h)
    assert response.status_code==200 and b'[White "DemoStudent"]' in response.data
    response.close()
    assert set(folder.glob('*'))==before
    assert a.post('/report',json={'library_id':lib},headers={'Authorization':'Bearer internal-test'}).status_code==403
    assert a.post('/logout',json={},headers=h).status_code==200
    assert a.get('/libraries',headers=h).status_code==401


def test_multimonth_thousand_games_dedup_and_shared_revisions(database,config):
    account=owner()
    content=games_bytes(600,month='2026.09')+b'\n'+games_bytes(600,month='2026.10',start=600)
    first=imported(account,content,config)
    assert first['games_processed']==1200 and first['new_games']==1200
    second=imported(account,content,config)
    assert second['snapshot_id']==first['snapshot_id'] and second['duplicate_games']==1200
    with db.connect() as c:
        assert c.execute('SELECT count(*) AS n FROM study.game_revisions WHERE owner_id=%s',(account,)).fetchone()['n']==1200
        assert c.execute('SELECT count(*) AS n FROM study.library_snapshots WHERE owner_id=%s',(account,)).fetchone()['n']==1
    # The same source game studied from the other player's color shares its payload.
    imported(account,games_bytes(1,month='2026.09'),config,player='SyntheticOpponent')
    with db.connect() as c:assert c.execute('SELECT count(*) AS n FROM study.game_revisions WHERE owner_id=%s',(account,)).fetchone()['n']==1200
    r=insights.report(account,first['library_id'],selected={'from_date':'2026-09-01','to_date':'2026-09-30'},config=config)
    assert r['summary']['games']==600 and r['summary']['score_rate']==.5
    page=insights.games(account,first['library_id'],None,{},offset=10,limit=10)
    assert len(page['rows'])==10 and page['has_more']


def test_corrections_replace_saved_cohort_and_streamed_exports(database,config):
    account=owner();first=imported(account,games_bytes(),config)
    saved=exports.save(account,first['library_id'],first['snapshot_id'],'Original white games',{'color':'white'})
    changed=imported(account,games_bytes().replace(b'WhiteElo "1500"',b'WhiteElo "1510"'),config)
    assert changed['changed_games']==8 and changed['snapshot_id']!=first['snapshot_id']
    replaced=imported(account,games_bytes(1),config,mode='replace')
    assert insights.report(account,first['library_id'],config=config)['summary']['games']==1
    path,_,_,sha=exports.prepare(account,study_id=saved['id'],config=config)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
    table=pq.read_table(path);assert table.num_rows==8
    assert set(table['player_rating'].to_pylist())=={1500}
    assert 'initial_seconds' in table.column_names
    path.unlink()
    with db.connect() as c:assert c.execute('SELECT count(*) AS n FROM study.game_revisions WHERE owner_id=%s',(account,)).fetchone()['n']==16


def test_player_auto_detection_missing_dates_and_job_failure_atomicity(database,config):
    account=owner();content=games_bytes(2).replace(b'[Black "SyntheticOpponent"]',b'[Black "SecondOpponent"]',1)
    record=imported(account,content,config,player='')
    assert record['player']=='DemoStudent'
    unknown=imported(account,b'[White "DemoStudent"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n',config)
    with db.connect() as c:
        snap=db.snapshot(c,account,record['library_id']);rows=c.execute('SELECT game_date FROM study.workspace_game_facts WHERE owner_id=%s AND snapshot_id=%s AND game_id LIKE %s',(account,snap['id'],'local_%')).fetchall()
        assert rows[0]['game_date'] is None
    path=db.root()/'bad.pgn';path.write_bytes(games_bytes(2).replace(b'f3 e5',b'f3 e9'))
    key=imports.create(account,'file','DemoStudent','merge',path)
    with db.connect() as c:job=imports.job(c,account,key)
    imports.process(job,config)
    with db.connect() as c:
        assert imports.job(c,account,key)['status']=='failed'
        assert db.snapshot(c,account,record['library_id'])['id']==unknown['snapshot_id']


def test_lichess_url_validation_and_download_processing(database,config):
    for url in ('http://lichess.org/api/games/user/DemoStudent','https://evil.example/api/games/user/DemoStudent',
            'https://lichess.org.evil.example/api/games/user/DemoStudent','https://lichess.org@127.0.0.1/api/games/user/DemoStudent',
            'https://lichess.org/api/games/user/DemoStudent?max=0','https://lichess.org/api/account'):
        with pytest.raises(ValueError):imports.lichess_url(url)
    url,player,options=imports.lichess_url('https://lichess.org/api/games/user/DemoStudent?tags=false&amp;literate=true')
    assert player=='DemoStudent' and options['tags']=='true' and 'max' not in options
    account=owner();key=imports.create(account,'lichess','','merge',url=url)
    class Fake:
        def open(self,request,timeout):
            assert request.get_header('Accept')=='application/x-chess-pgn'
            return io.BytesIO(games_bytes())
    assert worker.import_one(config,Fake())
    with db.connect() as c:
        job=imports.job(c,account,key)
        assert job['status']=='complete' and job['games_processed']==8
        assert job['input_path'] is None


def test_grounded_findings_filters_and_real_engine(database,config):
    account=owner();record=imported(account,games_bytes(24),config)
    r=insights.report(account,record['library_id'],config=config)
    assert r['summary']['games']==24 and r['summary']['score_rate']==.5
    assert any(o['review_candidate'] for o in r['openings'])
    assert len(r['findings'])<=3 and len(r['suggested_games'])<=5
    response=analysis.answer(account,record['library_id'],None,'Summarize my games',{},config)
    assert response['metrics']['games']==24 and len(response['references'])<=3
    refused=analysis.answer(account,record['library_id'],None,'Will my rating reach 2000 next month?',{},config)
    assert refused['supported'] is False and refused['references']==[]
    job=analysis.enqueue(account,record['library_id'],None,'demo0000',config)
    assert worker.analyze_one(config)
    replay=analysis.game(account,record['library_id'],None,'demo0000')
    evidence=replay['analysis_jobs'][0]['evidence']
    assert any(c.get('motif')=='Allows checkmate in one' for c in evidence['candidates'])
    response=analysis.answer(account,record['library_id'],None,'What is the best move here?',{},config,'demo0000',2)
    assert response['supported'] and response['metrics']['pv_san']
    position=analysis.positions(account,record['library_id'],None,'demo0000',0,{})
    assert position['games']==24 and position['pending_games']==0
    assert {v['move'] for v in position['alternatives']}=={'f3','e4'}


def test_cancel_quotas_and_failure_leave_no_duplicate_or_orphan_payloads(database,config):
    account=owner();initial=imported(account,games_bytes(8),config)
    path=db.root()/'cancel.pgn';path.write_bytes(games_bytes(10,start=20))
    key=imports.create(account,'file','DemoStudent','merge',path)
    with db.connect() as c:
        c.execute('UPDATE study.import_jobs SET cancel_requested=true WHERE id=%s',(key,));record=imports.job(c,account,key)
    imports.process(record,config)
    assert not path.exists()
    with db.connect() as c:
        assert imports.job(c,account,key)['status']=='cancelled'
        assert db.snapshot(c,account,initial['library_id'])['id']==initial['snapshot_id']
    path=db.root()/'quota.pgn';path.write_bytes(games_bytes(3,start=20))
    key=imports.create(account,'file','DemoStudent','merge',path)
    with db.connect() as c:record=imports.job(c,account,key)
    imports.process(record,replace(config,max_library_games=9))
    with db.connect() as c:
        assert imports.job(c,account,key)['status']=='failed'
        assert c.execute('SELECT count(*) AS n FROM study.game_revisions WHERE owner_id=%s',(account,)).fetchone()['n']==8
        assert db.snapshot(c,account,initial['library_id'])['id']==initial['snapshot_id']


def test_lichess_rate_limit_retry_and_redirect_refusal(database,config,monkeypatch):
    import urllib.error
    account=owner();key=imports.create(account,'lichess','','merge',url='https://lichess.org/api/games/user/DemoStudent')
    slept=[];monkeypatch.setattr(imports.time,'sleep',lambda seconds:slept.append(seconds))
    class Throttled:
        count=0
        def open(self,request,timeout):
            self.count+=1
            if self.count==1:raise urllib.error.HTTPError(request.full_url,429,'slow down',{},None)
            return io.BytesIO(games_bytes(2))
    assert worker.import_one(config,Throttled())
    assert sum(slept)==60
    with db.connect() as c:assert imports.job(c,account,key)['status']=='complete'
    with pytest.raises(ValueError,match='redirected'):imports.NoRedirect().redirect_request(None,None,None,None,None,None)


def test_account_password_change_revokes_other_sessions(database,config):
    app=create_app({'TOKEN':'internal-test','WORKER':False,'SETTINGS':config,'TESTING':True})
    c,h,account=client(app,'PasswordAccount')
    token,_=accounts.new_session(account)
    response=c.post('/password',json={'current_password':'a-valid-test-password','new_password':'a-new-strong-password'},headers=h)
    assert response.status_code==200 and accounts.session(token) is None
    with pytest.raises(ValueError,match='incorrect'):accounts.login('PasswordAccount','a-valid-test-password')
    assert accounts.login('PasswordAccount','a-new-strong-password')['id']==account


def test_legacy_studies_notes_and_versions_are_preserved_without_payload_duplication(database,config):
    from chess_lakehouse.personal import repository as legacy,research
    from chess_lakehouse.personal.settings import Settings
    from chess_lakehouse.personal.workspace.migrate_legacy import migrate
    settings=replace(Settings(),player='DemoStudent')
    first=legacy.publish(games_bytes(),settings,settings.player,settings.month)
    saved=research.save(settings,first['import_id'],'Legacy original',{})
    second=legacy.publish(games_bytes(4,start=8),settings,settings.player,settings.month)
    with db.connect() as c:c.execute("INSERT INTO study.review_items(import_id,game_id,ply,status,note) VALUES(%s,'demo0000',0,'reviewed','Synthetic legacy note')",(first['import_id'],))
    account=owner()
    assert migrate(account)==2 and migrate(account)==0
    with db.connect() as c:
        lib=c.execute('SELECT * FROM study.libraries WHERE owner_id=%s',(account,)).fetchone()
        assert db.snapshot(c,account,lib['id'])['game_count']==12
        assert c.execute('SELECT count(*) AS n FROM study.game_revisions WHERE owner_id=%s',(account,)).fetchone()['n']==12
        assert c.execute('SELECT count(*) AS n FROM study.workspace_reviews WHERE owner_id=%s',(account,)).fetchone()['n']==1
    path,*_=exports.prepare(account,study_id=saved['id'],format='manifest',config=config)
    assert json.loads(path.read_text())['rows']==8
    path.unlink()


def test_unknown_dates_unrelated_unfinished_and_empty_games_are_explained(database,config):
    account=owner()
    content=games_bytes(1)+b'\n'+games_bytes(1,player='Unrelated',start=5)+b'\n'+games_bytes(1,start=1).replace(b'0-1',b'*').replace(b'2. g4 Qh4#',b'')
    content+=b'\n[White "DemoStudent"]\n[Black "SyntheticOpponent"]\n[Result "1-0"]\n\n1-0\n'
    record=imported(account,content,config)
    assert record['new_games']==1 and record['rejected_games']==3
    assert len(record['diagnostics'])==3
    assert 'no moves' in record['diagnostics'][-1]['reason']
