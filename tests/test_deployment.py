"""Deployment probes and mounted passwords preserve the private API boundary."""
import os

import psycopg
import pytest

pytest.importorskip('flask')
from test_personal import database
from chess_lakehouse.personal.workspace import storage
from chess_lakehouse.personal.workspace.application import create_app
from chess_lakehouse.web import app as web


def unavailable():
    raise psycopg.OperationalError('synthetic database outage')


def test_live_probe_does_not_connect_to_database(monkeypatch):
    monkeypatch.setattr(storage,'connect',unavailable)
    private=create_app({'TOKEN':'synthetic-token','WORKER':False,'TESTING':True}).test_client()
    assert private.get('/livez').status_code==200
    assert private.get('/readyz').status_code==503
    assert private.get('/libraries').status_code==401
    monkeypatch.setattr(web.data,'connect',unavailable)
    public=web.create_app({'TESTING':True}).test_client()
    assert public.get('/livez').status_code==200
    assert public.get('/readyz').status_code==503


def test_mounted_database_password_authenticates(database,monkeypatch,tmp_path):
    path=tmp_path/'study-password';path.write_text(os.environ['PGPASSWORD']+'\n')
    monkeypatch.delenv('PGPASSWORD')
    monkeypatch.setenv('PGPASSWORD_FILE',str(path))
    with storage.connect() as c:
        assert c.execute('SELECT current_user AS name').fetchone()['name']=='chess_study'
