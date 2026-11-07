"""Deterministic real-PGN teaching materialization and public/private grants."""
import hashlib
import json
import os
from dataclasses import replace

import pytest
pytest.importorskip('flask')
import psycopg
import zstandard

from test_workspace import database
from test_personal import games_bytes
from chess_lakehouse.optimized import teaching,storage
from chess_lakehouse.optimized.settings import Settings,Source


@pytest.mark.parametrize('protocol',['https','http'])
def test_sample_reproducible_bounded_and_reader_cannot_publish(database,tmp_path,monkeypatch,protocol):
    archive=tmp_path/'fixture.zst';content=games_bytes(8).replace(b'https://lichess.org/',(protocol+'://lichess.org/').encode())
    archive.write_bytes(zstandard.ZstdCompressor().compress(content))
    settings=replace(Settings(Source('2013-01',hashlib.sha256(archive.read_bytes()).hexdigest(),8,100000,'https://database.lichess.org/standard/fixture.pgn.zst')),
        data_root=tmp_path/'data',min_free_disk_bytes=0,frame_target_bytes=2048)
    monkeypatch.setattr(teaching,'prepare_source',lambda selected:storage.prepare(archive,selected))
    monkeypatch.setenv('PGUSER','chess_loader');monkeypatch.setenv('PGPASSWORD',os.environ['LOADER_PASSWORD'])
    teaching.build(settings,limit=3)
    with psycopg.connect('') as c:
        first=c.execute('SELECT game_id,payload FROM academy.examples ORDER BY game_id').fetchall()
        dataset=c.execute('SELECT input_games,sample_games FROM academy.datasets').fetchone()
    assert dataset==(8,3) and len(first)==3
    assert all(value['positions'] for _,value in first)
    assert 'DemoStudent' not in json.dumps(first) and 'SyntheticOpponent' not in json.dumps(first)
    teaching.build(settings,limit=3)
    with psycopg.connect('') as c:assert c.execute('SELECT game_id,payload FROM academy.examples ORDER BY game_id').fetchall()==first
    from chess_lakehouse.web.app import create_app
    response=create_app({'TESTING':True}).test_client().get('/api/learn/examples')
    assert response.status_code==200 and response.json['datasets'][0]['source_month']=='2013-01-01'
    assert response.json['returned_games']==3 and response.json['truncated'] is False
    with psycopg.connect(host=os.environ['PGHOST'],dbname=database,user='chess_reader',password=os.environ['READER_PASSWORD']) as c:
        assert c.execute('SELECT count(*) FROM academy.examples').fetchone()[0]==3
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('DELETE FROM academy.examples')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('SELECT * FROM study.lesson_attempts')
