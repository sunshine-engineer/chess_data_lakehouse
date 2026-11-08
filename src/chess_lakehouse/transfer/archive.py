"""Consistent native archives, bounded artifact checks, and native DDL replay."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess

from . import inventory
from .state import SCHEMA, atomic_json, digest


def command(args, endpoint=None, output=None, log=None):
    """Run a fixed PostgreSQL command without a shell or credentials in arguments."""
    env = endpoint.environment() if endpoint else None
    result = subprocess.run(args, env=env, stdout=output if output else subprocess.PIPE,
                            stderr=log if log else subprocess.PIPE, check=False)
    if result.returncode:
        raise ValueError(f"{args[0]} failed; inspect the private migration log and PostgreSQL prerequisites")
    return result.stdout.decode("utf-8") if result.stdout is not None else ""


def client_major():
    """Return the installed pg_dump major version for compatibility checks."""
    return int(re.search(r"\b(\d+)\.", command(["pg_dump", "--version"]))[1])


def schema_sql(endpoint, options, snapshot=None):
    """Produce a comparable native schema, including object owners/ACLs by policy."""
    args = ["pg_dump", "--schema-only", "--no-password", "--exclude-schema=" + SCHEMA,
            "--no-tablespaces", "--encoding=UTF8"]
    if options.ownership == "current-user": args.append("--no-owner")
    if options.privileges == "omit": args.append("--no-privileges")
    if snapshot: args.append("--snapshot=" + snapshot)
    return command(args, endpoint)


def schema_digest(text):
    """Ignore dump timestamps/client comments and random psql restriction tokens."""
    lines = [line.rstrip() for line in text.splitlines()
             if line.strip() and not line.startswith(("--", "\\restrict ", "\\unrestrict "))]
    # Unlogged heaps are temporarily made logged while a transfer is incomplete.
    # Their original persistence is independently restored at final commit.
    return hashlib.sha256("\n".join(lines).replace("CREATE UNLOGGED TABLE ", "CREATE TABLE ").encode("utf-8")).hexdigest()


def toc(root, section):
    """Return PostgreSQL's section-filtered restore order; use OIDs, not split names."""
    text = command(["pg_restore", "--list", "--section=" + section, str(Path(root)/"archive")])
    rows = []
    for line in text.splitlines():
        match = re.match(r"^(\d+);\s+(\d+)\s+(\d+)\s+(.*)$", line)
        if match:
            rows.append(dict(id=match[1], oid=match[3], table_data=match[4].startswith("TABLE DATA "), line=line))
    return rows


def build(root, source, options):
    """Write one consistent directory dump and its validation manifest atomically.

    An interrupted dump restarts; committed restore chunks never depend on a
    live source snapshot. Partial dump directories are not treated as resumable.
    """
    root = Path(root); partial = root/"archive.partial"
    if partial.exists(): shutil.rmtree(partial)
    if shutil.disk_usage(root).free < options.min_free_bytes:
        raise OSError("Insufficient disk space for a migration archive")
    with source.connect() as conn, (root/"dump.log").open("ab") as log:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
        metadata = inventory.capture(conn)
        text = schema_sql(source, options, snapshot)
        (root/"schema.sql").write_text(text)
        metadata["schema_sha256"] = schema_digest(text)
        args = ["pg_dump", "--format=directory", "--file="+str(partial),
                "--jobs="+str(options.dump_jobs), "--compress=6", "--snapshot="+snapshot,
                "--no-password", "--exclude-schema="+SCHEMA, "--no-tablespaces", "--encoding=UTF8"]
        process = subprocess.Popen(args, env=source.environment(), stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        try:
            while True:
                try:
                    status = process.wait(timeout=1)
                    if status: raise ValueError("pg_dump failed; inspect private dump.log")
                    break
                except subprocess.TimeoutExpired:
                    if shutil.disk_usage(root).free < options.min_free_bytes:
                        raise OSError("Migration disk reserve reached during pg_dump")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait()
        os.replace(partial, root/"archive")
        data = toc(root, "data")
        selected = {row["oid"] for row in data if row["table_data"]}
        for oid, relation in metadata["relations"].items():
            if oid in selected or relation["kind"] == "m":
                metadata["fingerprints"][oid] = inventory.rows_digest(conn, relation)
        metadata["data_order"] = data
        metadata["pre_order"] = toc(root, "pre-data")
        metadata["post_order"] = toc(root, "post-data")
    metadata["files"] = {str(path.relative_to(root)): digest(path)
                         for path in sorted((root/"archive").iterdir()) if path.is_file()}
    metadata["files"]["schema.sql"] = digest(root/"schema.sql")
    atomic_json(root/"manifest.json", metadata)
    return metadata


def verify(root):
    """Reject missing, extra, replaced or corrupted archive files before resuming."""
    root = Path(root); metadata = json.loads((root/"manifest.json").read_text())
    actual = {str(p.relative_to(root)) for p in (root/"archive").iterdir() if p.is_file()}
    expected = {name for name in metadata["files"] if name.startswith("archive/")}
    if actual != expected:
        raise ValueError("Archive file inventory changed")
    for name, checksum in metadata["files"].items():
        path = root/name
        if root.resolve() not in path.resolve().parents or not path.is_file() or digest(path) != checksum:
            raise ValueError("Archive checksum mismatch; restore the original artifacts")
    return metadata


def chunks(root, entry, options):
    """Read native COPY-text rows from a directory dump with bounded memory.

    Embedded newlines in values are escaped by pg_dump. A literal backslash-dot
    row is escaped too, so only the COPY terminator is stripped. Empty lines are
    retained for zero-column tables. gzip resumes rescan the compressed prefix.
    """
    folder = Path(root)/"archive"
    plain = folder/(entry["id"]+".dat")
    path = plain if plain.exists() else plain.with_suffix(".dat.gz")
    opener = open if path == plain else gzip.open
    buffer = bytearray(); rows = number = 0
    with opener(path, "rb") as stream:
        while line := stream.readline(options.max_row_bytes+1):
            if len(line) > options.max_row_bytes:
                raise ValueError("One COPY row exceeds max_row_bytes; increase the explicit row bound")
            if line == b"\\.\n": break
            if rows and (rows >= options.chunk_rows or len(buffer)+len(line) > options.chunk_bytes):
                yield number, bytes(buffer), rows
                number += 1; buffer.clear(); rows = 0
            buffer.extend(line); rows += 1
        if rows: yield number, bytes(buffer), rows
