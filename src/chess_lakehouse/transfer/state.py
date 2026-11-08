"""Atomic private tracker files plus an authoritative destination commit ledger."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

SCHEMA = "_chess_transfer"


def digest(path):
    """Hash an artifact in bounded blocks; reject symlinks rather than following them."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Migration artifacts cannot be symlinks")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_json(path, value):
    """Replace a mode-0600 JSON file only after file and directory fsync."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(root):
    """Allow only one local writer per work directory; keep lock state out of JSON."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another migration is using this work directory") from None
        yield


def bootstrap(conn, job, config_hash, manifest_hash, allow_existing=False):
    """Bind a restore or explicit populated-destination upsert to its commit ledger."""
    if not conn.execute("SELECT pg_try_advisory_lock(hashtext('chess-database-transfer'))").fetchone()[0]:
        raise ValueError("Another migration owns this destination")
    exists = conn.execute("SELECT to_regnamespace(%s)", (SCHEMA,)).fetchone()[0]
    if not exists:
        from .inventory import destination_has_objects
        if not allow_existing and destination_has_objects(conn):
            raise ValueError("Destination is not empty; use a new database. No objects were deleted.")
        with conn.transaction():
            conn.execute(f"CREATE SCHEMA {SCHEMA}")
            conn.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
            conn.execute(f"""CREATE TABLE {SCHEMA}.jobs(id UUID PRIMARY KEY,config_hash TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,status TEXT NOT NULL,report JSONB NOT NULL DEFAULT '{{}}')""")
            conn.execute(f"""CREATE TABLE {SCHEMA}.units(job_id UUID NOT NULL REFERENCES {SCHEMA}.jobs,
                unit_id TEXT NOT NULL,checksum TEXT NOT NULL,rows BIGINT NOT NULL DEFAULT 0,
                committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(job_id,unit_id))""")
            conn.execute(f"INSERT INTO {SCHEMA}.jobs VALUES(%s,%s,%s,'running','{{}}')", (job, config_hash, manifest_hash))
    row = conn.execute(f"SELECT config_hash,manifest_hash FROM {SCHEMA}.jobs WHERE id=%s", (job,)).fetchone()
    if row is None and allow_existing:
        if conn.execute(f"SELECT 1 FROM {SCHEMA}.jobs WHERE status<>'complete' LIMIT 1").fetchone():
            raise ValueError("Resume the unfinished destination migration before starting another upsert")
        with conn.transaction():
            conn.execute(f"INSERT INTO {SCHEMA}.jobs VALUES(%s,%s,%s,'running','{{}}')", (job, config_hash, manifest_hash))
        row = (config_hash, manifest_hash)
    if not row or row != (config_hash, manifest_hash):
        raise ValueError("Destination ledger belongs to a different archive or configuration")


def completed(conn, job):
    """Read committed units; the JSON tracker may legitimately lag this ledger."""
    return {row[0]: {"checksum": row[1], "rows": row[2]} for row in conn.execute(
        f"SELECT unit_id,checksum,rows FROM {SCHEMA}.units WHERE job_id=%s", (job,))}


def mark(conn, job, unit, checksum, rows=0):
    """Insert a checkpoint inside the same transaction as the restored data."""
    conn.execute(f"INSERT INTO {SCHEMA}.units(job_id,unit_id,checksum,rows) VALUES(%s,%s,%s,%s)",
                 (job, unit, checksum, rows))


def lookup(conn, job, unit):
    """Look up one committed unit through the ledger primary key in bounded memory."""
    row = conn.execute(f"SELECT checksum,rows FROM {SCHEMA}.units WHERE job_id=%s AND unit_id=%s", (job, unit)).fetchone()
    return {"checksum": row[0], "rows": row[1]} if row else None


def finish(conn, job, report):
    """Record completion only after target and maintenance-window checks pass."""
    conn.execute(f"UPDATE {SCHEMA}.jobs SET status='complete',report=%s WHERE id=%s", (Jsonb(report), job))
