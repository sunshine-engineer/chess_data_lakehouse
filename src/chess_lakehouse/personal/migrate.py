"""Administrative additive schema migration and study-only database login."""
import hashlib
import os
from pathlib import Path
import psycopg
from psycopg import sql
from ..optimized.migrate import admin_connect,migrate


def bootstrap():
    password=os.environ.get('STUDY_PASSWORD')
    if not password:
        raise ValueError('STUDY_PASSWORD is required for the isolated study role')
    with admin_connect() as conn:
        migrate(conn)
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('chess-study-schema'))")
            for version,name in ((3,'003_personal.sql'),(4,'004_workspaces.sql'),(5,'005_workspace_positions.sql'),(6,'006_board_learning.sql'),(7,'007_position_context.sql'),(8,'008_incremental_review.sql')):
                script=Path(__file__).parents[1]/'migrations'/name
                checksum=hashlib.sha256(script.read_bytes()).hexdigest()
                prior=conn.execute('SELECT checksum FROM control.schema_migrations WHERE version=%s',(version,)).fetchone()
                if prior and prior[0]!=checksum:
                    raise ValueError('personal migration checksum changed; add a new migration')
                if not prior:
                    conn.execute(script.read_text())
                    conn.execute('INSERT INTO control.schema_migrations(version,checksum) VALUES(%s,%s)',(version,checksum))
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='chess_study'").fetchone():
                conn.execute(sql.SQL('CREATE ROLE chess_study LOGIN PASSWORD {}').format(sql.Literal(password)))
            else:
                conn.execute(sql.SQL('ALTER ROLE chess_study PASSWORD {}').format(sql.Literal(password)))
            conn.execute('REVOKE ALL ON SCHEMA study FROM PUBLIC')
            conn.execute('GRANT USAGE ON SCHEMA study TO chess_study')
            conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA study TO chess_study')
            conn.execute('GRANT USAGE ON SCHEMA academy TO chess_study')
            conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA academy TO chess_study')
    print('Personal study schema ready; study role has no archive grants',flush=True)


if __name__=='__main__':
    bootstrap()
