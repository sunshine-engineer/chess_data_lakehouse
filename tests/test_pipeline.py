"""Offline integration tests: real compressed PGN, database, and Parquet."""
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import chess.pgn
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import zstandard as zstd

from chess_lakehouse.models import RecordVisitor, transform, time_control
from chess_lakehouse.pipeline import download, main, process_archive, sha256_file, validate_config

FIXTURE = Path(__file__).parent / "fixtures" / "games.pgn"


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "games.pgn.zst"
    path.write_bytes(zstd.ZstdCompressor().compress(FIXTURE.read_bytes()))
    return path


@pytest.mark.parametrize("size", [1, 2, 20])
def test_end_to_end_and_rerun(archive, tmp_path, size):
    output = tmp_path / "lake"
    for _ in range(2):
        report = process_archive(archive, output, "2013-01", size)
        assert (report["input_games"], report["accepted_games"], report["rejected_games"],
                report["duplicate_games"]) == (5, 2, 2, 1)
        run = output / "runs" / json.loads((output / "CURRENT.json").read_text())["run_id"]
        with sqlite3.connect(run / "warehouse.sqlite") as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("SELECT count(*) FROM fact_game").fetchone()[0] == 2
            assert conn.execute("SELECT date_key FROM fact_game WHERE game_id='test0001'").fetchone()[0] == "2012-12-31"
            assert conn.execute("SELECT latest_title FROM dim_player WHERE latest_title IS NOT NULL").fetchone()[0] == "FM"
        for table, expected in report["tables"].items():
            folder = run / "parquet" / table
            data = pq.read_table(folder)
            assert data.num_rows == expected
            if table == "fact_game":
                assert data.column("black_rating").type == pa.int64()
        assert "Alice" not in (run / "warehouse.sqlite").read_bytes().decode("utf-8", errors="ignore")


def test_failed_run_preserves_current(archive, tmp_path):
    output = tmp_path / "lake"
    process_archive(archive, output, "2013-01", 2)
    previous = (output / "CURRENT.json").read_bytes()
    with patch("chess_lakehouse.pipeline.export_tables", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            process_archive(archive, output, "2013-01", 2)
    assert (output / "CURRENT.json").read_bytes() == previous
    assert len(list((output / "runs").iterdir())) == 1


@pytest.mark.parametrize("month,enabled,size", [("all", False, 2), ("2013-01,2013-02", False, 2),
    ("2013-02", False, 2), ("2013-01", True, 2), ("2013-01", False, 0)])
def test_guard(month, enabled, size):
    with pytest.raises(ValueError):
        validate_config(dict(test_month="2013-01", all_months_enabled=enabled,
                             archive_sha256="a" * 64), month, size)


@pytest.mark.parametrize("change", [{"UTCDate": "2013.02.30"}, {"TimeControl": "oops"},
    {"Result": "*"}, {"White": "?"}, {"Site": "https://evil.example/abc"}, {"ECO": "Z90"}])
def test_invalid_records(change):
    with FIXTURE.open() as stream:
        game = chess.pgn.read_game(stream, Visitor=RecordVisitor)
    game.headers.update(change)
    with pytest.raises(ValueError):
        transform(game, "2013-01", "test")


@pytest.mark.parametrize("control,speed", [("15+0", "ultrabullet"), ("60+0", "bullet"),
    ("180+0", "blitz"), ("600+0", "rapid"), ("1800+0", "classical"), ("-", "unknown")])
def test_speed(control, speed):
    assert time_control(control)["speed"] == speed


def test_download_integrity_and_cache(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"archive bytes")
    target = tmp_path / "bronze" / "archive.zst"
    expected = sha256_file(source)
    download(source.as_uri(), target, expected, 100)
    assert target.read_bytes() == source.read_bytes()
    with patch("chess_lakehouse.pipeline.urlopen", side_effect=AssertionError("network called")):
        download(source.as_uri(), target, expected, 100)
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="cached archive checksum"):
        download(source.as_uri(), target, expected, 100)


@pytest.mark.parametrize("limit,checksum", [(1, "a" * 64), (100, "a" * 64)])
def test_failed_download_not_published(tmp_path, limit, checksum):
    source = tmp_path / "source"
    source.write_bytes(b"archive bytes")
    target = tmp_path / "target.zst"
    with patch("chess_lakehouse.pipeline.time.sleep"):
        with pytest.raises(ValueError):
            download(source.as_uri(), target, checksum, limit)
    assert not target.exists()
    assert not target.with_suffix(".zst.part").exists()


def test_cli_checksum_and_guard_before_network(archive, tmp_path):
    output = tmp_path / "output"
    with patch("chess_lakehouse.pipeline.urlopen", side_effect=AssertionError("network called")):
        with pytest.raises(SystemExit):
            main(["--month", "2013-02", "--output", str(output)])
        assert not output.exists()
        with pytest.raises(SystemExit):
            main(["--archive", str(archive), "--output", str(output)])
        assert not (output / "CURRENT.json").exists()
        assert not (output / ".pipeline.lock").exists()


def test_empty_archive_does_not_publish(tmp_path):
    path = tmp_path / "empty.zst"
    path.write_bytes(zstd.ZstdCompressor().compress(b""))
    with pytest.raises(ValueError, match="no accepted"):
        process_archive(path, tmp_path / "out", "2013-01", 2)
    assert not (tmp_path / "out" / "CURRENT.json").exists()


def test_cli_lock_prevents_concurrent_run(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    lock = output / ".pipeline.lock"
    lock.write_text("existing writer")
    with patch("chess_lakehouse.pipeline.download", side_effect=AssertionError("download called")):
        with pytest.raises(SystemExit):
            main(["--output", str(output)])
    assert lock.read_text() == "existing writer"


def test_cli_offline_success(archive, tmp_path):
    config = tmp_path / "pipeline.toml"
    config.write_text('test_month = "2013-01"\nall_months_enabled = false\nchunk_size = 2\n'
                      f'archive_sha256 = "{sha256_file(archive)}"\n')
    output = tmp_path / "out"
    with patch("chess_lakehouse.pipeline.download", side_effect=AssertionError("network called")):
        main(["--config", str(config), "--archive", str(archive), "--output", str(output)])
    assert (output / "CURRENT.json").exists()
    assert not (output / ".pipeline.lock").exists()


def test_nullable_integer_schema(tmp_path):
    pgn = FIXTURE.read_text().split('[Event "Synthetic fixture invalid rating"]')[0]
    # Remove all known ratings: even entirely-null columns must remain int64.
    import re
    pgn = re.sub(r'\[(White|Black)Elo "[^"]*"\]', r'[\1Elo "?"]', pgn)
    archive = tmp_path / "nulls.zst"
    archive.write_bytes(zstd.ZstdCompressor().compress(pgn.encode()))
    report = process_archive(archive, tmp_path / "lake", "2013-01", 1)
    table = pq.read_table(tmp_path / "lake" / "runs" / report["run_id"] / "parquet" / "fact_game")
    assert table.schema.field("white_rating").type == pa.int64()
    assert table.column("white_rating").null_count == table.num_rows


def test_expected_monthly_count(archive, tmp_path):
    with pytest.raises(ValueError, match="expected 100 games, found 5"):
        process_archive(archive, tmp_path / "lake", "2013-01", 2, expected_games=100)
    assert not (tmp_path / "lake" / "CURRENT.json").exists()
