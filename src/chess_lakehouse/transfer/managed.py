"""Provision a managed PostgreSQL login/database and native application schema.

Source ownership names become ordinary NOLOGIN ownership/permission groups;
superuser attributes and role passwords are never copied. The requested login
inherits those groups. Data remains the responsibility of the upsert entrypoint.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets

import psycopg
from psycopg import sql

from . import archive, inventory, state
from .settings import Endpoint, Options


def schema_names(conn):
    """Select application namespaces while excluding provider/system/transfer state."""
    return [row[0] for row in conn.execute("""SELECT nspname FROM pg_namespace
        WHERE left(nspname,3)<>'pg_' AND nspname NOT IN ('information_schema',%s) ORDER BY nspname""",(state.SCHEMA,))]


def schema_hash(endpoint, schemas):
    """Compare native DDL only for the explicitly selected application namespaces."""
    args=['pg_dump','--schema-only','--no-password','--no-tablespaces','--encoding=UTF8']
    args += ['--schema='+sql.Identifier(name).as_string() for name in schemas]
    return archive.schema_digest(archive.command(args,endpoint))


def prepare(source, admin, target, login, password_file, workdir, execute=False):
    """Create missing roles/database and atomically restore schema into an empty DB.

    Existing logins are never assigned a replacement password. An existing
    application schema must already equal the source's native schema. A completed
    preparation is reusable; data is never restored, dropped or truncated here.
    """
    os.umask(0o077)
    database=os.environ.get(target.prefix+'_PGDATABASE','')
    if not all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}',value) for value in (database,login)):
        raise ValueError('Use simple lowercase database/login names')
    with source.connect() as src,admin.connect(autocommit=True) as dst:
        identity=inventory.identity(src);target_version=int(dst.execute('SHOW server_version_num').fetchone()[0])
        if identity['server_version']//10000>archive.client_major() or archive.client_major()>target_version//10000:
            raise ValueError('Managed target and PostgreSQL client must support the source major version')
        extra_extensions=src.execute("SELECT extname FROM pg_extension WHERE extname<>'plpgsql'").fetchall()
        if extra_extensions:raise ValueError('Provision/review managed extension support before schema preparation')
        schemas=schema_names(src);roles=inventory.roles(src)
        public=src.execute("SELECT oid::text,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'").fetchone()
        existing_roles={row[0] for row in dst.execute('SELECT rolname FROM pg_roles')}
        database_exists=bool(dst.execute('SELECT 1 FROM pg_database WHERE datname=%s',(database,)).fetchone())
        # Dependencies on the pinned bootstrap superuser are not necessarily
        # present in pg_shdepend. Collect actual owners as well as ACL roles.
        owners={row[0] for row in src.execute('''WITH app AS (
            SELECT oid FROM pg_namespace WHERE nspname=ANY(%s))
            SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE oid IN (SELECT oid FROM app)
            UNION SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relnamespace IN (SELECT oid FROM app)
            UNION SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace IN (SELECT oid FROM app)
            UNION SELECT pg_get_userbyid(typowner) FROM pg_type WHERE typnamespace IN (SELECT oid FROM app)''',(schemas,))}
        groups=sorted(name for name in owners|{r['name'] for r in roles}|{identity['owner']} if not name.startswith('pg_'))
        result=dict(database=database,login=login,schemas=schemas,ownership_groups=groups,
                    target_instance=os.environ.get('TARGET_INSTANCE_CONNECTION_NAME'),
                    groups_to_create=[name for name in groups if name not in existing_roles],
                    create_login=login not in existing_roles,create_database=not database_exists,
                    source_identity=identity,source_schema_sha256=schema_hash(source,schemas),
                    public_schema_owner=public[1] if public else None,
                    password_file=str(password_file),data_strategy='upsert',superuser_attributes_copied=False)
        if not execute:return result
        root=Path(workdir);root.mkdir(parents=True,exist_ok=True)
        with state.lock(root):
            manifest=root/'schema-preparation.json'
            if manifest.exists():
                prior=json.loads(manifest.read_text())
                if any(prior[k]!=result[k] for k in ('database','login','target_instance','source_identity','source_schema_sha256')):
                    raise ValueError('Schema preparation workdir belongs to another source/schema/target')
            else:state.atomic_json(manifest,result)
            password_file=Path(password_file)
            if login in existing_roles and not password_file.is_file():
                raise ValueError('Login already exists; provide its original password file. No password was changed.')
            if not password_file.exists():
                password_file.parent.mkdir(parents=True,exist_ok=True)
                with password_file.open('x') as stream:
                    os.chmod(password_file,0o600);stream.write(secrets.token_urlsafe(36)+'\n');stream.flush();os.fsync(stream.fileno())
            password=password_file.read_text().rstrip('\r\n')
            if not password:raise ValueError('Login password file is empty')
            # Validate an existing login before granting it any new groups.
            if login in existing_roles:
                env=admin.environment()
                with psycopg.connect(host=env['PGHOST'],port=env.get('PGPORT','5432'),dbname=env['PGDATABASE'],
                        user=login,password=password,sslmode=env.get('PGSSLMODE','prefer'),connect_timeout=10):pass
            administrator=dst.execute('SELECT current_user').fetchone()[0]
            with dst.transaction():
                for name in result['groups_to_create']:
                    dst.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(name)))
                if login not in existing_roles:
                    dst.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(
                        sql.Identifier(login),sql.Literal(password)))
                for group in groups:
                    # Managed providers cannot recreate source SUPERUSER; retain
                    # only database-object ownership through ordinary groups.
                    if group in (login,administrator):continue
                    privileged=dst.execute('SELECT rolsuper FROM pg_roles WHERE rolname=%s',(group,)).fetchone()[0]
                    if privileged:raise ValueError('Cannot grant a destination superuser ownership role to the application login')
                    dst.execute(sql.SQL('GRANT {} TO {},{}').format(sql.Identifier(group),sql.Identifier(login),sql.Identifier(administrator)))
            if not database_exists:
                dst.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING {} LC_COLLATE {} LC_CTYPE {}').format(
                    sql.Identifier(database),sql.Identifier(identity['owner']),sql.Literal(identity['encoding']),
                    sql.Literal(identity['collation']),sql.Literal(identity['ctype'])))
            with target.connect() as conn:
                actual=inventory.identity(conn)
                if any(actual[k]!=identity[k] for k in ('encoding','collation','ctype','owner')):
                    raise ValueError('Managed destination database locale/owner differs; no schema objects were overwritten')
                populated=inventory.destination_has_objects(conn)
            if populated:
                if schema_hash(target,schemas)!=result['source_schema_sha256']:
                    raise ValueError('Existing managed schema differs; schema preparation never replaces existing objects')
            else:
                dump=root/'schema.dump'
                args=['pg_dump','--format=custom','--schema-only','--no-password','--no-tablespaces','--file='+str(dump)]
                args += ['--schema='+sql.Identifier(name).as_string() for name in schemas]
                with (root/'schema-restore.log').open('ab') as log:
                    archive.command(args,source,log=log)
                    # Every normal fresh database already has public. Skip only
                    # its CREATE entry; retain the native ACL/comment entries.
                    listing=archive.command(['pg_restore','--list',str(dump)])
                    lines=[]
                    for line in listing.splitlines():
                        match=re.match(r'^(\d+);\s+(\d+)\s+(\d+)\s+(.*)$',line)
                        if public and match and match[2]=='2615' and match[3]==public[0] and match[4].startswith('SCHEMA '):continue
                        lines.append(line)
                    selection=root/'schema-selection.list';selection.write_text('\n'.join(lines)+'\n')
                    archive.command(['pg_restore','--exit-on-error','--single-transaction','--no-tablespaces',
                        '--use-list='+str(selection),'--dbname='+database,str(dump)],target,log=log)
                if public:
                    with target.connect() as conn:
                        conn.execute(sql.SQL('ALTER SCHEMA public OWNER TO {}').format(sql.Identifier(public[1])))
            if schema_hash(target,schemas)!=result['source_schema_sha256']:
                raise ValueError('Managed application schema verification failed')
            result.update(status='schema-ready',schema_verified=True,target_identity=actual)
            state.atomic_json(manifest,result)
            return result


def main():
    """Plan or prepare the managed database; credentials are mounted file paths."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login',default=os.environ.get('TARGET_PGUSER','dev'))
    parser.add_argument('--password-file',type=Path,default=Path(os.environ.get('TARGET_PGPASSWORD_FILE','/private/dev-password')))
    parser.add_argument('--workdir',type=Path,default=Path('/migration/cloudsql-schema'))
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if os.environ.get('CHESS_CONTAINER')!='1':parser.error('Run this tool inside Docker')
    try:
        result=prepare(Endpoint('SOURCE'),Endpoint('ADMIN'),Endpoint('TARGET'),args.login,args.password_file,args.workdir,args.run)
        print(json.dumps(result,indent=2,sort_keys=True))
    except Exception as exc:
        message='Database operation failed; inspect private schema-restore.log and managed permissions' if isinstance(exc,psycopg.Error) else str(exc)
        parser.exit(1,f'Managed preparation stopped ({type(exc).__name__}): {message}\n')


if __name__=='__main__':main()
