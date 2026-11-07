"""Product API behavior over a disposable warehouse; no live library writes."""
import base64
import csv
import hashlib
import io
import json

import pytest
pytest.importorskip('flask')
from test_web_ml import warehouse
from chess_lakehouse.web import data,downloads
from chess_lakehouse.web.app import create_app


def test_catalog_scope_and_combined_filters(warehouse):
    catalog=data.catalog()
    assert catalog['private_study_included'] is False
    assert [(p['engine'],p['month'],p['games']) for p in catalog['publications']]==[('python','2013-01',1050)]
    selection={'result':'1/2-1/2','eco':'C20','from_date':'2013-01-05','to_date':'2013-01-08'}
    overview=data.overview(selection)
    page=data.games(dict(selection,limit=100))
    assert page['total']==overview['summary']['games']==44
    assert all(row['result']=='1/2-1/2' and '2013-01-05'<=row['date']<='2013-01-08' for row in page['rows'])
    for invalid in ({'eco':'C20 OR true'},{'result':'win'},{'from_date':'yesterday'},{'from_date':'2013-02-01','to_date':'2013-01-01'},{'game_id':'../../secret'}):
        with pytest.raises(ValueError):data.games(invalid)


@pytest.mark.parametrize('sort',['id','newest','oldest'])
def test_keyset_covers_every_game_once_and_binds_filters(warehouse,sort):
    ids=[];cursor=None;previous=None
    while True:
        query={'limit':100,'sort':sort}
        if cursor:query['cursor']=cursor
        page=data.games(query);ids.extend(r['game_id'] for r in page['rows'])
        if previous:
            first=page['rows'][0];before=(previous['date'],previous['game_id']);after=(first['date'],first['game_id'])
            if sort=='newest':assert after<before
            elif sort=='oldest':assert after>before
        if not page['has_more']:break
        previous=page['rows'][-1];cursor=page['next_cursor']
    assert len(ids)==len(set(ids))==1050
    token=data.games({'limit':2,'sort':sort})['next_cursor']
    with pytest.raises(ValueError,match='filters'):data.games({'cursor':token,'sort':sort,'speed':'rapid'})
    with pytest.raises(ValueError):data.games({'cursor':'%%%','sort':sort})


def test_game_detail_filtered_csv_and_download_checksums(warehouse,tmp_path):
    app=create_app({'TESTING':True,'ML_ROOT':tmp_path});client=app.test_client()
    selected=client.get('/api/games?result=1-0&limit=1').json['rows'][0]['game_id']
    game=client.get('/api/games/'+selected).json
    assert game['source_url']=='https://lichess.org/'+selected
    assert game['initial_seconds']==300 and game['opening']=='Test opening'
    assert 'player_hash' not in game and 'username' not in game
    assert client.get('/api/games/abcdefgh').status_code==404
    assert client.get('/api/games/invalid').status_code==400
    response=client.get('/api/games.csv?result=1-0&export_limit=7')
    assert response.status_code==200
    body=response.data
    assert hashlib.sha256(body).hexdigest()==response.headers['X-Content-SHA256']
    rows=list(csv.DictReader(io.StringIO(body.decode())))
    assert len(rows)==7 and {r['result'] for r in rows}=={'1-0'}
    assert response.headers['X-Export-Truncated']=='true'
    assert response.headers['X-Export-Total']=='360'
    assert rows[0]['run_id']=='fixture'
    response.close()
    assert client.get('/api/games.csv?export_limit=10001').status_code==400
    assert downloads.safe('=DANGEROUS()').startswith("'")
    assert downloads.safe('-text').startswith("'") and downloads.safe(-123)==-123


def test_product_documents_missing_states_and_read_only_boundary(warehouse,tmp_path):
    client=create_app({'TESTING':True,'ML_ROOT':tmp_path,'EVIDENCE_ROOT':tmp_path}).test_client()
    cases=client.get('/api/case-studies').json['cases']
    assert len(cases)==5 and {v['id'] for v in cases}=={'personal','pipelines','outcome','length','clusters'}
    for case in cases:
        page=client.get('/case-studies/'+case['id'])
        assert page.status_code==200 and b'Main navigation' in page.data
        assert b'case-studies/docs/' not in page.data
    assert client.get('/guides/web-app').status_code==200
    assert client.get('/not-a-real-page').status_code==404
    assert b'Open my workspace' in client.get('/not-a-real-page').data
    assert client.get('/api/evidence/no-such-file').status_code==404
    assert client.get('/api/evidence/benchmarks').status_code==503
    assert client.get('/api/models').json['available'] is False
    assert client.post('/api/predictions',json={'white_rating':1500,'black_rating':1500,'initial_seconds':300,'increment_seconds':0}).status_code==503
    row={'white_rating':1500,'black_rating':1500,'speed':'blitz'}
    comparison=data.comparable(row,'python','2013-01')
    assert comparison['games']>0
    assert abs(comparison['white_win_rate']+comparison['black_win_rate']+comparison['draw_rate']-1)<1e-9
    with pytest.raises(data.PublicationChanged):data.comparable(row,'python','2013-01',{'publication':'wrong'})


def test_incomplete_or_nonfinite_benchmark_evidence_is_not_presented_as_valid(tmp_path):
    client=create_app({'TESTING':True,'EVIDENCE_ROOT':tmp_path,'ML_ROOT':tmp_path}).test_client()
    for bad in ('{broken',json.dumps({'summary':{}}),'{"summary":{"python":{"samples":NaN}}}'):
        (tmp_path/'comparison-v2.json').write_text(bad)
        assert client.get('/api/benchmarks').status_code==503
        assert client.get('/api/evidence/benchmarks').status_code==503
    fields=('samples','median_elapsed_seconds','median_input_games_per_second','median_container_peak_memory_mib','median_container_cpu_seconds','median_parquet_bytes')
    report={'benchmark_group':'synthetic-evidence','measurement':'Synthetic software test',
        'summary':{engine:dict.fromkeys(fields,1) for engine in data.ENGINES},'equality':{'synthetic/check':True},
        'histories':[['2013-01-01','a'*64]]}
    (tmp_path/'comparison-v2.json').write_text(json.dumps(report))
    assert client.get('/api/benchmarks').status_code==200
    report['summary']['python']['median_elapsed_seconds']=float('nan')
    (tmp_path/'comparison-v2.json').write_text(json.dumps(report))
    assert client.get('/api/benchmarks').status_code==503
