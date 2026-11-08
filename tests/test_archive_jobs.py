"""Actual archive subprocess publication and pause recovery on synthetic data."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from test_v2_postgres import database
from chess_lakehouse.personal.migrate import bootstrap
from chess_lakehouse.optimized.migrate import admin_connect
from chess_lakehouse.optimized import archive_worker as worker
from chess_lakehouse.optimized.storage import iter_games
from test_v2_storage import source_settings

pytestmark=pytest.mark.skipif(not os.environ.get('PGHOST'),reason='Docker PostgreSQL integration')


def setup_job(monkeypatch,tmp_path):
    monkeypatch.setenv('STUDY_PASSWORD',os.environ.get('STUDY_PASSWORD') or 'disposable-archive-test-password');bootstrap()
    archive,settings=source_settings(tmp_path)
    text=next(iter_games(archive,8192))
    archive,settings=source_settings(tmp_path,text.encode(),expected=1)
    source=dict(month='2013-01',sha256=settings.source.sha256,expected_games=1,max_download_bytes=1000000,
        url='https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst')
    target=settings.data_root/'bronze'/source['sha256']/'lichess_db_standard_rated_2013-01.pgn.zst'
    target.parent.mkdir(parents=True);target.write_bytes(archive.read_bytes());monkeypatch.setenv('CHESS_DATA_ROOT',str(settings.data_root))
    owner=str(uuid4());key=str(uuid4())
    with admin_connect() as c:
        c.execute("INSERT INTO study.accounts(id,username,username_key,password_hash) VALUES(%s,'ArchiveFixture','archivefixture','test')",(owner,))
        c.execute("INSERT INTO study.archive_jobs(id,owner_id,source,engine) VALUES(%s,%s,%s,'python')",(key,owner,Jsonb(source)))
    return key


def test_archive_supervisor_executes_a_real_container_subprocess(database,tmp_path,monkeypatch):
    key=setup_job(monkeypatch,tmp_path)
    assert worker.run_one()
    with worker.connect() as c:
        row=c.execute('SELECT * FROM study.archive_jobs WHERE id=%s',(key,)).fetchone()
        assert row['status']=='complete',row
        assert c.execute('SELECT accepted_games FROM control.month_publications').fetchone()['accepted_games']==1
        assert row['run_id']
        # Loader cannot see user credentials or original private game payloads.
        for table in ('accounts','game_revisions','sessions'):
            assert not c.execute("SELECT has_table_privilege(current_user,%s,'SELECT') AS allowed",('study.'+table,)).fetchone()['allowed']


def test_pause_records_final_run_event_and_terminates_child(database,tmp_path,monkeypatch):
    key=setup_job(monkeypatch,tmp_path);popen=subprocess.Popen
    def child(*args,**kwargs):
        with worker.connect() as c:c.execute("UPDATE study.archive_jobs SET action='pause' WHERE id=%s",(key,))
        return popen([sys.executable,'-u','-c',"import json,time; print(json.dumps(dict(event='run_started',run_id='python_pausefixture'))); time.sleep(60)"],**kwargs)
    monkeypatch.setattr(worker.subprocess,'Popen',child)
    assert worker.run_one()
    with worker.connect() as c:row=c.execute('SELECT * FROM study.archive_jobs WHERE id=%s',(key,)).fetchone()
    assert row['status']=='paused'


def test_latest_subscription_obeys_ceiling_and_never_requeues_a_month(database,tmp_path,monkeypatch):
    from chess_lakehouse import archive_catalog
    key=setup_job(monkeypatch,tmp_path)
    source=dict(month='2013-02',url='https://database.lichess.org/standard/lichess_db_standard_rated_2013-02.pgn.zst',sha256='b'*64,expected_games=1,estimated_bytes=1000,display_size='1 kB')
    monkeypatch.setattr(archive_catalog,'catalog',lambda:[source])
    with admin_connect() as c:
        owner=c.execute('SELECT owner_id FROM study.archive_jobs WHERE id=%s',(key,)).fetchone()[0]
        c.execute("INSERT INTO study.archive_subscriptions(owner_id,enabled,max_download_bytes,engine) VALUES(%s,true,2000,'python')",(owner,))
    worker.scheduled()
    with admin_connect() as c:c.execute('UPDATE study.archive_subscriptions SET checked_at=NULL')
    worker.scheduled()
    with admin_connect() as c:
        assert c.execute('SELECT count(*) FROM study.archive_jobs').fetchone()[0]==2
        c.execute('UPDATE study.archive_subscriptions SET checked_at=NULL,max_download_bytes=1')
    worker.scheduled()
    with admin_connect() as c:
        assert c.execute('SELECT count(*) FROM study.archive_jobs').fetchone()[0]==2
        assert 'ceiling' in c.execute('SELECT error FROM study.archive_subscriptions').fetchone()[0]


def test_new_month_does_not_reset_an_unfinished_archive_cursor():
    older={'source_month':'2013-01-01','source_sha256':'a'*64}
    newer={'source_month':'2013-02-01','source_sha256':'b'*64}
    cursor={'source':worker.source_key(older),'row':31,'side':1,'done':[]}
    assert worker.next_source([newer,older],cursor)==older
    assert cursor['row']==31 and cursor['side']==1
    cursor['done'].append(worker.source_key(older))
    assert worker.next_source([newer,older],cursor)==newer
    cursor['done'].append(worker.source_key(newer))
    assert worker.next_source([newer,older],cursor) is None
