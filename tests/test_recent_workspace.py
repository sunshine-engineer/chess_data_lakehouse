"""Recent cohorts, start-time order, precise imports and contextual study questions."""
import io
import json
from datetime import datetime,timezone,timedelta
import re

import pytest
pytest.importorskip('flask')

from test_personal import database,games_bytes
from test_workspace import config,owner,imported
from test_incremental_review import Provider,meta,run_sync
from chess_lakehouse.personal.workspace import storage as db,insights,analysis,exports,chronology,board,import_status
from chess_lakehouse.personal.workspace.questions import recent_count


def timed_games():
    records=[]
    for i,hour in enumerate([19,21,18,23,20,22]):
        data=games_bytes(1,start=i)
        data=re.sub(rb'\[Date "[^"]+"\]',f'[Date "2026.11.07"]\n[UTCDate "2026.11.07"]\n[UTCTime "{hour}:00:00"]'.encode(),data)
        if i==5:data=data.replace(b'300+0',b'600+0')
        records.append(data)
    return b'\n\n'.join(records)


@pytest.mark.parametrize('question,count',[
    ('Summarize my last 3 games?',3),('Review my latest three games',3),
    ('Summarize my last few games',3),('Show my 5 most recent games',5),
    ('Review the previous 2 white games',2),('What should I practise from my last 5 games?',5),
    ('Summarize my last 3 blitz games',3),('Summarize my last 3 losses',3),('Summarize my games in the last 30 days',None)])
def test_recent_query_grammar(question,count):assert recent_count(question)==count


@pytest.mark.parametrize('question',['Summarize my last 0 games','Review my last 201 games','Summarize my last dozen games','Summarize my last -3 games'])
def test_unsupported_recent_counts_do_not_silently_widen(question):
    with pytest.raises(ValueError):recent_count(question)


def test_three_games_means_three_in_actual_start_order_with_matching_evidence(database,config):
    account=owner();record=imported(account,timed_games(),config)
    answer=analysis.answer(account,record['library_id'],None,'Summarize my last 3 games?',{},config)
    assert answer['supported'] and answer['metrics']['games']==3
    assert answer['metrics']['wins']==1 and answer['metrics']['losses']==2
    assert answer['filters']['last_games']=='3' and 'latest 3' in answer['answer']
    assert [g['game_id'] for g in answer['games']]==['demo0003','demo0005','demo0001']
    assert [r['game_id'] for r in answer['references']]==['demo0003','demo0005','demo0001']
    assert answer['chronology_note'] is None
    table=insights.games(account,record['library_id'],None,{'last_games':'3'})
    assert table['total']==3 and [r['game_id'] for r in table['rows']]==[g['game_id'] for g in answer['games']]
    scoped=analysis.answer(account,record['library_id'],None,'Summarize this game',{},config,'demo0000',0)
    assert scoped['metrics']['games']==1 and scoped['games'][0]['game_id']=='demo0000'
    implicit=analysis.answer(account,record['library_id'],None,'Summarize',{},config,'demo0000',0)
    assert implicit['metrics']['games']==1 and implicit['scope_label']=='This game'
    implicit_loss=analysis.answer(account,record['library_id'],None,'Why did I lose?',{},config,'demo0005',0)
    assert 'This game was a win' in implicit_loss['answer']
    premise=analysis.answer(account,record['library_id'],None,'Why did I lose this game?',{},config,'demo0005',0)
    assert 'This game was a win' in premise['answer']
    fewer=analysis.answer(account,record['library_id'],None,'Summarize my last 3 games',{'result':'win'},config)
    assert fewer['metrics']['games']==2 and 'Only 2 games match' in fewer['answer']
    losses=analysis.answer(account,record['library_id'],None,'Summarize my last 2 losses',{},config)
    assert losses['metrics']['games']==2 and losses['metrics']['losses']==2
    breakdown=analysis.answer(account,record['library_id'],None,'Summarize my last 3 games: wins and losses',{},config)
    assert breakdown['metrics']['games']==3 and breakdown['metrics']['wins']==1


def test_filters_paging_and_recent_saved_selection_stay_consistent(database,config):
    account=owner();record=imported(account,timed_games(),config)
    recent=insights.games(account,record['library_id'],None,{'last_games':'3','speed':'blitz','opponent':'SYNTHETIC'},offset=1,limit=2)
    assert [g['game_id'] for g in recent['rows']]==['demo0001','demo0004']
    assert recent['total']==3 and not recent['has_more']
    older=insights.games(account,record['library_id'],None,{},limit=2,order='oldest')
    assert [g['game_id'] for g in older['rows']]==['demo0002','demo0000']
    saved=exports.save(account,record['library_id'],None,'Three games exactly',{'last_games':'3'})
    with db.connect() as c:
        original=c.execute('SELECT payload_sha256 FROM study.game_revisions WHERE owner_id=%s ORDER BY game_id',(account,)).fetchall()
        chronology.save(c,account,'demo0000',datetime(2026,11,8,tzinfo=timezone.utc),'lichess')
        after=c.execute('SELECT payload_sha256 FROM study.game_revisions WHERE owner_id=%s ORDER BY game_id',(account,)).fetchall()
        assert after==original
    path,*_=exports.prepare(account,study_id=saved['id'],format='pgn',config=config)
    text=path.read_text();path.unlink()
    assert all(i in text for i in ('demo0003','demo0005','demo0001')) and 'demo0000' not in text
    path,*_=exports.prepare(account,study_id=saved['id'],format='report',config=config)
    report=json.loads(path.read_text());path.unlink()
    assert report['summary']['games']==3 and {g['game_id'] for g in report['cohort_games']}=={'demo0003','demo0005','demo0001'}
    other=owner('OtherRecentOwner')
    with pytest.raises(ValueError):insights.games(other,record['library_id'],None,{'last_games':'3'})


def test_unknown_times_are_reported_and_backfill_never_imports_or_changes_checkpoint(database,config):
    account=owner();record=imported(account,games_bytes(2),config)
    response=analysis.answer(account,record['library_id'],None,'Summarize my last 3 games',{},config)
    assert response['chronology_note'] and 'approximate' in response['answer']
    class Metadata:
        def open(self,request,timeout):
            assert 'moves=false' in request.full_url and request.get_header('Accept')=='application/x-ndjson'
            return io.BytesIO((json.dumps(meta(0))+'\n'+json.dumps(meta(8))+'\n').encode())
    result=chronology.backfill('DemoAccount','DemoStudent',Metadata())
    assert result['owned_times']==1 and not result['moves_downloaded'] and not result['checkpoints_changed']
    with db.connect() as c:
        assert not c.execute('SELECT 1 FROM study.library_sync_state WHERE owner_id=%s',(account,)).fetchone()
        assert c.execute('SELECT count(*) AS n FROM study.game_revisions WHERE owner_id=%s',(account,)).fetchone()['n']==2
        prior=c.execute('SELECT started_at FROM study.game_times WHERE owner_id=%s',(account,)).fetchone()['started_at']
        chronology.save(c,account,'demo0000',prior+timedelta(days=1),'pgn_utc')
        assert c.execute('SELECT started_at FROM study.game_times WHERE owner_id=%s',(account,)).fetchone()['started_at']==prior


def test_pending_empty_game_has_clear_message_and_is_not_called_unsupported(database,config):
    account=owner();imported(account,games_bytes(1),config)
    empty=b'[Site "https://lichess.org/game/demo0001"]\n[White "DemoStudent"]\n[Black "Other"]\n[Result "*"]\n\n*\n'
    job=run_sync(account,config,Provider([meta(1,status='started')],{'demo0001':empty}))
    assert job['status']=='complete' and job['pending_games']==1 and job['skip_counts']=={'unfinished':1}
    outcome=import_status.describe(job)
    assert 'still in progress' in outcome['message'] and '0 new' not in outcome['message']
    assert not outcome['details']
    up_to_date=run_sync(account,config,Provider([] ,{'demo0001':empty}))
    assert up_to_date['pending_games']==1
    added=import_status.describe(dict(job,new_games=1,pending_games=0,skip_counts={}))
    assert added['message']=='Added 1 new game.'
    file_notice=import_status.describe(dict(job,pending_games=0,source_kind='file'))
    assert 'Upload the completed PGN again' in file_notice['message']


def test_trial_questions_use_exact_position_engine_evidence_and_owner(database,config):
    account=owner();record=imported(account,games_bytes(1),config)
    value=dict(library_id=str(record['library_id']),game_id='demo0000',ply=0,moves=['e2e4'],profile='quick')
    missing=analysis.answer(account,record['library_id'],None,'What is the best move here?',{},config,'demo0000',0,['e2e4'],'quick')
    assert not missing['supported'] and 'Evaluate this trial position first' in missing['answer']
    board.enqueue(account,value,config);assert board.run_one(config)
    answer=analysis.answer(account,record['library_id'],None,'What is the best move here?',{},config,'demo0000',0,['e2e4'],'quick')
    assert answer['supported'] and answer['scope_label']=='Current trial position' and answer['metrics']['pv_uci']
    wrong=analysis.answer(account,record['library_id'],None,'What is the best move here?',{},config,'demo0000',0,['d2d4'],'quick')
    assert not wrong['supported']
    board.enqueue(account,{**value,'ply':1,'moves':[]},config);assert board.run_one(config)
    played=analysis.answer(account,record['library_id'],None,'What is the best move here?',{},config,'demo0000',1,[],'quick')
    assert played['supported'] and played['scope_label']=='Selected played position'
    with pytest.raises(ValueError):analysis.answer(account,record['library_id'],None,'What is the best move here?',{},config,'demo0000',0,['e2e5'],'quick')
