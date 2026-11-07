"""Checkpoint publication, immutable add-only imports and legal coaching evidence."""
from dataclasses import replace
import io
import json
from urllib.parse import urlsplit,parse_qs

import pytest
pytest.importorskip('flask')
import chess
import chess.engine

from test_personal import database,games_bytes
from test_workspace import config,owner,imported,client
from chess_lakehouse.personal import engine,review,pgn
from chess_lakehouse.personal.workspace import imports,sync,storage as db,worker,analysis
from chess_lakehouse.personal.workspace.config import Config
from chess_lakehouse.personal.workspace.application import create_app


class Provider:
    def __init__(self,metadata,games):self.metadata=metadata;self.games=games;self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        if request.get_header('Accept')=='application/x-ndjson':
            return io.BytesIO(('\n'.join(json.dumps(v) for v in self.metadata)+'\n').encode())
        assert urlsplit(request.full_url).path=='/api/games/export/_ids'
        ids=request.data.decode().split(',')
        return io.BytesIO(b'\n\n'.join(self.games[i] for i in ids if i in self.games))


def meta(i,created=1793500000000,status='mate'):
    return dict(id=f'demo{i:04}',createdAt=created,status=status,variant='standard')


def run_sync(account,config,provider,url='https://lichess.org/api/games/user/DemoStudent'):
    key=imports.create(account,'lichess','','append',url=url)
    assert worker.import_one(config,provider)
    with db.connect() as c:return imports.job(c,account,key)


def test_sync_fetches_only_missing_ids_and_revisits_late_finishes(database,config,monkeypatch):
    account=owner();first=imported(account,games_bytes(2),config)
    now=1794000000000;monkeypatch.setattr(sync.time,'time',lambda:now/1000)
    unfinished=games_bytes(1,start=3).replace(b'0-1',b'*').replace(b'2. g4 Qh4#',b'')
    provider=Provider([meta(0),meta(1),meta(2),meta(3,status='started')],{'demo0002':games_bytes(1,start=2),'demo0003':unfinished})
    job=run_sync(account,config,provider)
    assert job['status']=='complete',job['error']
    assert job['new_games']==1 and job['skipped_old_games']==2 and job['metadata_games']==4
    assert set(provider.requests[1].data.decode().split(','))=={'demo0002','demo0003'}
    with db.connect() as c:
        saved=c.execute('SELECT watermark_ms FROM study.library_sync_state WHERE library_id=%s',(first['library_id'],)).fetchone()['watermark_ms']
        assert saved==now
        assert c.execute('SELECT game_id FROM study.sync_pending_games WHERE library_id=%s',(first['library_id'],)).fetchone()['game_id']=='demo0003'
    now+=3*sync.OVERLAP_MS
    # The pending game's start is now outside the new window, but its ID is fetched.
    provider=Provider([],{'demo0003':games_bytes(1,start=3)})
    second=run_sync(account,config,provider)
    assert second['status']=='complete' and second['new_games']==1,second['error']
    assert int(parse_qs(urlsplit(provider.requests[0].full_url).query)['since'][0])==saved-sync.OVERLAP_MS
    assert provider.requests[1].data==b'demo0003'
    provider=Provider([],{});third=run_sync(account,config,provider)
    assert third['status']=='complete' and third['snapshot_id']==second['snapshot_id']
    assert len(provider.requests)==1
    with db.connect() as c:
        assert c.execute('SELECT count(*) AS n FROM study.game_revisions WHERE owner_id=%s',(account,)).fetchone()['n']==4
        assert not c.execute('SELECT 1 FROM study.sync_pending_games WHERE library_id=%s',(first['library_id'],)).fetchone()


def test_missing_completed_pgn_and_cancel_do_not_advance_checkpoint(database,config,monkeypatch):
    account=owner();first=run_sync(account,config,Provider([meta(0)],{'demo0000':games_bytes(1)}))
    assert first['status']=='complete',first['error']
    with db.connect() as c:before=c.execute('SELECT * FROM study.library_sync_state WHERE library_id=%s',(first['library_id'],)).fetchone()
    created=before['watermark_ms']-1000
    failed=run_sync(account,config,Provider([meta(1,created)],{}))
    assert failed['status']=='failed' and 'omitted' in failed['error']
    with db.connect() as c:
        assert c.execute('SELECT * FROM study.library_sync_state WHERE library_id=%s',(first['library_id'],)).fetchone()==before
        assert db.snapshot(c,account,first['library_id'])['id']==first['snapshot_id']
        assert not c.execute('SELECT 1 FROM study.import_game_queue WHERE job_id=%s',(failed['id'],)).fetchone()
    key=imports.create(account,'lichess','','append',url='https://lichess.org/api/games/user/DemoStudent')
    with db.connect() as c:c.execute('UPDATE study.import_jobs SET cancel_requested=true WHERE id=%s',(key,))
    assert worker.import_one(config,Provider([],{}))
    with db.connect() as c:
        assert imports.job(c,account,key)['status']=='cancelled'
        assert c.execute('SELECT * FROM study.library_sync_state WHERE library_id=%s',(first['library_id'],)).fetchone()==before


def test_add_only_ignores_old_changes_refresh_is_explicit_and_replace_resets_sync(database,config):
    account=owner();first=run_sync(account,config,Provider([meta(0)],{'demo0000':games_bytes(1)}))
    old_corrupted=games_bytes(1).replace(b'WhiteElo "1500"',b'WhiteElo "1800"').replace(b'f3 e5',b'f3 e9')
    added=imported(account,old_corrupted+b'\n'+games_bytes(1,start=1),config,mode='append')
    assert added['new_games']==1 and added['changed_games']==0 and added['duplicate_games']==1
    with db.connect() as c:
        row=db.game(c,account,db.snapshot(c,account,first['library_id']),'demo0000')
        assert row['payload']['white_rating']==1500
    refreshed=imported(account,games_bytes(1).replace(b'WhiteElo "1500"',b'WhiteElo "1800"'),config,mode='merge')
    assert refreshed['changed_games']==1
    imported(account,games_bytes(1),config,mode='replace')
    with db.connect() as c:assert not c.execute('SELECT 1 FROM study.library_sync_state WHERE library_id=%s',(first['library_id'],)).fetchone()


def test_filtered_imports_do_not_advance_global_cursor_and_empty_sync_is_valid(database,config):
    account=owner();provider=Provider([],{});empty=run_sync(account,config,provider)
    assert empty['status']=='complete' and empty['snapshot_id'] is None
    # A capped source is an exact PGN export, not an unrestricted-history sync.
    class Capped:
        def open(self,request,timeout):
            assert request.get_header('Accept')=='application/x-chess-pgn'
            assert 'max=1' in request.full_url
            return io.BytesIO(games_bytes(1))
    job=run_sync(account,config,Capped(),'https://lichess.org/api/games/user/DemoStudent?max=1')
    assert job['status']=='complete' and job['sync_mode'] is None
    with db.connect() as c:
        assert c.execute('SELECT last_job_id FROM study.library_sync_state WHERE library_id=%s',(empty['library_id'],)).fetchone()['last_job_id']==empty['id']


def test_sync_action_is_owner_scoped_and_api_defaults_to_add_only(database,config):
    app=create_app({'TOKEN':'internal-test','WORKER':False,'SETTINGS':config,'TESTING':True})
    a,h,account=client(app,'SyncOwner');b,hb,_=client(app,'OtherSyncOwner')
    first=imported(account,games_bytes(1),config);route='/libraries/'+str(first['library_id'])+'/sync'
    assert b.post(route,json={},headers=hb).status_code==400
    assert a.post(route,json={},headers={'Authorization':'Bearer internal-test'}).status_code==403
    response=a.post(route,json={},headers=h)
    assert response.status_code==202
    with db.connect() as c:assert imports.job(c,account,response.json['job_id'])['mode']=='append'
    response=a.post('/imports/lichess',json={'url':'https://lichess.org/api/games/user/DemoStudent'},headers=h)
    with db.connect() as c:assert imports.job(c,account,response.json['job_id'])['mode']=='append'


def test_bulk_export_game_urls_empty_games_and_gameid_only_records(database,config):
    account=owner();first=imported(account,games_bytes(1),config)
    # The live bulk endpoint uses /game/ID, including records with no moves.
    empty=b'[Site "https://lichess.org/game/demo0001"]\n[White "DemoStudent"]\n[Black "SyntheticOpponent"]\n[Result "*"]\n\n*\n'
    finished=games_bytes(1,start=2).replace(b'https://lichess.org/demo0002',b'https://lichess.org/game/demo0002')
    provider=Provider([meta(0),meta(1,status='aborted'),meta(2)],{'demo0001':empty,'demo0002':finished})
    job=run_sync(account,config,provider)
    assert job['status']=='complete',job['error']
    assert job['new_games']==1 and job['rejected_games']==1 and job['skipped_old_games']==1
    assert imports.source_id({'Site':'https://lichess.org/game/demo0002'})=='demo0002'
    assert imports.source_id({'Site':'?','GameId':'demo0002'})=='demo0002'
    assert imports.source_id({'Site':'https://lichess.org/demo0002abcd/black'})=='demo0002'
    reimport=imported(account,finished.replace(b'f3 e5',b'f3 e9'),config,mode='append')
    assert reimport['duplicate_games']==1 and reimport['changed_games']==0
    with db.connect() as c:
        assert c.execute('SELECT count(*) AS n FROM study.sync_pending_games WHERE library_id=%s',(first['library_id'],)).fetchone()['n']==0
def test_real_stockfish_returns_three_legal_explained_lines_and_progress(database,config):
    account=owner();record=imported(account,games_bytes(1),config)
    analysis.enqueue(account,record['library_id'],None,'demo0000',config)
    assert worker.analyze_one(config)
    job=analysis.game(account,record['library_id'],None,'demo0000')['analysis_jobs'][0]
    assert job['status']=='complete',job['error']
    e=job['evidence'];assert e['complete_coverage'] and e['review_version']==3
    assert e['player_moves_reviewed']==e['player_moves_total']==2
    assert e['analyzed_plies']==e['total_plies']==4
    assert job['progress']['stage']=='Review finished'
    finding=next(c for c in e['candidates'] if c['ply']==3)
    assert len(finding['alternatives'])==3
    assert len({l['pv_uci'][0] for l in finding['alternatives']})==3
    assert finding['motif']=='Allows checkmate in one'
    for line in finding['alternatives']:
        board=chess.Board(line['positions'][0])
        assert len(line['explanation'])>150 and 'opponent' in line['explanation']
        for i,uci in enumerate(line['pv_uci']):
            move=chess.Move.from_uci(uci);assert move in board.legal_moves;board.push(move)
            assert board.fen()==line['positions'][i+1]
    assert Config().engine_game_seconds==300


def test_all_detected_findings_retained_beyond_old_cap(monkeypatch,config):
    moves=['g1f3','g8f6','f3g1','f6g8']*22
    row={'moves':moves,'initial_fen':chess.STARTING_FEN,'player_color':'white'}
    class Search:
        id={'name':'Synthetic deterministic search'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def configure(self,values):pass
        def analyse(self,board,limit,multipv=None,root_moves=None):
            played=chess.Move.from_uci(moves[board.ply()]) if board.ply()<len(moves) else None
            legal=list(board.legal_moves)
            choices=root_moves or ([m for m in legal if m!=played][:multipv] if multipv else legal[:1])
            values=[dict(score=chess.engine.PovScore(chess.engine.Cp(-500 if root_moves else 100),chess.WHITE),pv=[m],depth=10,nodes=1000) for m in choices]
            return values if multipv else values[0]
    monkeypatch.setattr(chess.engine.SimpleEngine,'popen_uci',lambda *a,**k:Search())
    e=engine.analyze(row,config)
    assert e['complete_coverage'] and e['player_moves_reviewed']==44
    assert len(e['candidates'])==44 and all(len(c['alternatives'])==3 for c in e['candidates'])


def test_explanations_keep_losing_positions_honest_for_black():
    board=chess.Board();board.push_uci('e2e4');move=chess.Move.from_uci('e7e5')
    line=review.line_data(board,{'score':chess.engine.PovScore(chess.engine.Cp(600),chess.WHITE),'pv':[move]},chess.BLACK)
    played={**line,'score_cp':-900}
    text=review.explain(board,line,played,chess.BLACK)
    assert line['score_cp']==-600 and 'opponent still has the advantage' in text
    assert '+3.00' in text and 'favoring your position' not in text


def test_timeout_reports_partial_coverage_instead_of_complete(monkeypatch,config):
    clock=[0.0]
    monkeypatch.setattr(review.time,'monotonic',lambda:clock[0])
    class SlowSearch:
        id={'name':'Synthetic slow search'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def configure(self,values):pass
        def analyse(self,board,limit,**kwargs):
            clock[0]+=30
            return dict(score=chess.engine.PovScore(chess.engine.Cp(0),chess.WHITE),pv=[next(iter(board.legal_moves))])
    monkeypatch.setattr(chess.engine.SimpleEngine,'popen_uci',lambda *a,**k:SlowSearch())
    row={'moves':['e2e4','e7e5','g1f3','b8c6'],'initial_fen':chess.STARTING_FEN,'player_color':'white'}
    e=engine.analyze(row,config)
    assert not e['complete_coverage'] and e['player_moves_reviewed']==0
    assert e['analyzed_plies']<e['total_plies']
