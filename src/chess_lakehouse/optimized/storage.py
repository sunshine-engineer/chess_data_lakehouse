"""Checksummed Parquet shards and atomic manifests; no full-month buffering."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import zstandard as zstd

from ..pipeline import sha256_file
from .settings import VERSION

FRAME_SCHEMA = pa.schema([("record_number", pa.int64()), ("pgn", pa.string())])


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def file_lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def check_disk(path, minimum, additional=0):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < minimum + additional:
        raise OSError(f"insufficient working disk: {free} free bytes, need {minimum + additional}")
    return free


def iter_games(archive, max_record_bytes):
    """Source-specific Event framing, with bounded line and single-game sizes."""
    lines, size, in_comment = [], 0, False
    with Path(archive).open("rb") as raw, zstd.ZstdDecompressor().stream_reader(raw) as reader:
        with io.TextIOWrapper(reader, encoding="utf-8", errors="strict") as stream:
            while line := stream.readline(max_record_bytes + 1):
                if len(line.encode("utf-8")) > max_record_bytes:
                    raise ValueError("PGN line exceeds configured record byte limit")
                if line.startswith('[Event "') and not in_comment:
                    if lines:
                        yield "".join(lines)
                    lines, size = [line], len(line.encode("utf-8"))
                elif lines:
                    lines.append(line)
                    size += len(line.encode("utf-8"))
                elif line.strip():
                    raise ValueError("unexpected input before first Event header")
                if size > max_record_bytes:
                    raise ValueError("PGN game exceeds configured record byte limit")
                if not line.startswith("["):
                    for character in line.split(";", 1)[0]:
                        if character == "{":
                            in_comment = True
                        elif character == "}":
                            in_comment = False
            if lines:
                yield "".join(lines)


def write_parquet(path, rows, schema):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    count = 0
    try:
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
            for batch in rows:
                if batch:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    count += len(batch)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(path=str(path), sha256=sha256_file(path), rows=count, bytes=path.stat().st_size)


def verify_file(entry, schema=None):
    path = Path(entry["path"])
    if not path.is_file() or sha256_file(path) != entry["sha256"]:
        raise ValueError(f"shard checksum mismatch: {path.name}")
    metadata = pq.ParquetFile(path)
    if metadata.metadata.num_rows != entry["rows"]:
        raise ValueError(f"shard row count mismatch: {path.name}")
    if schema is not None and not metadata.schema_arrow.equals(schema, check_metadata=False):
        raise ValueError(f"shard schema mismatch: {path.name}")
    return path


def prepare(archive, settings, interrupt_after_shards=None):
    """Resume by rescanning zstd and reusing committed, verified framed shards.

    The source is not seekable. This avoids repeating downstream parsing/loading,
    but does not claim to avoid decompression up to the last committed record.
    """
    signature = hashlib.sha256(json.dumps(dict(version=VERSION,
        source=settings.source.sha256, target=settings.frame_target_bytes,
        maximum=settings.max_record_bytes), sort_keys=True).encode()).hexdigest()[:24]
    root = settings.data_root / "framed" / signature
    manifest_path = root / "manifest.json"
    with file_lock(root / ".lock"):
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else dict(
            version=VERSION, source_sha256=settings.source.sha256, complete=False, shards=[])
        for entry in manifest["shards"]:
            verify_file(entry, FRAME_SCHEMA)
        if manifest["complete"]:
            if sum(entry["rows"] for entry in manifest["shards"]) != settings.source.expected_games:
                raise ValueError("cached frame count differs from source manifest")
            return manifest
        skipped = sum(entry["rows"] for entry in manifest["shards"])
        rows, buffered, seen = [], 0, 0

        def commit():
            nonlocal rows, buffered
            check_disk(root, settings.min_free_disk_bytes, buffered)
            index = len(manifest["shards"])
            entry = write_parquet(root / f"part-{index:06d}.parquet", [rows], FRAME_SCHEMA)
            entry.update(shard_id=index, first_record=rows[0]["record_number"], last_record=rows[-1]["record_number"])
            manifest["shards"].append(entry)
            atomic_json(manifest_path, manifest)
            rows, buffered = [], 0
            if interrupt_after_shards is not None and len(manifest["shards"]) >= interrupt_after_shards:
                raise InterruptedError("injected framing interruption")

        for number, pgn in enumerate(iter_games(archive, settings.max_record_bytes), 1):
            seen = number
            if number <= skipped:
                continue
            size = len(pgn.encode("utf-8"))
            if rows and buffered + size > settings.frame_target_bytes:
                commit()
            rows.append(dict(record_number=number, pgn=pgn))
            buffered += size
        if rows:
            commit()
        if seen != settings.source.expected_games:
            raise ValueError(f"expected {settings.source.expected_games} games, framed {seen}")
        manifest["complete"] = True
        atomic_json(manifest_path, manifest)
        return manifest
