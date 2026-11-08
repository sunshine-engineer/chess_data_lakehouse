"""Stream a Lichess standard archive into bounded JSONL files for both engines."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import chess.pgn
import zstandard as zstd

from .models import RecordVisitor, transform


def frame_archive(archive: Path, directory: Path, chunk_size: int) -> tuple[list[Path], int]:
    """Lichess exports start each game with Event; comments cannot start a game.

    Framing is source-specific. It does not claim to support arbitrary PGN layouts.
    The archive checksum and expected frame count protect this input contract.
    """
    directory.mkdir(parents=True, exist_ok=False)
    paths = []
    count = 0
    lines = []
    in_comment = False
    output = None

    def emit():
        nonlocal count, output
        if count % chunk_size == 0:
            if output:
                output.close()
            path = directory / f"part-{len(paths):05d}.jsonl"
            paths.append(path)
            output = path.open("w", encoding="utf-8")
        count += 1
        output.write(json.dumps(dict(record_number=count, pgn="".join(lines))) + "\n")

    try:
        with archive.open("rb") as raw, zstd.ZstdDecompressor().stream_reader(raw) as reader:
            with io.TextIOWrapper(reader, encoding="utf-8", errors="strict") as stream:
                for line in stream:
                    if line.startswith('[Event "') and not in_comment:
                        if lines:
                            emit()
                        lines = [line]
                    elif lines:
                        lines.append(line)
                    elif line.strip():
                        raise ValueError("unexpected data before first Lichess Event header")
                    # Header strings can contain braces, so inspect movetext only.
                    if not line.startswith("["):
                        for char in line.split(";", 1)[0]:
                            if char == "{":
                                in_comment = True
                            elif char == "}":
                                in_comment = False
                if lines:
                    emit()
    finally:
        if output:
            output.close()
    return paths, count


def parse_frame(record: dict, month: str, stamp: str) -> tuple:
    """Shared Python semantics keep engine output comparable."""
    game = chess.pgn.read_game(io.StringIO(record["pgn"]), Visitor=RecordVisitor)
    game_id = None
    try:
        if game is None:
            raise ValueError("empty PGN frame")
        match = re.fullmatch(r"https?://lichess\.org/([A-Za-z0-9]{8})", game.headers.get("Site", ""))
        game_id = match[1] if match else None
        models = transform(game, month, stamp)
        return record["record_number"], game_id, json.dumps(models), None
    except (ValueError, OverflowError) as error:
        return record["record_number"], game_id, None, str(error)


def python_records(paths, month, stamp):
    """Yield bounded Python-side PGN records for the baseline ingestion path."""
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                yield parse_frame(json.loads(line), month, stamp)
