"""Download, stream-decompress, validate, chunk-load, and publish one month."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import tomllib
from datetime import datetime, timezone
from urllib.request import urlopen
from uuid import uuid4

import chess.pgn
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard as zstd

from .models import RecordVisitor, transform

LOG = logging.getLogger(__name__)
TABLES = ("dim_date", "dim_player", "dim_opening", "dim_time_control", "fact_game", "rejected_game")
BASE_URL = "https://database.lichess.org/standard"


def validate_config(config: dict, month: str, chunk_size: int) -> None:
    """Fail closed before network access or output changes."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must be a single YYYY-MM value")
    if config.get("all_months_enabled") is not False:
        raise ValueError("all-month processing must remain disabled")
    if month != config["test_month"]:
        raise ValueError(f"only test month {config['test_month']} is enabled")
    if not 1 <= chunk_size <= 100000:
        raise ValueError("chunk size must be between 1 and 100000")
    if not re.fullmatch(r"[a-f0-9]{64}", config["archive_sha256"]):
        raise ValueError("a trusted archive SHA256 is required")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, expected_sha256: str, max_bytes: int) -> Path:
    """Retry whole downloads; partial files never become trusted archives."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_file(target) != expected_sha256:
            raise ValueError(f"cached archive checksum mismatch: {target}")
        return target
    partial = target.with_suffix(target.suffix + ".part")
    for attempt in range(3):
        try:
            total = 0
            with urlopen(url, timeout=60) as response, partial.open("wb") as output:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise ValueError("archive exceeds download limit")
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > max_bytes:
                        raise ValueError("archive exceeds download limit")
                    output.write(block)
                if length and total != int(length):
                    raise ValueError("incomplete HTTP download")
            if sha256_file(partial) != expected_sha256:
                raise ValueError("download checksum mismatch")
            os.replace(partial, target)
            return target
        except Exception:
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    columns = ",".join(row)
    values = ",".join("?" for _ in row)
    suffix = " ON CONFLICT DO NOTHING"
    if table == "dim_player":
        suffix = (" ON CONFLICT(player_key) DO UPDATE SET latest_title=excluded.latest_title, "
                  "last_observed_date=excluded.last_observed_date "
                  "WHERE excluded.last_observed_date >= dim_player.last_observed_date")
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({values}){suffix}", tuple(row.values()))


def load_chunk(conn: sqlite3.Connection, chunk: list, month: str, stamp: str) -> tuple[int, int, int]:
    accepted = rejected = duplicates = 0
    with conn:
        for number, game in chunk:
            try:
                records = transform(game, month, stamp)
            except (ValueError, OverflowError) as error:
                site = game.headers.get("Site", "")
                match = re.fullmatch(r"https?://lichess\.org/([A-Za-z0-9]{8})", site)
                insert(conn, "rejected_game", dict(record_number=number, source_month=month,
                       game_id=match[1] if match else None, failure_stage="parse_or_validate",
                       reason=str(error), ingested_at=stamp))
                rejected += 1
                continue
            game_id = records["fact_game"][0]["game_id"]
            if conn.execute("SELECT 1 FROM fact_game WHERE game_id=?", (game_id,)).fetchone():
                duplicates += 1
                continue
            for table, rows in records.items():
                for row in rows:
                    insert(conn, table, row)
            accepted += 1
    return accepted, rejected, duplicates


def export_tables(conn: sqlite3.Connection, run_dir: Path, month: str, chunk_size: int) -> dict:
    """Fixed schemas preserve nullable integer columns, including empty tables."""
    counts = {}
    for table in TABLES:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        types = {"TEXT": pa.string(), "INTEGER": pa.int64(), "REAL": pa.float64()}
        schema = pa.schema([pa.field(col[1], types[col[2]]) for col in info])
        directory = run_dir / "parquet" / table / f"source_month={month}"
        directory.mkdir(parents=True)
        # The partition directory already carries source_month. Keeping it out of
        # files avoids type conflicts with Hive-partition readers.
        file_schema = pa.schema([f for f in schema if f.name != "source_month"])
        cursor = conn.execute(f"SELECT * FROM {table}")
        count = 0
        with pq.ParquetWriter(directory / "part-00000.parquet", file_schema, compression="zstd") as writer:
            while rows := cursor.fetchmany(chunk_size):
                records = [dict(zip(schema.names, row)) for row in rows]
                writer.write_table(pa.Table.from_pylist(records, schema=file_schema))
                count += len(rows)
        counts[table] = count
    return counts


def process_archive(archive: Path, output: Path, month: str, chunk_size: int,
                    source_url: str | None = None, expected_games: int | None = None) -> dict:
    """Build an isolated snapshot; publish its pointer only after all checks pass."""
    output.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    run_dir = output / "runs" / run_id
    run_dir.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).isoformat()
    stats = dict(input_games=0, accepted_games=0, rejected_games=0, duplicate_games=0, chunks=0)
    started = time.monotonic()
    conn = sqlite3.connect(run_dir / "warehouse.sqlite")
    try:
        conn.executescript(Path(__file__).with_name("schema.sql").read_text())
        with archive.open("rb") as compressed:
            with zstd.ZstdDecompressor().stream_reader(compressed) as reader:
                with io.TextIOWrapper(reader, encoding="utf-8", errors="strict") as stream:
                    chunk = []
                    while True:
                        game = chess.pgn.read_game(stream, Visitor=RecordVisitor)
                        if game is None:
                            break
                        stats["input_games"] += 1
                        chunk.append((stats["input_games"], game))
                        if len(chunk) == chunk_size:
                            _flush(conn, chunk, month, stamp, stats)
                            chunk.clear()
                    if chunk:
                        _flush(conn, chunk, month, stamp, stats)
        if expected_games is not None and stats["input_games"] != expected_games:
            raise ValueError(f"expected {expected_games} games, found {stats['input_games']}")
        if not stats["input_games"] or not stats["accepted_games"]:
            raise ValueError("archive contains no accepted games")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("foreign key validation failed")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        if stats["input_games"] != sum(stats[k] for k in ("accepted_games", "rejected_games", "duplicate_games")):
            raise ValueError("input reconciliation failed")
        counts = export_tables(conn, run_dir, month, chunk_size)
        if counts["fact_game"] != stats["accepted_games"] or counts["rejected_game"] != stats["rejected_games"]:
            raise ValueError("export reconciliation failed")
        report = dict(status="complete", schema_version=1, run_id=run_id, source_month=month,
                      source_url=source_url, archive_sha256=sha256_file(archive),
                      chunk_size=chunk_size, elapsed_seconds=round(time.monotonic() - started, 2),
                      all_months_enabled=False, tables=counts, **stats)
        (run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        conn.close()
        pointer = output / f".CURRENT-{run_id}.json"
        pointer.write_text(json.dumps(dict(run_id=run_id, source_month=month)) + "\n")
        os.replace(pointer, output / "CURRENT.json")
        return report
    except BaseException:
        conn.close()
        shutil.rmtree(run_dir)
        raise


def _flush(conn, chunk, month, stamp, stats):
    accepted, rejected, duplicates = load_chunk(conn, chunk, month, stamp)
    for name, count in zip(("accepted_games", "rejected_games", "duplicate_games"),
                           (accepted, rejected, duplicates)):
        stats[name] += count
    stats["chunks"] += 1
    LOG.info("chunk=%s input=%s accepted=%s rejected=%s duplicates=%s", stats["chunks"],
             stats["input_games"], stats["accepted_games"], stats["rejected_games"], stats["duplicate_games"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/pipeline.toml"))
    parser.add_argument("--month", help="One YYYY-MM; must match the configured test month")
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--archive", type=Path, help="Existing .pgn.zst; still checksum-verified")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with args.config.open("rb") as stream:
            config = tomllib.load(stream)
        month = args.month if args.month is not None else config["test_month"]
        chunk_size = args.chunk_size if args.chunk_size is not None else config["chunk_size"]
        validate_config(config, month, chunk_size)
        output = args.output or Path(config["output_dir"])
        # Prevent concurrent writers; a stale lock after SIGKILL requires manual removal.
        output.mkdir(parents=True, exist_ok=True)
        lock = output / ".pipeline.lock"
        with lock.open("x") as handle:
            handle.write(str(os.getpid()))
        try:
            filename = f"lichess_db_standard_rated_{month}.pgn.zst"
            url = f"{BASE_URL}/{filename}"
            archive = args.archive or download(url, output / "bronze" / filename,
                                               config["archive_sha256"], config["max_download_bytes"])
            if sha256_file(archive) != config["archive_sha256"]:
                raise ValueError("archive checksum mismatch")
            report = process_archive(archive, output, month, chunk_size, url, config.get("expected_games"))
            print(json.dumps(report, indent=2))
        finally:
            lock.unlink(missing_ok=True)
    except (OSError, ValueError, sqlite3.Error, zstd.ZstdError) as error:
        parser.exit(1, f"Pipeline failed: {error}\n")


if __name__ == "__main__":
    main()
