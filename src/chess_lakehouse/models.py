"""PGN validation and deterministic dimension/fact records."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

import chess.pgn


def key(value: str) -> str:
    """Derive a stable natural-key digest from a normalized source value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def optional(value: str | None) -> str | None:
    """Normalize an absent PGN header to the model's optional-value representation."""
    return None if value in (None, "", "?", "-") else value


def rating(value: str | None) -> int | None:
    """Parse a bounded chess rating and reject unsupported source values."""
    value = optional(value)
    if value is None:
        return None
    number = int(value)
    if not 0 <= number <= 4000:
        raise ValueError("rating outside 0..4000")
    return number


def band(value: int | None) -> str | None:
    """Map an optional rating to its deterministic analytical rating band."""
    return f"{value // 200 * 200}-{value // 200 * 200 + 199}" if value is not None else None


def time_control(value: str) -> dict:
    """Parse a PGN time control into clock components and a speed category."""
    if value in ("-", "?", ""):
        return dict(time_control_key="unknown", initial_seconds=None,
                    increment_seconds=None, speed="unknown")
    if not re.fullmatch(r"\d+\+\d+", value):
        raise ValueError("invalid time control")
    initial, increment = map(int, value.split("+"))
    duration = initial + 40 * increment
    speed = ("ultrabullet" if duration < 30 else "bullet" if duration < 180
             else "blitz" if duration < 480 else "rapid" if duration < 1500 else "classical")
    return dict(time_control_key=f"{initial}+{increment}", initial_seconds=initial,
                increment_seconds=increment, speed=speed)


@dataclass
class ParsedGame:
    """Only headers, mainline ply count and parser errors are retained per game."""
    headers: dict[str, str]
    ply_count: int
    errors: list[str]


class RecordVisitor(chess.pgn.BaseVisitor):
    """Validate moves with python-chess without allocating a full game tree."""
    def begin_game(self):
        self.headers = {}
        self.plies = 0
        self.errors = []

    def visit_header(self, tagname, tagvalue):
        self.headers[tagname] = tagvalue

    def visit_move(self, board, move):
        self.plies += 1

    def begin_variation(self):
        return chess.pgn.SKIP

    def handle_error(self, error):
        # Keep diagnostics bounded and avoid logging PGN/player names.
        if not self.errors:
            self.errors.append(type(error).__name__)

    def result(self):
        return ParsedGame(self.headers, self.plies, self.errors)


def transform(game: ParsedGame, source_month: str, ingested_at: str) -> dict:
    """Convert one parsed game into shared typed dimensional and fact records."""
    h = game.headers
    if game.errors:
        raise ValueError("PGN parse error: " + ",".join(sorted(set(game.errors))))
    if h.get("Variant", "Standard") != "Standard":
        raise ValueError("unsupported chess variant")
    site = h.get("Site", "")
    match = re.fullmatch(r"https?://lichess\.org/([A-Za-z0-9]{8})", site)
    if not match:
        raise ValueError("missing or invalid Lichess game ID")
    result = h.get("Result")
    if result not in ("1-0", "0-1", "1/2-1/2"):
        raise ValueError("missing or unfinished result")
    date = datetime.strptime(h.get("UTCDate", h.get("Date", "")), "%Y.%m.%d").date()
    # Archives may contain games started in the previous month. Preserve UTCDate.
    players = []
    for color in ("White", "Black"):
        name = optional(h.get(color))
        if name is None:
            raise ValueError("missing player")
        players.append(dict(player_key=key(name.casefold()), latest_title=optional(h.get(color + "Title")),
                            last_observed_date=date.isoformat()))
    white, black = rating(h.get("WhiteElo")), rating(h.get("BlackElo"))
    control = time_control(h.get("TimeControl", "-"))
    eco = optional(h.get("ECO"))
    if eco is not None and not re.fullmatch(r"[A-E][0-9]{2}", eco):
        raise ValueError("invalid ECO code")
    opening = optional(h.get("Opening"))
    family, _, variation = (opening or "").partition(": ")
    opening_key = key((eco or "") + "|" + (opening or ""))
    fact = dict(game_id=match[1], date_key=date.isoformat(), white_player_key=players[0]["player_key"],
                black_player_key=players[1]["player_key"], opening_key=opening_key,
                time_control_key=control["time_control_key"], game_month=date.strftime("%Y-%m-01"),
                source_month=source_month, white_rating=white, black_rating=black,
                average_rating=(white + black) / 2 if white is not None and black is not None else None,
                rating_difference=white - black if white is not None and black is not None else None,
                white_rating_band=band(white), black_rating_band=band(black), result=result,
                winner_color={"1-0": "white", "0-1": "black", "1/2-1/2": "draw"}[result],
                ply_count=game.ply_count, move_count=(game.ply_count + 1) // 2,
                termination=optional(h.get("Termination")), speed=control["speed"], ingested_at=ingested_at)
    return dict(dim_date=[dict(date_key=date.isoformat(), year=date.year, month=date.month,
                              quarter=(date.month - 1) // 3 + 1, weekday=date.weekday())],
                dim_player=players,
                dim_opening=[dict(opening_key=opening_key, eco=eco, family=family or None,
                                  variation=variation or None)], dim_time_control=[control], fact_game=[fact])
