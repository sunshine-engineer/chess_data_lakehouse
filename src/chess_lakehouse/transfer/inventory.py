"""Snapshot inventories and order-independent SHA256 row validation."""
import hashlib
import json

from psycopg import sql

from .state import SCHEMA


def identity(conn):
    """Identify a database across network aliases without requiring cluster superuser."""
    row = conn.execute("""SELECT current_database(),oid::text,pg_encoding_to_char(encoding),
        datcollate,datctype,pg_get_userbyid(datdba),obj_description(oid,'pg_database')
        FROM pg_database WHERE datname=current_database()""").fetchone()
    result = dict(zip(("database", "oid", "encoding", "collation", "ctype", "owner", "comment"), row))
    result["server_version"] = int(conn.execute("SHOW server_version_num").fetchone()[0])
    try:
        with conn.transaction():
            result["system_identifier"] = conn.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0]
    except Exception as exc:
        import psycopg
        if not isinstance(exc, psycopg.errors.InsufficientPrivilege):
            raise
        result["system_identifier"] = None
    return result


def relations(conn):
    """List physical tables and materialized views with COPY-compatible columns."""
    rows = conn.execute("""SELECT c.oid::bigint,n.nspname,c.relname,c.relkind,c.relispopulated,
        ARRAY(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0
              AND NOT a.attisdropped AND a.attgenerated='' ORDER BY a.attnum),c.relpersistence
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','m') AND left(n.nspname,3)<>'pg_'
        AND n.nspname NOT IN ('information_schema',%s) ORDER BY n.nspname,c.relname""", (SCHEMA,)).fetchall()
    return {str(row[0]): dict(oid=row[0], schema=row[1], name=row[2], kind=row[3],
                            populated=row[4], columns=row[5], persistence=row[6]) for row in rows}


def rows_digest(conn, relation):
    """Hash every typed row with bounded memory, retaining duplicate-row counts.

    A 256-bit modular sum of individual SHA256 row digests is insensitive to row
    order while detecting duplicates together with the exact row count. Read ONLY
    each physical table so inherited/partition rows are never counted twice.
    """
    total = count = 0
    if relation["kind"] == "m" and not relation["populated"]:
        return {"rows": 0, "sha256_sum": "unpopulated"}
    query = sql.SQL("SELECT sha256(convert_to(row_to_json(t)::text,'UTF8')) FROM {} {} AS t").format(
        sql.SQL("ONLY") if relation["kind"] == "r" else sql.SQL(""),
        sql.Identifier(relation["schema"], relation["name"]))
    with conn.cursor(name="transfer_fingerprint") as cursor:
        cursor.itersize = 500
        cursor.execute(query)
        for row in cursor:
            total = (total + int.from_bytes(bytes(row[0]), "big")) % (1 << 256)
            count += 1
    return {"rows": count, "sha256_sum": f"{total:064x}"}


def sequences(conn):
    """Capture last_value/is_called, including sequences behind identity columns."""
    result = []
    for schema, name in conn.execute("""SELECT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='S'
        AND left(n.nspname,3)<>'pg_' AND n.nspname<>%s ORDER BY 1,2""", (SCHEMA,)).fetchall():
        value = conn.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(sql.Identifier(schema, name))).fetchone()
        result.append(dict(schema=schema, name=name, last_value=value[0], is_called=value[1]))
    return result


def large_objects(conn):
    """Fingerprint large-object pages without loading a whole object into memory."""
    result = []; current = None; digest = None; pages = 0
    with conn.cursor(name="transfer_large_objects") as cursor:
        cursor.itersize = 100
        cursor.execute("SELECT loid,pageno,data FROM pg_largeobject ORDER BY loid,pageno")
        for oid, page, data in cursor:
            if oid != current:
                if current is not None:
                    result.append(dict(oid=current, pages=pages, sha256=digest.hexdigest()))
                current, digest, pages = oid, hashlib.sha256(), 0
            digest.update(str(page).encode() + b":" + bytes(data)); pages += 1
    if current is not None:
        result.append(dict(oid=current, pages=pages, sha256=digest.hexdigest()))
    empty = conn.execute("""SELECT oid FROM pg_largeobject_metadata m WHERE NOT EXISTS
        (SELECT 1 FROM pg_largeobject p WHERE p.loid=m.oid) ORDER BY oid""").fetchall()
    result.extend(dict(oid=row[0], pages=0, sha256=hashlib.sha256().hexdigest()) for row in empty)
    return sorted(result, key=lambda item: item["oid"])


def roles(conn):
    """Find database owner/ACL roles; never read or copy role password hashes."""
    names = {row[0] for row in conn.execute("""SELECT DISTINCT r.rolname FROM pg_roles r
        JOIN pg_shdepend d ON d.refobjid=r.oid AND d.refclassid='pg_authid'::regclass
        WHERE d.dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
        OR (d.dbid=0 AND d.classid='pg_database'::regclass AND
            d.objid=(SELECT oid FROM pg_database WHERE datname=current_database()))""").fetchall()}
    return [dict(name=row[0], login=row[1], inherit=row[2], privileged=any(row[3:]))
            for row in conn.execute("""SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname""", (list(names),)).fetchall()]


def database_options(conn):
    """Retain per-database settings and database grants as private archive metadata."""
    settings = [dict(role=row[0], values=row[1]) for row in conn.execute("""SELECT
        CASE WHEN setrole=0 THEN NULL ELSE pg_get_userbyid(setrole) END,setconfig
        FROM pg_db_role_setting WHERE setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())
        ORDER BY setrole""").fetchall()]
    grants = [dict(role=row[0], privilege=row[1], grantable=row[2]) for row in conn.execute("""SELECT
        CASE WHEN a.grantee=0 THEN NULL ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable FROM pg_database d,
        LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
        WHERE d.datname=current_database() ORDER BY 1,2""").fetchall()]
    return {"settings": settings, "grants": grants}


def capture(conn):
    """Capture the source catalog under the caller's exported repeatable-read snapshot."""
    return dict(identity=identity(conn), relations=relations(conn), roles=roles(conn),
                sequences=sequences(conn), large_objects=large_objects(conn),
                database_options=database_options(conn), fingerprints={})


def stable_json(value):
    """Serialize comparison metadata without whitespace or dictionary-order drift."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def destination_has_objects(conn):
    """Reject populated destinations including standalone public functions/types."""
    return bool(conn.execute("""SELECT EXISTS(
        SELECT 1 FROM pg_namespace WHERE left(nspname,3)<>'pg_'
          AND nspname NOT IN ('public','information_schema')
        UNION ALL SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
        UNION ALL SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
        UNION ALL SELECT 1 FROM pg_extension WHERE extname<>'plpgsql'
        UNION ALL SELECT 1 FROM pg_largeobject_metadata
        UNION ALL SELECT 1 FROM pg_event_trigger
        UNION ALL SELECT 1 FROM pg_publication
        UNION ALL SELECT 1 FROM pg_subscription
        UNION ALL SELECT 1 FROM pg_default_acl)""").fetchone()[0])
