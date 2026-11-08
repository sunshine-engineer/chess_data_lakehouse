"""Transactional table chunks and native object restores with commit reconciliation."""
import hashlib
from pathlib import Path
import subprocess

from psycopg import sql

from . import state


def native(root, target, conn, job, entry, options, inject=None):
    """Restore one native TOC object and its ledger marker in an explicit transaction.

    Unlike psql --single-transaction, EOF must never commit a truncated producer
    stream. BEGIN is written explicitly; COMMIT is sent only after pg_restore
    exits successfully. A process crash therefore rolls back incomplete work.
    Sequence SETVAL entries are replay-idempotent, although PostgreSQL sequence
    value changes themselves are nontransactional.
    """
    unit = "native:" + entry["id"]
    checksum = hashlib.sha256(entry["line"].encode()).hexdigest()
    previous = state.lookup(conn, job, unit)
    if previous:
        if previous["checksum"] != checksum:
            raise ValueError("A native restore unit changed since its checkpoint")
        return False
    root = Path(root); selection = root/"selection.list"
    selection.write_text(entry["line"]+"\n")
    args = ["pg_restore", "--exit-on-error", "--no-tablespaces", "--file=-",
            "--use-list="+str(selection), str(root/"archive")]
    if options.ownership == "current-user": args.insert(1, "--no-owner")
    if options.privileges == "omit": args.insert(1, "--no-privileges")
    marker = sql.SQL("INSERT INTO {}.units(job_id,unit_id,checksum,rows) VALUES({},{},{},0);\nCOMMIT;\n").format(
        sql.Identifier(state.SCHEMA), sql.Literal(job), sql.Literal(unit), sql.Literal(checksum)).as_string(conn).encode()
    with (root/"restore.log").open("ab") as log:
        producer = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log)
        consumer = subprocess.Popen(["psql", "--no-password", "-X", "-q", "--set=ON_ERROR_STOP=1"],
                                    stdin=subprocess.PIPE, stdout=log, stderr=log, env=target.environment())
        try:
            consumer.stdin.write(f"BEGIN; SET LOCAL lock_timeout = '{options.lock_timeout_seconds}s';\n".encode())
            for block in iter(lambda: producer.stdout.read(65536), b""):
                consumer.stdin.write(block)
            if producer.wait():
                raise ValueError("Native object restore failed; inspect private restore.log")
            if inject: inject("native_before_commit", unit)
            consumer.stdin.write(marker); consumer.stdin.close()
            if consumer.wait():
                raise ValueError("Destination rejected a native object; inspect private restore.log")
            if inject: inject("native_after_commit", unit)
        except BaseException:
            if producer.poll() is None: producer.terminate()
            if consumer.poll() is None: consumer.terminate()
            producer.wait(); consumer.wait()
            raise
        finally:
            producer.stdout.close()
            if not consumer.stdin.closed:
                try: consumer.stdin.close()
                except BrokenPipeError: pass
    return True


def protect_unlogged(conn, job, relations):
    """Keep incomplete unlogged table copies durable across a target server crash."""
    done = state.completed(conn, job)
    for oid, relation in relations.items():
        unit = "durability:"+oid
        if relation["kind"] == "r" and relation["persistence"] == "u" and unit not in done:
            with conn.transaction():
                conn.execute(sql.SQL("ALTER TABLE ONLY {} SET LOGGED").format(sql.Identifier(relation["schema"], relation["name"])))
                state.mark(conn, job, unit, "logged-until-completion")


def restore_persistence(conn, relations):
    """Restore source unlogged semantics inside the transaction marking completion."""
    for relation in relations.values():
        if relation["kind"] == "r" and relation["persistence"] == "u":
            conn.execute(sql.SQL("ALTER TABLE ONLY {} SET UNLOGGED").format(sql.Identifier(relation["schema"], relation["name"])))


def copy_chunk(conn, job, entry, relation, number, data, rows, inject=None):
    """Commit one bounded COPY payload and marker together; skip committed retries."""
    unit = f"copy:{entry['id']}:{number}"
    checksum = hashlib.sha256(data).hexdigest()
    previous = state.lookup(conn, job, unit)
    if previous:
        if previous != {"checksum": checksum, "rows": rows}:
            raise ValueError("COPY chunk differs from its committed checkpoint")
        return False
    columns = sql.SQL(" ({})").format(sql.SQL(",").join(map(sql.Identifier, relation["columns"]))) if relation["columns"] else sql.SQL("")
    query = sql.SQL("COPY {}{} FROM STDIN WITH (FORMAT text)").format(
        sql.Identifier(relation["schema"], relation["name"]), columns)
    with conn.transaction():
        with conn.cursor().copy(query) as copy:
            copy.write(data)
        if inject: inject("before_commit", unit)
        state.mark(conn, job, unit, checksum, rows)
    if inject: inject("after_commit", unit)
    return True


def database_metadata(conn, job, metadata, options):
    """Restore database-local settings/grants against the target database's name."""
    if state.lookup(conn, job, "database-metadata"): return
    target = conn.execute("SELECT current_database()").fetchone()[0]
    source = metadata["identity"]
    with conn.transaction():
        if options.ownership == "preserve":
            conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(target), sql.Identifier(source["owner"])))
        conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(target), sql.Literal(source["comment"])))
        for setting in metadata["database_options"]["settings"]:
            for item in setting["values"]:
                name, value = item.split("=", 1)
                if setting["role"]:
                    query = sql.SQL("ALTER ROLE {} IN DATABASE {} SET {} TO {}").format(
                        sql.Identifier(setting["role"]), sql.Identifier(target), sql.Identifier(name), sql.Literal(value))
                else:
                    query = sql.SQL("ALTER DATABASE {} SET {} TO {}").format(sql.Identifier(target), sql.Identifier(name), sql.Literal(value))
                conn.execute(query)
        if options.privileges == "preserve":
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(target)))
            for grant in metadata["database_options"]["grants"]:
                if grant["privilege"] not in ("CONNECT", "CREATE", "TEMPORARY"): raise ValueError("Unknown database privilege")
                conn.execute(sql.SQL("GRANT {} ON DATABASE {} TO {}{}").format(
                    sql.SQL(grant["privilege"]), sql.Identifier(target),
                    sql.Identifier(grant["role"]) if grant["role"] else sql.SQL("PUBLIC"),
                    sql.SQL(" WITH GRANT OPTION") if grant["grantable"] else sql.SQL("")))
        state.mark(conn, job, "database-metadata", "v1")
