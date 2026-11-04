"""Durable shard validation and bounded source framing, executed inside Docker."""
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import zstandard as zstd

from chess_lakehouse.pipeline import sha256_file
from chess_lakehouse.optimized.settings import Settings, Source, load
from chess_lakehouse.optimized.storage import prepare, iter_games
from chess_lakehouse.optimized.records import parse_base, enrich, UNKNOWN_HASH

FIXTURE = Path(__file__).parent / 'fixtures' / 'games.pgn'


def source_settings(tmp_path, pgn=None, month='2013-01', expected=5):
    archive = tmp_path / f'{month}.zst'
    archive.write_bytes(zstd.ZstdCompressor().compress(pgn or FIXTURE.read_bytes()))
    settings = Settings(Source(month, sha256_file(archive), expected, 1000000, 'fixture'),
        data_root=tmp_path / 'data', frame_target_bytes=600, max_record_bytes=8192,
        parse_batch_rows=2, copy_batch_rows=2, min_free_disk_bytes=0,
        max_reject_ratio=0.5, max_unknown_player_ratio=1)
    return archive, settings


def test_partial_framing_resumes_without_rewriting_committed_shard(tmp_path):
    archive, settings = source_settings(tmp_path)
    with pytest.raises(InterruptedError):
        prepare(archive, settings, interrupt_after_shards=1)
    shard = next((settings.data_root / 'framed').glob('*/part-000000.parquet'))
    before = (shard.stat().st_mtime_ns, sha256_file(shard))
    manifest = prepare(archive, settings)
    assert manifest['complete'] and sum(s['rows'] for s in manifest['shards']) == 5
    assert before == (shard.stat().st_mtime_ns, sha256_file(shard))
    assert len(manifest['shards']) > 1
    shard.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        prepare(archive, settings)


def test_single_game_byte_limit_and_expected_volume(tmp_path):
    archive, settings = source_settings(tmp_path)
    with pytest.raises(ValueError, match='byte limit'):
        list(iter_games(archive, 50))
    with pytest.raises(ValueError, match='expected 6'):
        prepare(archive, replace(settings, source=replace(settings.source, expected_games=6)))


def test_unknown_player_is_retained_with_explicit_identity(tmp_path):
    archive, settings = source_settings(tmp_path)
    first = next(iter_games(archive, 8192)).replace('[White "Alice"]', '[White "?"]')
    row = enrich(parse_base(dict(record_number=1, pgn=first), '2013-01'))
    assert row['reason'] is None and row['white_unknown']
    assert row['white_player_hash'] == UNKNOWN_HASH
    assert row['move_count'] == 2 and row['speed'] == 'blitz'
    assert parse_base(dict(record_number=1, pgn=first), '2013-01', 'reject')['reason'] == 'missing player'


def test_default_month_controls_remain_closed():
    with pytest.raises(ValueError, match='not explicitly allowlisted'):
        load(month='2013-02')
    with pytest.raises(ValueError):
        load(month='all')
    assert load().source.month == '2013-01'
