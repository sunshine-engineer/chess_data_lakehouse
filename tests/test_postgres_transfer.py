"""Cross-instance PostgreSQL transfer, including both sides of a commit crash."""
from dataclasses import replace
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from chess_lakehouse.transfer import archive, inventory, service, state
from chess_lakehouse.transfer.settings import Endpoint, Options, load


@pytest.fixture
def databases(monkeypatch):
    """Use two separate PostgreSQL containers and fresh databases for each test."""
    if not os.environ.get('SOURCE_PGHOST') or not os.environ.get('TARGET_PGHOST'):
        pytest.skip('Cross-instance tests require compose.migration-qa.yaml')
    source, target = Endpoint("SOURCE"), Endpoint("TARGET")
    a, b = source.connect(autocommit=True), target.connect(autocommit=True)
    name_a, name_b = "transfer_src_"+uuid4().hex[:12], "transfer_dst_"+uuid4().hex[:12]
    a.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name_a)))
    b.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name_b)))
    monkeypatch.setenv("SOURCE_PGDATABASE", name_a); monkeypatch.setenv("TARGET_PGDATABASE", name_b)
    try:
        yield source, target
    finally:
        a.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name_a)))
        b.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name_b)))
        a.close(); b.close()


@pytest.fixture
def options():
    return Options(chunk_rows=2, chunk_bytes=1024, max_row_bytes=1024*1024, dump_jobs=2, min_free_bytes=0)


def seed(source):
    """Exercise objects that plain pandas/to_sql migrations commonly lose."""
    with source.connect() as conn:
        database=conn.execute('SELECT current_database()').fetchone()[0]
        conn.execute(sql.SQL("COMMENT ON DATABASE {} IS 'Portable synthetic fixture'").format(sql.Identifier(database)))
        conn.execute(sql.SQL("ALTER DATABASE {} SET statement_timeout TO '120000'").format(sql.Identifier(database)))
        conn.execute('CREATE SCHEMA "Case Schema"')
        conn.execute('CREATE TYPE "Case Schema".mood AS ENUM (\'good\',\'bad\')')
        conn.execute('''CREATE TABLE "Case Schema"."Quoted Table" (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            label TEXT, payload JSONB, bytes BYTEA, numbers INTEGER[],
            mood "Case Schema".mood DEFAULT 'good',
            label_length INTEGER GENERATED ALWAYS AS (length(label)) STORED)''')
        for i in range(7):
            conn.execute('''INSERT INTO "Case Schema"."Quoted Table"(label,payload,bytes,numbers)
                VALUES(%s,%s::jsonb,%s,%s)''', (f'row {i}\n\\.\t"雪"',json.dumps({'n':i}),bytes([0,i,255]),[i,None,i+1]))
        conn.execute('CREATE TABLE "Case Schema".no_key (value TEXT)')
        conn.execute("INSERT INTO \"Case Schema\".no_key VALUES ('duplicate'),('duplicate'),(NULL),('')")
        conn.execute('CREATE TABLE "Case Schema".empty_table(id INT)')
        conn.execute('CREATE UNLOGGED TABLE "Case Schema".unlogged_heap(id INT)')
        conn.execute('INSERT INTO "Case Schema".unlogged_heap VALUES(1),(2)')
        conn.execute('CREATE TABLE "Case Schema".zero_columns()')
        conn.execute('INSERT INTO "Case Schema".zero_columns DEFAULT VALUES')
        conn.execute('CREATE TABLE "Case Schema".parent (id INT, month INT) PARTITION BY LIST(month)')
        conn.execute('CREATE TABLE "Case Schema".leaf PARTITION OF "Case Schema".parent FOR VALUES IN (1)')
        conn.execute('INSERT INTO "Case Schema".parent VALUES (1,1),(2,1)')
        conn.execute('''CREATE FUNCTION "Case Schema".double_it(x INT) RETURNS INT LANGUAGE sql IMMUTABLE AS 'SELECT x*2' ''')
        conn.execute('CREATE VIEW "Case Schema".sample_view AS SELECT id,label FROM "Case Schema"."Quoted Table"')
        conn.execute('CREATE MATERIALIZED VIEW "Case Schema".sample_mv AS SELECT count(*) AS n FROM "Case Schema"."Quoted Table"')
        conn.execute('CREATE INDEX no_key_value ON "Case Schema".no_key(value)')
        conn.execute('ALTER TABLE "Case Schema".no_key ENABLE ROW LEVEL SECURITY')
        conn.execute('CREATE POLICY read_policy ON "Case Schema".no_key FOR SELECT USING (true)')
        conn.execute("SELECT lo_from_bytea(45678,%s)",(b'large-object\x00payload'*2000,))
        conn.execute('COMMENT ON TABLE "Case Schema".no_key IS \'A preserved comment\'')


def test_all_objects_data_and_idempotent_resume(databases, options, tmp_path):
    source, target = databases; seed(source)
    plan = service.plan(source, target, options)
    assert plan['source_identity']['system_identifier'] != plan['target_identity']['system_identifier']
    report = service.run(source, target, options, tmp_path, maintenance_window=True)
    assert report['status']=='complete' and report['rows_verified']>=14
    assert report['large_objects_verified'] and report['sequences_verified']
    assert service.run(source, target, options, tmp_path, maintenance_window=True)['status']=='complete'
    tracker=json.loads((tmp_path/'tracker.json').read_text())
    assert tracker['phase']=='complete' and tracker['units_committed']>=6
    with target.connect() as c:
        assert c.execute(f"SELECT count(*) FROM {state.SCHEMA}.units WHERE unit_id LIKE 'copy:%'").fetchone()[0]>=6
    assert 'synthetic-source-password' not in (tmp_path/'tracker.json').read_text()
    with target.connect() as c:
        assert c.execute('SELECT count(*) FROM "Case Schema"."Quoted Table"').fetchone()[0]==7
        assert c.execute('SELECT length(lo_get(45678))').fetchone()[0]>1000


@pytest.mark.parametrize('window',['before_commit','after_commit','native_before_commit','native_after_commit'])
def test_crash_resumes_without_duplicates(databases, options, tmp_path, window):
    source,target=databases;seed(source);seen=[]
    def interrupt(phase, unit):
        if phase==window and not seen:seen.append(unit);raise InterruptedError('synthetic interruption')
    with pytest.raises(InterruptedError):service.run(source,target,options,tmp_path,True,inject=interrupt)
    with target.connect() as c:
        committed=c.execute(f'SELECT count(*) FROM {state.SCHEMA}.units WHERE unit_id=%s',(seen[0],)).fetchone()[0]
        assert committed==(1 if window in ('after_commit','native_after_commit') else 0)
    assert service.run(source,target,options,tmp_path,True)['status']=='complete'
    with target.connect() as c:assert c.execute('SELECT count(*) FROM "Case Schema"."Quoted Table"').fetchone()[0]==7
    with target.connect() as c:assert c.execute("SELECT relpersistence FROM pg_class WHERE oid='\"Case Schema\".unlogged_heap'::regclass").fetchone()[0]=='u'


def test_populated_destination_and_missing_window_are_rejected(databases, options, tmp_path):
    source,target=databases
    with pytest.raises(ValueError,match='maintenance'):service.run(source,target,options,tmp_path)
    with target.connect() as c:c.execute('CREATE TABLE keep_me(x INT)');c.execute('INSERT INTO keep_me VALUES(5)')
    with pytest.raises(ValueError,match='not empty'):service.run(source,target,options,tmp_path,True)
    with target.connect() as c:assert c.execute('SELECT x FROM keep_me').fetchone()[0]==5


def test_destination_with_only_a_function_is_rejected(databases,options):
    source,target=databases
    with target.connect() as c:c.execute("CREATE FUNCTION public.keep_me() RETURNS INT LANGUAGE sql AS 'SELECT 5'")
    with pytest.raises(ValueError,match='not empty'):service.plan(source,target,options)
    with target.connect() as c:assert c.execute('SELECT public.keep_me()').fetchone()[0]==5


def test_tracker_recovery_and_artifact_tampering(databases, options, tmp_path):
    source,target=databases;seed(source)
    service.run(source,target,options,tmp_path,True)
    (tmp_path/'tracker.json').write_text('{broken')
    with pytest.raises(ValueError,match='Tracker is corrupt'):service.run(source,target,options,tmp_path,True)
    assert service.run(source,target,options,tmp_path,True,True)['status']=='complete'
    path=next((tmp_path/'archive').glob('*.dat.gz'));path.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):service.run(source,target,options,tmp_path,True)


def test_source_changes_and_target_changes_block_completion(databases, options, tmp_path):
    source,target=databases;seed(source)
    service.run(source,target,options,tmp_path,True)
    with source.connect() as c:c.execute('INSERT INTO "Case Schema".no_key VALUES (\'new write\')')
    with pytest.raises(ValueError,match='Source changed'):service.run(source,target,options,tmp_path,True)
    with target.connect() as c:c.execute('DELETE FROM "Case Schema".no_key')
    with pytest.raises(ValueError,match='validation failed'):service.run(source,target,options,tmp_path,True)


def test_options_and_endpoint_secrets(monkeypatch,tmp_path):
    assert Options().fingerprint()==replace(Options(), max_row_bytes=128*1024*1024, dump_jobs=1).fingerprint()
    assert Options().fingerprint()!=replace(Options(), chunk_rows=3).fingerprint()
    with pytest.raises(ValueError):Options(chunk_rows=0)
    with pytest.raises(ValueError):Options(dump_jobs=100)
    path=tmp_path/'config.toml';path.write_text('[source]\npassword="no"')
    with pytest.raises(ValueError,match='credentials'):load(path)
    path.write_text('[options]\nchunk_rows=3'); assert load(path)[2].chunk_rows==3
    secret=tmp_path/'password';secret.write_text('secret\n')
    monkeypatch.setenv('TEST_PGHOST','example.invalid');monkeypatch.setenv('TEST_PGDATABASE','sample')
    monkeypatch.setenv('TEST_PGPASSWORD_FILE',str(secret));endpoint=Endpoint('TEST')
    assert endpoint.environment()['PGPASSWORD']=='secret'
    assert 'secret' not in json.dumps(endpoint.public())
    monkeypatch.setenv('PGPASSWORD','do-not-inherit')
    with pytest.raises(ValueError,match='ambient'):endpoint.connect()


def test_repository_schema_roles_and_grants(databases, options, tmp_path, monkeypatch):
    """Transfer the project's real immutable SQL migrations, including role ACLs."""
    from chess_lakehouse.personal import migrate
    source,target=databases
    monkeypatch.setattr(migrate,'admin_connect',source.connect)
    # migrate() receives its connection explicitly inside personal.bootstrap().
    for key in ('LOADER_PASSWORD','READER_PASSWORD','STUDY_PASSWORD'):
        monkeypatch.setenv(key,'synthetic-role-password')
    migrate.bootstrap()
    with source.connect() as c:
        c.execute("SELECT control.ensure_partition('python',DATE '2013-01-01')")
    report=service.run(source,target,replace(options,create_missing_roles=True),tmp_path,True)
    assert report['status']=='complete' and report['tables_verified']>=60
    with target.connect() as c:
        assert c.execute('SELECT count(*) FROM control.schema_migrations').fetchone()[0]==10
        assert c.execute("SELECT has_schema_privilege('chess_study','study','USAGE')").fetchone()[0]
        assert not c.execute("SELECT has_schema_privilege('chess_study','warehouse_python','USAGE')").fetchone()[0]


@pytest.mark.parametrize('window',['before_commit','after_commit'])
def test_process_sigkill_and_resume(databases, options, tmp_path, window):
    """Kill a real migration process at a COPY commit boundary, then reconcile."""
    source,target=databases;seed(source)
    ready=tmp_path/'ready'
    child='''
import pathlib,sys,time
from chess_lakehouse.transfer import service
from chess_lakehouse.transfer.settings import Endpoint,Options
def pause(phase,unit):
    if phase==sys.argv[2]:
        pathlib.Path(sys.argv[1],'ready').write_text(unit)
        time.sleep(60)
options=Options(chunk_rows=2,chunk_bytes=1024,max_row_bytes=1024*1024,dump_jobs=2,min_free_bytes=0)
service.run(Endpoint('SOURCE'),Endpoint('TARGET'),options,sys.argv[1],True,inject=pause)
'''
    process=subprocess.Popen([sys.executable,'-c',child,str(tmp_path),window],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+60
        while not ready.exists():
            if process.poll() is not None:raise AssertionError(process.stderr.read().decode())
            if time.monotonic()>deadline:raise AssertionError('Migration did not reach crash boundary')
            time.sleep(.05)
        process.kill();assert process.wait(timeout=10)==-9
        with target.connect() as c:
            assert bool(state.lookup(c,json.loads((tmp_path/'job.json').read_text())['job_id'],ready.read_text()))==(window=='after_commit')
        assert service.run(source,target,options,tmp_path,True)['status']=='complete'
        with target.connect() as c:assert c.execute('SELECT count(*) FROM "Case Schema"."Quoted Table"').fetchone()[0]==7
    finally:
        if process.poll() is None:process.kill();process.wait()
        process.stderr.close()
