"""Explicit two-worker Docker-cluster correctness check in a disposable database."""
from dataclasses import replace
from pathlib import Path
import json
import os
import shutil
from uuid import uuid4

from psycopg import sql
import zstandard as zstd

from ..pipeline import sha256_file
from .comparison import compare
from .migrate import admin_connect,migrate
from .settings import Settings,Source
from .storage import atomic_json
from .workflow import run,event


def main():
    """Exercise the supported two-worker Spark path using isolated synthetic records."""
    previous={name:os.environ.get(name) for name in ('PGDATABASE','PGUSER','PGPASSWORD')}
    database='chess_cluster_test_'+uuid4().hex[:12]
    root=Path('/data/cluster-smoke')/uuid4().hex
    root.mkdir(parents=True)
    admin=admin_connect()
    admin.autocommit=True
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        os.environ['PGDATABASE']=database
        migrate()
        os.environ['PGUSER']='chess_loader'
        os.environ['PGPASSWORD']=os.environ['LOADER_PASSWORD']
        archive=root/'fixture.zst'
        archive.write_bytes(zstd.ZstdCompressor().compress(Path('/app/tests/fixtures/games.pgn').read_bytes()))
        settings=Settings(Source('2013-01',sha256_file(archive),5,1000000,'synthetic-no-network'),
            data_root=root,frame_target_bytes=600,parse_batch_rows=2,copy_batch_rows=2,
            max_reject_ratio=0.5,max_unknown_player_ratio=1,min_free_disk_bytes=0,
            spark_master=os.environ['SPARK_MASTER'])
        for engine in ('python','spark'):
            run(engine,settings,root/'reports',group='cluster-smoke',archive=archive)
        result=compare('cluster-smoke',root/'reports',required_engines=('python','spark'))
        atomic_json('/reports/cluster-smoke.json',dict(status='passed',master=settings.spark_master,
            workers=2,synthetic_fixture=True,equality=result['equality'],capacity_benchmark=False))
        event('cluster_smoke_passed',workers=2)
    finally:
        for name,value in previous.items():
            if value is None: os.environ.pop(name,None)
            else: os.environ[name]=value
        admin.execute(sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(database)))
        admin.close()
        shutil.rmtree(root,ignore_errors=True)


if __name__=='__main__':
    main()
