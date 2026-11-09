"""Managed-database ownership provisioning and protected legacy history transfer."""
from dataclasses import replace
import os
from uuid import uuid4

import pytest
from psycopg import sql

from test_postgres_transfer import databases,options
from chess_lakehouse.transfer import service,inventory
from chess_lakehouse.transfer.managed import prepare
from chess_lakehouse.transfer.settings import Endpoint


@pytest.mark.parametrize('chunk_rows',[1,2])
def test_immutable_parent_history_respects_single_statement_bound(databases,options,tmp_path,chunk_rows):
    source,target=databases
    for endpoint in (source,target):
        with endpoint.connect() as c:
            c.execute('''CREATE SCHEMA study;
                CREATE TABLE study.history(id INT PRIMARY KEY,parent INT REFERENCES study.history(id),value TEXT);
                CREATE FUNCTION study.protect_published() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'published study data is immutable'; END $$;
                CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON study.history
                FOR EACH ROW EXECUTE FUNCTION study.protect_published();''')
    with source.connect() as c:c.execute("INSERT INTO study.history VALUES(1,NULL,'parent'),(2,1,'child')")
    settings=replace(options,mode='upsert',chunk_rows=chunk_rows)
    if chunk_rows==1:
        with pytest.raises(ValueError,match='one bounded COPY chunk'):service.run(source,target,settings,tmp_path,True)
        with target.connect() as c:assert c.execute('SELECT count(*) FROM study.history').fetchone()[0]==0
    else:
        assert service.run(source,target,settings,tmp_path,True)['status']=='complete'
        with target.connect() as c:
            assert c.execute('SELECT id,parent,value FROM study.history ORDER BY id').fetchall()==[(1,None,'parent'),(2,1,'child')]
            assert c.execute("SELECT tgenabled FROM pg_trigger WHERE tgname='immutable_history'").fetchone()[0]=='O'


def test_prepare_owned_schema_as_non_superuser_then_upsert(databases,options,tmp_path,monkeypatch):
    source,target=databases
    suffix=uuid4().hex[:10];owner='owner_'+suffix;login='dev_'+suffix
    target_database=os.environ['TARGET_PGDATABASE']
    for key,value in list(os.environ.items()):
        if key.startswith('TARGET_PG'):monkeypatch.setenv('ADMIN_'+key[7:],value)
    monkeypatch.setenv('ADMIN_PGDATABASE','postgres')
    admin=Endpoint('ADMIN')
    with source.connect() as c:
        database=c.execute('SELECT current_database()').fetchone()[0]
        c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(owner)))
        c.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(sql.Identifier(database),sql.Identifier(owner)))
        c.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(owner)))
        c.execute('''CREATE SCHEMA model;
            CREATE TABLE model.games(id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,label TEXT);
            CREATE VIEW model.labels AS SELECT label FROM model.games;
            CREATE FUNCTION model.twice(x INT) RETURNS INT LANGUAGE sql AS 'SELECT x*2';
            INSERT INTO model.games(label) VALUES('source');''')
    with admin.connect(autocommit=True) as c:c.execute(sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(target_database)))
    password=tmp_path/'dev-password'
    monkeypatch.setenv('TARGET_PGUSER',login);monkeypatch.delenv('TARGET_PGPASSWORD')
    monkeypatch.setenv('TARGET_PGPASSWORD_FILE',str(password))
    assert prepare(source,admin,target,login,password,tmp_path/'schema')['create_database']
    try:report=prepare(source,admin,target,login,password,tmp_path/'schema',True)
    except ValueError as exc:
        log=tmp_path/'schema/schema-restore.log'
        raise AssertionError(log.read_text() if log.exists() else str(exc)) from exc
    assert report['schema_verified'] and password.is_file()
    with target.connect() as c:
        assert c.execute('SELECT count(*) FROM model.games').fetchone()[0]==0
        assert c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()==(False,False,False)
        assert c.execute('SELECT model.twice(3)').fetchone()[0]==6
    assert prepare(source,admin,target,login,password,tmp_path/'schema',True)['status']=='schema-ready'
    assert service.run(source,target,replace(options,mode='upsert'),tmp_path/'data',True)['status']=='complete'
    with target.connect() as c:assert c.execute('SELECT label FROM model.labels').fetchone()[0]=='source'


@pytest.mark.parametrize('rows',[0,1,10003])
def test_server_hash_aggregation_matches_streaming_modular_sum(databases,rows):
    source,_=databases
    with source.connect() as c:
        # Random-looking hashes exercise all four signed-word high bits, duplicate
        # contributions, carries between words, and the empty aggregate.
        query=sql.SQL("SELECT sha256(convert_to((n/3)::text,'UTF8')) FROM generate_series(1,{}) n").format(sql.Literal(rows))
        hashes=c.execute(query).fetchall()
        expected=sum(int.from_bytes(bytes(row[0]),'big') for row in hashes) % (1<<256)
        assert inventory.digest_query(c,query)=={'rows':rows,'sha256_sum':f'{expected:064x}'}
