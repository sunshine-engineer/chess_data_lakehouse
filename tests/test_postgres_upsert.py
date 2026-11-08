"""Real cross-instance upserts preserve destination-only rows and resume commits."""
from dataclasses import replace
import json
import subprocess
import sys
import time

import psycopg
import pytest

from test_postgres_transfer import databases, options
from chess_lakehouse.transfer import service, state


def seed(source,target):
    """Use generated values, foreign keys, composite keys, and a nullable FK cycle."""
    ddl='''CREATE SCHEMA "Merge Schema";
    CREATE TABLE "Merge Schema".parents(
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label TEXT NOT NULL,
      size INTEGER GENERATED ALWAYS AS (length(label)) STORED);
    CREATE TABLE "Merge Schema".children(
      parent BIGINT REFERENCES "Merge Schema".parents(id), code TEXT, value JSONB,
      PRIMARY KEY(parent,code));
    CREATE TABLE "Merge Schema".libraries(id INT PRIMARY KEY, active_snapshot INT);
    CREATE TABLE "Merge Schema".snapshots(id INT PRIMARY KEY, library INT NOT NULL REFERENCES "Merge Schema".libraries(id));
    ALTER TABLE "Merge Schema".libraries ADD FOREIGN KEY(active_snapshot) REFERENCES "Merge Schema".snapshots(id);
    CREATE TABLE "Merge Schema".key_only(id INT PRIMARY KEY);
    CREATE TABLE "Merge Schema".partitioned(id INT, month INT, label TEXT, PRIMARY KEY(id,month)) PARTITION BY LIST(month);
    CREATE TABLE "Merge Schema".part_one PARTITION OF "Merge Schema".partitioned FOR VALUES IN (1);
    '''
    for endpoint in (source,target):
        with endpoint.connect() as c:c.execute(ddl)
    with source.connect() as c:
        c.execute('''INSERT INTO "Merge Schema".parents(label) VALUES('source one'),('source two'),('source three');
          INSERT INTO "Merge Schema".children VALUES(1,'a',NULL),(2,'b','{"source":true}');
          INSERT INTO "Merge Schema".libraries VALUES(1,NULL);
          INSERT INTO "Merge Schema".snapshots VALUES(1,1);
          UPDATE "Merge Schema".libraries SET active_snapshot=1;
          INSERT INTO "Merge Schema".key_only VALUES(1),(2);
          INSERT INTO "Merge Schema".partitioned VALUES(1,1,'source'),(2,1,'new');''')
    with target.connect() as c:
        c.execute('''INSERT INTO "Merge Schema".parents(id,label) OVERRIDING SYSTEM VALUE VALUES(1,'old destination'),(50,'destination only');
          INSERT INTO "Merge Schema".children VALUES(1,'a','{"source":false}'),(50,'z','{"keep":true}');
          INSERT INTO "Merge Schema".libraries VALUES(1,NULL),(20,NULL);
          INSERT INTO "Merge Schema".snapshots VALUES(20,20);
          UPDATE "Merge Schema".libraries SET active_snapshot=20;
          INSERT INTO "Merge Schema".key_only VALUES(1),(50);
          INSERT INTO "Merge Schema".partitioned VALUES(1,1,'old'),(50,1,'keep');
          CREATE TABLE public.destination_only(id INT PRIMARY KEY);
          INSERT INTO public.destination_only VALUES(500);''')


def test_source_wins_preserves_destination_only_and_sequences(databases,options,tmp_path):
    source,target=databases;seed(source,target)
    options=replace(options,mode='upsert')
    assert service.plan(source,target,options)['upsert']['conflict_policy']=='source-wins'
    report=service.run(source,target,options,tmp_path,True)
    assert report['mode']=='upsert' and report['destination_only_rows_preserved']==6
    with target.connect() as c:
        assert c.execute('SELECT id,label,size FROM "Merge Schema".parents ORDER BY id').fetchall()==[
            (1,'source one',10),(2,'source two',10),(3,'source three',12),(50,'destination only',16)]
        assert c.execute('SELECT active_snapshot FROM "Merge Schema".libraries WHERE id=1').fetchone()[0]==1
        assert c.execute('SELECT active_snapshot FROM "Merge Schema".libraries WHERE id=20').fetchone()[0]==20
        assert c.execute('SELECT value FROM "Merge Schema".children WHERE parent=1 AND code=\'a\'').fetchone()[0] is None
        assert c.execute('SELECT id FROM public.destination_only').fetchone()[0]==500
        assert c.execute('SELECT last_value FROM "Merge Schema".parents_id_seq').fetchone()[0]>=50
    assert service.run(source,target,options,tmp_path,True)['status']=='complete'
    with target.connect() as c:
        assert c.execute('INSERT INTO "Merge Schema".parents(label) VALUES(\'later\') RETURNING id').fetchone()[0]>50


@pytest.mark.parametrize('window',['before_commit','after_commit'])
@pytest.mark.parametrize('unit_prefix',['stage:','merge:','patch:'])
def test_upsert_commit_crash_resumes(databases,options,tmp_path,window,unit_prefix):
    source,target=databases;seed(source,target);options=replace(options,mode='upsert');seen=[]
    def interrupt(phase,unit):
        if phase==window and unit.startswith(unit_prefix) and not seen:
            seen.append(unit);raise InterruptedError('synthetic commit crash')
    with pytest.raises(InterruptedError):service.run(source,target,options,tmp_path,True,inject=interrupt)
    job=json.loads((tmp_path/'job.json').read_text())['job_id']
    with target.connect() as c:assert bool(state.lookup(c,job,seen[0]))==(window=='after_commit')
    assert service.run(source,target,options,tmp_path,True)['status']=='complete'
    with target.connect() as c:assert c.execute('SELECT count(*) FROM "Merge Schema".parents').fetchone()[0]==4


@pytest.mark.parametrize('problem',['missing','no-key','different-type','nonnullable-cycle','trigger'])
def test_incompatible_merge_fails_before_any_destination_writes(databases,options,problem):
    source,target=databases
    with source.connect() as c:
        c.execute('CREATE TABLE public.items(id INT PRIMARY KEY, value TEXT)')
        c.execute("INSERT INTO public.items VALUES(1,'source')")
    with target.connect() as c:
        if problem!='missing':
            c.execute('CREATE TABLE public.items(id INT PRIMARY KEY, value '+('INT' if problem=='different-type' else 'TEXT')+')')
        if problem=='no-key':
            c.execute('ALTER TABLE public.items DROP CONSTRAINT items_pkey')
    if problem in ('nonnullable-cycle','trigger'):
        for endpoint in (source,target):
            with endpoint.connect() as c:
                if problem=='nonnullable-cycle':
                    c.execute('ALTER TABLE public.items ADD COLUMN parent INT NOT NULL DEFAULT 1 REFERENCES public.items(id)')
                else:
                    c.execute('''CREATE FUNCTION public.hook() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$;
                        CREATE TRIGGER item_hook BEFORE INSERT ON public.items FOR EACH ROW EXECUTE FUNCTION public.hook();''')
    with pytest.raises(ValueError):service.plan(source,target,replace(options,mode='upsert'))
    with target.connect() as c:assert c.execute("SELECT to_regnamespace('_chess_transfer')").fetchone()[0] is None


def test_baseline_and_destination_tampering_are_rejected(databases,options,tmp_path):
    source,target=databases;seed(source,target);options=replace(options,mode='upsert')
    service.run(source,target,options,tmp_path,True)
    (tmp_path/'tracker.json').write_text('{broken')
    assert service.run(source,target,options,tmp_path,True,recover_tracker=True)['status']=='complete'
    baseline=tmp_path/'upsert-baseline.json';original=baseline.read_text();baseline.write_text('{}')
    with pytest.raises(ValueError,match='baseline'):service.run(source,target,options,tmp_path,True)
    baseline.write_text(original)
    with target.connect() as c:c.execute('DELETE FROM "Merge Schema".parents WHERE id=3')
    with pytest.raises(ValueError,match='verification failed'):service.run(source,target,options,tmp_path,True)


def test_new_snapshot_uses_new_job_and_keeps_old_ledger(databases,options,tmp_path):
    source,target=databases;seed(source,target);options=replace(options,mode='upsert')
    service.run(source,target,options,tmp_path/'first',True)
    with source.connect() as c:c.execute('UPDATE "Merge Schema".parents SET label=\'new version\' WHERE id=1')
    service.run(source,target,options,tmp_path/'second',True)
    with target.connect() as c:
        assert c.execute('SELECT label FROM "Merge Schema".parents WHERE id=1').fetchone()[0]=='new version'
        assert c.execute(f'SELECT count(*) FROM {state.SCHEMA}.jobs').fetchone()[0]==2


def test_alternate_unique_identity_conflict_stops_before_row_changes(databases,options,tmp_path):
    source,target=databases
    for endpoint in (source,target):
        with endpoint.connect() as c:c.execute('CREATE TABLE public.users(id INT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT)')
    with source.connect() as c:c.execute("INSERT INTO public.users VALUES(1,'same@example.invalid','source')")
    with target.connect() as c:c.execute("INSERT INTO public.users VALUES(50,'same@example.invalid','destination')")
    with pytest.raises(ValueError,match='unique identity'):service.run(source,target,replace(options,mode='upsert'),tmp_path,True)
    with target.connect() as c:assert c.execute('SELECT id,name FROM public.users').fetchall()==[(50,'destination')]


def test_upsert_repository_models_and_nullable_snapshot_cycle(databases,options,tmp_path,monkeypatch):
    """Exercise the actual study/warehouse schemas, including owner-scoped composite FKs."""
    from chess_lakehouse.personal import migrate
    source,target=databases
    for key in ('LOADER_PASSWORD','READER_PASSWORD','STUDY_PASSWORD'):monkeypatch.setenv(key,'synthetic-role-password')
    for endpoint in (source,target):
        monkeypatch.setattr(migrate,'admin_connect',endpoint.connect);migrate.bootstrap()
        with endpoint.connect() as c:c.execute("SELECT control.ensure_partition('python',DATE '2013-01-01')")
    account='00000000-0000-0000-0000-000000000001'
    library='00000000-0000-0000-0000-000000000002'
    snapshot='00000000-0000-0000-0000-000000000003'
    with source.connect() as c:
        c.execute('INSERT INTO study.accounts(id,username,username_key,password_hash) VALUES(%s,\'SyntheticSource\',\'syntheticsource\',\'synthetic\')',(account,))
        c.execute('INSERT INTO study.libraries(id,owner_id,player,player_key) VALUES(%s,%s,\'SyntheticPlayer\',\'syntheticplayer\')',(library,account))
        c.execute('INSERT INTO study.library_snapshots(id,owner_id,library_id,version,game_count,dataset_sha256) VALUES(%s,%s,%s,1,0,\'synthetic\')',(snapshot,account,library))
        c.execute('UPDATE study.libraries SET active_snapshot=%s WHERE id=%s',(snapshot,library))
    with target.connect() as c:
        c.execute('INSERT INTO study.accounts(id,username,username_key,password_hash) VALUES(%s,\'OldName\',\'oldname\',\'synthetic\')',(account,))
        c.execute("INSERT INTO study.accounts(id,username,username_key,password_hash) VALUES('00000000-0000-0000-0000-000000000009','DestinationOnly','destinationonly','synthetic')")
    report=service.run(source,target,replace(options,mode='upsert'),tmp_path,True)
    assert report['tables_verified']>=60 and report['destination_only_rows_preserved']==1
    with target.connect() as c:
        assert c.execute('SELECT username FROM study.accounts WHERE id=%s',(account,)).fetchone()[0]=='SyntheticSource'
        assert str(c.execute('SELECT active_snapshot FROM study.libraries WHERE id=%s',(library,)).fetchone()[0])==snapshot
        assert c.execute('SELECT count(*) FROM study.accounts').fetchone()[0]==2


def test_real_sigkill_after_upsert_commit_resumes(databases,options,tmp_path):
    source,target=databases;seed(source,target);ready=tmp_path/'ready'
    child='''
import pathlib,sys,time
from chess_lakehouse.transfer import service
from chess_lakehouse.transfer.settings import Endpoint,Options
def pause(phase,unit):
    if phase=='after_commit' and unit.startswith('merge:'):
        pathlib.Path(sys.argv[1],'ready').write_text(unit)
        time.sleep(60)
options=Options(mode='upsert',chunk_rows=2,chunk_bytes=1024,max_row_bytes=1024*1024,dump_jobs=2,min_free_bytes=0)
service.run(Endpoint('SOURCE'),Endpoint('TARGET'),options,sys.argv[1],True,inject=pause)
'''
    process=subprocess.Popen([sys.executable,'-c',child,str(tmp_path)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+60
        while not ready.exists():
            if process.poll() is not None:raise AssertionError(process.stderr.read().decode())
            if time.monotonic()>deadline:raise AssertionError('Upsert did not reach its committed checkpoint')
            time.sleep(.05)
        process.kill();assert process.wait(timeout=10)==-9
        job=json.loads((tmp_path/'job.json').read_text())['job_id']
        with target.connect() as c:assert state.lookup(c,job,ready.read_text()) is not None
        assert service.run(source,target,replace(options,mode='upsert'),tmp_path,True)['status']=='complete'
    finally:
        if process.poll() is None:process.kill();process.wait()
        process.stderr.close()


@pytest.mark.parametrize('different',[False,True])
def test_published_history_guards_remain_enabled(databases,options,tmp_path,different):
    source,target=databases
    for endpoint in (source,target):
        with endpoint.connect() as c:
            c.execute('''CREATE SCHEMA study;
              CREATE TABLE study.frozen(id INT PRIMARY KEY,value TEXT);
              CREATE FUNCTION study.protect_published() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN RAISE EXCEPTION 'published study data is immutable'; END $$;
              CREATE TRIGGER frozen_guard BEFORE UPDATE OR DELETE ON study.frozen
              FOR EACH ROW EXECUTE FUNCTION study.protect_published();''')
    with source.connect() as c:c.execute("INSERT INTO study.frozen VALUES(1,'same'),(2,'new')")
    with target.connect() as c:c.execute('INSERT INTO study.frozen VALUES(1,%s)',('different' if different else 'same',))
    if different:
        with pytest.raises(ValueError,match='immutable published history'):
            service.run(source,target,replace(options,mode='upsert'),tmp_path,True)
    else:
        assert service.run(source,target,replace(options,mode='upsert'),tmp_path,True)['status']=='complete'
    with target.connect() as c:
        assert c.execute("SELECT tgenabled FROM pg_trigger WHERE tgname='frozen_guard'").fetchone()[0]=='O'
        assert c.execute('SELECT count(*) FROM study.frozen').fetchone()[0]==(1 if different else 2)


def test_source_null_clears_a_matching_destination_link(databases,options,tmp_path):
    source,target=databases;seed(source,target)
    with source.connect() as c:c.execute('UPDATE "Merge Schema".libraries SET active_snapshot=NULL WHERE id=1')
    assert service.run(source,target,replace(options,mode='upsert'),tmp_path,True)['status']=='complete'
    with target.connect() as c:
        assert c.execute('SELECT active_snapshot FROM "Merge Schema".libraries WHERE id=1').fetchone()[0] is None
        assert c.execute('SELECT active_snapshot FROM "Merge Schema".libraries WHERE id=20').fetchone()[0]==20


def test_cli_explicit_upsert_plans_then_runs(databases,tmp_path):
    source,target=databases
    for endpoint in (source,target):
        with endpoint.connect() as c:c.execute('CREATE TABLE public.items(id INT PRIMARY KEY,value TEXT)')
    with source.connect() as c:c.execute("INSERT INTO public.items VALUES(1,'source'),(2,'new')")
    with target.connect() as c:c.execute("INSERT INTO public.items VALUES(1,'old'),(50,'keep')")
    config=tmp_path/'options.toml';config.write_text('[options]\nmin_free_bytes=0\nchunk_rows=1\n')
    command=[sys.executable,'-m','chess_lakehouse.transfer','--mode','upsert','--config',str(config),'--workdir',str(tmp_path/'job')]
    assert json.loads(subprocess.run(command,check=True,text=True,capture_output=True).stdout)['mode']=='upsert'
    with target.connect() as c:assert c.execute("SELECT to_regnamespace('_chess_transfer')").fetchone()[0] is None
    report=json.loads(subprocess.run(command+['--run','--maintenance-window'],check=True,text=True,capture_output=True).stdout)
    assert report['status']=='complete' and report['destination_only_rows_preserved']==1
    with target.connect() as c:assert c.execute('SELECT * FROM public.items ORDER BY id').fetchall()==[(1,'source'),(2,'new'),(50,'keep')]
