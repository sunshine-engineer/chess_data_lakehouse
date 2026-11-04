"""Bounded framing is shared by both container engines."""
import io
import json
from pathlib import Path

import chess.pgn
import pytest
import zstandard as zstd

from chess_lakehouse.framing import frame_archive, parse_frame
from chess_lakehouse.models import RecordVisitor

FIXTURE = Path(__file__).parent / 'fixtures' / 'games.pgn'


@pytest.mark.parametrize('size', [1, 2, 10])
def test_framing_and_validation_agree_with_original_parser(tmp_path, size):
    archive = tmp_path / 'games.zst'
    archive.write_bytes(zstd.ZstdCompressor().compress(FIXTURE.read_bytes()))
    paths, count = frame_archive(archive, tmp_path / 'frames', size)
    assert count == 5
    records = []
    for path in paths:
        chunk = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(chunk) <= size
        records.extend(chunk)
    assert [r['record_number'] for r in records] == [1, 2, 3, 4, 5]
    transformed = [parse_frame(r, '2013-01', '2026-11-04T00:00:00+00:00') for r in records]
    assert sum(r[3] is not None for r in transformed) == 2
    assert transformed[0][1] == transformed[3][1] == 'test0001'


def test_event_inside_brace_comment_does_not_split_game(tmp_path):
    source = FIXTURE.read_text().split('[Event "Synthetic fixture"]')[1]
    # A line that looks like a header inside a multiline PGN comment.
    pgn = '[Event "Synthetic fixture"]' + source
    pgn = pgn.replace('1. e4', '1. { comment\n[Event "not a game"]\n} e4', 1)
    archive = tmp_path / 'games.zst'
    archive.write_bytes(zstd.ZstdCompressor().compress(pgn.encode()))
    paths, count = frame_archive(archive, tmp_path / 'frames', 2)
    assert count == 1
    assert len(paths) == 1
