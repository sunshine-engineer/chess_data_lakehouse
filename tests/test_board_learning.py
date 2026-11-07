"""Chess legality, private practice isolation and real bounded engine execution."""
import json
from uuid import uuid4

import chess
import chess.pgn
import pytest
pytest.importorskip('flask')

from test_workspace import database,config,owner,imported,client
from test_personal import games_bytes
from chess_lakehouse import academy
from chess_lakehouse.personal.workspace import board,learning,analysis,storage as db
from chess_lakehouse.personal.workspace.application import create_app
from chess_lakehouse.web.app import create_app as public_app


def test_every_authored_drill_valid_and_computed():
    for item in academy.LESSONS:
        position=chess.Board(item['fen']);assert position.is_valid(),item['id']
        accepted=[m for m in position.legal_moves if academy.grade(item['id'],m.uci())['correct']]
        assert accepted,item['id']
    assert academy.grade('opening-center','e2e4')['correct']
    assert not academy.grade('opening-center','g1f3')['correct']
    assert academy.grade('endgame-promotion','a7a8b')['correct'] # legal underpromotion meets this narrowly stated goal


@pytest.mark.parametrize('fen,move,expected',[
 ('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1','e1g1','g1'),
 ('7k/8/8/3pP3/8/8/8/4K3 w - d6 0 2','e5d6','d6'),
 ('7k/P7/8/8/8/8/8/4K3 w - - 0 1','a7a8n','a8')])
def test_trials_special_rules_and_mainline_preserved(database,config,fen,move,expected):
    account=owner();game=chess.pgn.Game();game.setup(chess.Board(fen));game.headers.update(White='DemoStudent',Black='SyntheticOpponent',Result='1-0',Site='https://lichess.org/rules001')
    game.add_variation(next(iter(game.board().legal_moves)))
    record=imported(account,str(game).encode(),config)
    selected={'library_id':str(record['library_id']),'game_id':'rules001','ply':0,'moves':[move]}
    result=board.position(account,selected);after=chess.Board(result['fen']);assert after.piece_at(chess.parse_square(expected))
    assert not after.piece_at(chess.parse_square(move[:2]))
    with db.connect() as c:
        revision=db.game(c,account,db.snapshot(c,account,record['library_id']),'rules001')
        assert len(revision['payload']['moves'])==1 and revision['payload']['initial_fen']==fen
    with pytest.raises(ValueError):board.position(account,{**selected,'moves':['a1a8','a8a1']})


def test_preferences_variations_analysis_and_account_isolation(database,config):
    a=owner('FirstStudy');b=owner('OtherStudy');record=imported(a,games_bytes(),config)
    selected={'library_id':str(record['library_id']),'game_id':'demo0000','ply':0,'moves':['e2e4']}
    assert board.preferences(a,{'theme':'walnut','coordinates':False})['theme']=='walnut'
    assert board.preferences(b)['theme']=='forest'
    with pytest.raises(ValueError):board.preferences(a,{'theme':'arbitrary'})
    key=board.save(a,{**selected,'label':'Try the center'})['id'];assert board.position(a,selected)['variations'][0]['id']==__import__('uuid').UUID(key)
    board.remove(b,{'id':key});assert len(board.position(a,selected)['variations'])==1
    for operation in (board.position,board.save):
        with pytest.raises(ValueError):operation(b,{**selected,'label':'stolen'})
    job=board.enqueue(a,{**selected,'profile':'quick'},config)
    with pytest.raises(ValueError):board.result(b,job['id'])
    assert board.run_one(config)
    result=board.result(a,job['id']);assert result['status']=='complete',result
    evidence=result['evidence'];assert evidence['budget']['threads']==1 and evidence['budget']['seconds']==.3
    position=chess.Board(result['fen'])
    for uci in evidence['pv_uci']:assert chess.Move.from_uci(uci) in position.legal_moves;position.push_uci(uci)
    assert board.enqueue(a,{**selected,'profile':'quick'},config)['status']=='complete'
    # Identical FENs reached by different histories cannot share search evidence.
    one=board.enqueue(a,{**selected,'moves':['g1f3','g8f6','f3g1','f6g8'],'profile':'quick'},config)
    two=board.enqueue(a,{**selected,'moves':['b1c3','b8c6','c3b1','c6b8'],'profile':'quick'},config)
    assert one['id']!=two['id']
    assert board.result(a,one['id'])['fen']==board.result(a,two['id'])['fen']
    limited=__import__('dataclasses').replace(config,engine_queue_limit=2)
    with pytest.raises(ValueError,match='queued'):board.enqueue(a,{**selected,'profile':'balanced'},limited)
    with pytest.raises(ValueError,match='queued'):analysis.enqueue(a,record['library_id'],None,'demo0000',limited)
    board.remove(a,{'id':key});assert not board.position(a,selected)['variations']


def test_progress_idempotence_api_auth_and_personal_evidence(database,config):
    app=create_app({'TOKEN':'internal-test','WORKER':False,'SETTINGS':config,'TESTING':True})
    a,h,account=client(app,'PracticeAccount');b,hb,other=client(app,'PrivateAccount')
    value={'attempt_id':str(uuid4()),'lesson_id':'opening-center','move':'e2e4'}
    assert a.post('/learning/attempt',json=value,headers=h).json['correct']
    assert a.post('/learning/attempt',json=value,headers=h).status_code==200
    assert a.get('/learning/progress',headers=h).json['lessons'][0]['attempts']==1
    assert b.get('/learning/progress',headers=hb).json['lessons']==[]
    assert a.post('/learning/attempt',json={**value,'move':'d2d4'},headers=h).status_code==400
    assert a.post('/board/preferences',json={},headers={'Authorization':'Bearer internal-test'}).status_code==403
    record=imported(account,games_bytes(),config)
    result=learning.personal(account,{'library_id':str(record['library_id'])})
    assert result['games_examined']==8 and result['library_games']==8
    assert result['recommendations'][0]['lesson_id']=='opening-development'
    assert '4 of 8' in result['recommendations'][0]['explanation']
    assert result['examples'] and all(x['game_id'].startswith('demo') for x in result['examples'])
    with pytest.raises(ValueError):learning.personal(other,{'library_id':str(record['library_id'])})


def test_public_drill_api_has_no_private_dependency():
    c=public_app({'TESTING':True}).test_client()
    assert len(c.get('/api/learn').json['lessons'])==7
    response=c.post('/api/learn/board',json={'lesson_id':'opening-center','moves':['e2e4']})
    assert response.status_code==200 and response.json['turn']=='black'
    assert c.post('/api/learn/board',json={'lesson_id':'opening-center','moves':['e2e5']}).status_code==400
    assert c.post('/api/learn/attempt',json={'lesson_id':'opening-center','move':'e2e4'}).json['saved'] is False
