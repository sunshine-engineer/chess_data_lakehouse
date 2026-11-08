"""Ownership, restartable full coverage, exercise evidence, and archive controls."""
import copy
from uuid import uuid4

import pytest
pytest.importorskip('flask')
import chess
from psycopg.types.json import Jsonb

from test_personal import database,games_bytes
from test_workspace import config,owner,imported,client
from chess_lakehouse import archive_catalog
from chess_lakehouse.personal.workspace import repertoire as r,analysis,storage as db,archives
from chess_lakehouse.personal.workspace.application import create_app


def candidate():
    board=chess.Board();initial=board.fen();board.push_uci('e2e4')
    return dict(ply=1,played_uci='f2f3',played_san='f3',suggested_san='e4',loss_cp=200,
        alternatives=[dict(score_value=150,score_cp=150,pv_uci=['e2e4'],pv_san=['e4'],positions=[initial,board.fen()],score_label='+1.50 for you',explanation='Synthetic legal line.')])


def test_exercises_exclude_losing_defenses_and_wrong_side():
    payload=dict(initial_fen=chess.STARTING_FEN,moves=['f2f3'],player_color='white');c=candidate()
    result=list(r.extract(payload,{'candidates':[c]}));assert len(result)==1 and result[0]['evidence']['accepted']==['e2e4']
    assert result[0]['phase']=='opening'
    bad=copy.deepcopy(c);bad['alternatives'][0]['score_value']=-300
    assert not list(r.extract(payload,{'candidates':[bad]}))
    assert not list(r.extract({**payload,'player_color':'black'},{'candidates':[c]}))
    bad=copy.deepcopy(c);bad['alternatives'][0]['pv_uci']=['e2e5']
    assert not list(r.extract(payload,{'candidates':[bad]}))
    end=chess.Board('7k/8/8/8/8/8/6Q1/4K3 w - - 0 20')
    assert r.phase(end,40)=='endgame' and r.phase(chess.Board(),21)=='middlegame'


def test_full_collection_pause_resume_idempotence_and_attempt_ownership(database,config,monkeypatch):
    who=owner();record=imported(who,games_bytes(35),config)
    build=r.start(who,{'library_id':str(record['library_id']),'archive_fallback':False})['build']
    assert sum(row['games'] for row in r.status(who,{'build_id':str(build['id'])})['counts'])==35
    assert r.start(who,{'library_id':str(record['library_id']),'archive_fallback':False})['build']['id']==build['id']
    r.control(who,{'build_id':str(build['id']),'action':'pause'});assert not r.run_one(config)
    monkeypatch.setattr(r.engine,'analyze',lambda *args,**kwargs:{'candidates':[candidate()],'engine':'Synthetic searched evidence'})
    r.control(who,{'build_id':str(build['id']),'action':'resume'})
    for _ in range(36):r.run_one(config)
    status=r.status(who,{'build_id':str(build['id'])});assert status['build']['status']=='complete'
    assert status['counts']==[{'status':'complete','source_month':None,'games':35}]
    listing=r.listing(who,{'build_id':str(build['id'])});assert len(listing['exercises'])==20 and listing['has_more']
    exercise=listing['exercises'][0];detail=r.exercise(who,{'exercise_id':str(exercise['id'])})
    assert 'evidence' not in detail and 'e2e4' in detail['legal_moves']
    value={'exercise_id':str(exercise['id']),'move':'e2e4','attempt_id':str(uuid4())}
    assert r.attempt(who,value)['correct'] and r.attempt(who,value)['correct']
    with pytest.raises(ValueError):r.attempt(who,{**value,'move':'d2d4'})
    other=owner('AnotherRepertoire')
    for function,value in [(r.status,{'build_id':str(build['id'])}),(r.listing,{'build_id':str(build['id'])}),(r.exercise,{'exercise_id':str(exercise['id'])}),(r.attempt,value)]:
        with pytest.raises(ValueError):function(other,value)


def test_selected_game_narration_and_loss_auto_review(database,config):
    who=owner();record=imported(who,games_bytes(1),config);lib=record['library_id']
    text=analysis.answer(who,lib,None,'Summarize the game?',{},config,'demo0000',3)['answer']
    assert 'SyntheticOpponent' in text and 'checkmate' in text and 'position 3' in text and '0.0%' not in text
    response=analysis.answer(who,lib,None,'Why did I lose this game?',{},config,'demo0000',3)
    assert response['analysis_job']['status']=='queued' and 'requested a review' in response['answer']
    response2=analysis.answer(who,lib,None,'Why did I lose this game?',{},config,'demo0000',3)
    assert response2['analysis_job']['id']==response['analysis_job']['id']


def test_clock_pressure_is_observed_without_inventing_a_timeout():
    from chess_lakehouse.personal.workspace.narration import describe
    payload=dict(initial_fen=chess.STARTING_FEN,moves=['e2e4','e7e5'],clocks=[55,5],player_color='black',white='Opponent',black='Student',outcome='loss')
    text=describe(payload,{'candidates':[],'player_moves_reviewed':1,'player_moves_total':1},2,True)
    assert '5 seconds' in text and 'does not identify whether' in text and 'does not identify a tactical cause' in text


def source():return dict(month='2013-01',url=archive_catalog.BASE+'/standard/lichess_db_standard_rated_2013-01.pgn.zst',sha256='a'*64,expected_games=121332,display_size='17.8 MB',estimated_bytes=17800000)


def test_catalog_parses_only_standard_rows_and_requires_checksum():
    url=source()['url'];html=f'<table><tr><td>2013 - January</td><td>17.8 MB</td><td>121,332</td><td><a href="{url}">.pgn.zst</a></td></tr></table>'
    rows=archive_catalog.parse(html,'a'*64+'  '+url.rsplit('/',1)[1]+'\n');assert rows==[source()]
    assert archive_catalog.parse(html.replace(url,url.replace(archive_catalog.BASE+'/','')),'a'*64+'  '+url.rsplit('/',1)[1]+'\n')==rows
    assert archive_catalog.select({'url':url+'.torrent','max_download_bytes':20000000},rows)['url']==url
    with pytest.raises(ValueError):archive_catalog.select({'max_download_bytes':1000},rows)
    with pytest.raises(ValueError):archive_catalog.parse(html,'')
    for bad in ['http://database.lichess.org','https://evil.example/standard/x.pgn.zst',url+'?x=1',url.replace('database.lichess.org','database.lichess.org@evil.example')]:
        with pytest.raises(ValueError):archive_catalog.source_url(bad)


def test_archive_jobs_owner_ceiling_pause_retry_and_subscription(database,config,monkeypatch):
    monkeypatch.setattr(archive_catalog,'catalog',lambda:[source()]);who=owner();other=owner('OtherArchive')
    app=create_app({'TOKEN':'internal-test','WORKER':False,'SETTINGS':config,'TESTING':True})
    c,h,api_owner=client(app,'ArchiveApi')
    assert c.get('/archives/catalog',headers=h).json['archives'][0]['month']=='2013-01'
    job=archives.create(who,{'max_download_bytes':20000000})
    with pytest.raises(ValueError):archives.control(other,{'id':job['id'],'action':'pause'})
    assert archives.control(who,{'id':job['id'],'action':'pause'})['jobs'][0]['status']=='paused'
    assert archives.control(who,{'id':job['id'],'action':'resume'})['jobs'][0]['status']=='queued'
    assert archives.control(who,{'id':job['id'],'action':'cancel'})['jobs'][0]['status']=='cancelled'
    assert not archives.jobs(other)['jobs']
    assert archives.subscribe(who,{'enabled':True,'max_download_bytes':20000000})['subscription']['enabled']
    assert not archives.subscribe(who,{'enabled':False,'max_download_bytes':20000000})['subscription']['enabled']


def test_archive_feeder_reads_all_records_both_sides_and_restarts(database,config,tmp_path,monkeypatch):
    import json
    from chess_lakehouse.optimized import archive_worker as worker
    from chess_lakehouse.optimized.storage import write_parquet,FRAME_SCHEMA
    from chess_lakehouse.optimized.migrate import admin_connect
    from contextlib import contextmanager
    from psycopg.rows import dict_row
    @contextmanager
    def loader():
        with admin_connect() as c:
            c.row_factory=dict_row;c.execute('SET ROLE chess_loader');yield c
    monkeypatch.setattr(worker,'connect',loader)
    who=owner();record=imported(who,games_bytes(1),config)
    build=r.start(who,{'library_id':str(record['library_id'])})['build']
    entry=write_parquet(tmp_path/'framed'/'test'/'part.parquet',[[{'record_number':n+1,'pgn':games_bytes(1,start=n).decode()} for n in range(3)]],FRAME_SCHEMA)
    (tmp_path/'framed'/'test'/'manifest.json').write_text(json.dumps(dict(source_sha256='a'*64,complete=True,shards=[entry])))
    with admin_connect() as c:
        c.execute("INSERT INTO control.pipeline_runs(run_id,engine,source_month,source_sha256,benchmark_group,config_hash,code_hash,image_id,settings,runtime,status) VALUES('feeder','python','2013-01-01',%s,'test','test','test','test','{}','{}','complete')",('a'*64,))
        c.execute("INSERT INTO control.month_publications(engine,source_month,run_id,source_sha256,accepted_games) VALUES('python','2013-01-01','feeder',%s,3)",('a'*64,))
    with db.connect() as c:
        c.execute("UPDATE study.repertoire_tasks SET status='complete' WHERE build_id=%s",(build['id'],))
        c.execute("UPDATE study.repertoire_builds SET source_stage='archive' WHERE id=%s",(build['id'],))
    for _ in range(12):
        worker.feed_one(tmp_path)
        with db.connect() as c:c.execute("UPDATE study.repertoire_tasks SET status='complete' WHERE build_id=%s",(build['id'],))
    with db.connect() as c:rows=c.execute('SELECT source_key,payload FROM study.repertoire_tasks WHERE build_id=%s AND source_month IS NOT NULL ORDER BY source_key',(build['id'],)).fetchall()
    assert len(rows)==6 and {row['payload']['player_color'] for row in rows}=={'white','black'}
    assert all('a'*64 in row['source_key'] for row in rows)
    assert not worker.feed_one(tmp_path)
