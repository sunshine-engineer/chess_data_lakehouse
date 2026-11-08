"""Typed source observations shared by serial, multiprocessing and Spark parsers."""
from __future__ import annotations

from datetime import date
import io
import math
import re

import chess.pgn
import pyarrow as pa

from ..models import RecordVisitor, key, optional, transform

UNKNOWN_NAME = "__chess_unknown_player__"
UNKNOWN_HASH = bytes.fromhex(key(UNKNOWN_NAME))
FIELDS = [
    ("record_number", pa.int64()), ("game_id", pa.string()), ("game_date", pa.date32()),
    ("white_player_hash", pa.binary()), ("black_player_hash", pa.binary()),
    ("white_title", pa.string()), ("black_title", pa.string()),
    ("white_unknown", pa.bool_()), ("black_unknown", pa.bool_()),
    ("opening_hash", pa.binary()), ("eco", pa.string()), ("opening_family", pa.string()),
    ("opening_variation", pa.string()), ("time_control_code", pa.string()),
    ("initial_seconds", pa.int64()), ("increment_seconds", pa.int64()),
    ("white_rating", pa.int64()), ("black_rating", pa.int64()), ("result", pa.string()),
    ("ply_count", pa.int64()), ("termination", pa.string()), ("reason", pa.string()),
]
DERIVED = [
    ("average_rating", pa.float64()), ("rating_difference", pa.int64()),
    ("white_rating_band", pa.string()), ("black_rating_band", pa.string()),
    ("winner_color", pa.string()), ("move_count", pa.int64()), ("speed", pa.string()),
]
BASE_SCHEMA = pa.schema(FIELDS)
SCHEMA = pa.schema(FIELDS + DERIVED)
COLUMNS = SCHEMA.names


def parse_base(frame, month, missing_player_policy="unknown"):
    """Parse a framed PGN into the shared base record or a counted rejection."""
    row = dict.fromkeys(BASE_SCHEMA.names)
    row["record_number"] = int(frame["record_number"])
    try:
        game = chess.pgn.read_game(io.StringIO(frame["pgn"]), Visitor=RecordVisitor)
        if game is None:
            raise ValueError("empty PGN frame")
        match = re.fullmatch(r"https?://lichess\.org/([A-Za-z0-9]{8})", game.headers.get("Site", ""))
        row["game_id"] = match[1] if match else None
        for color in ("White", "Black"):
            unknown = optional(game.headers.get(color)) is None
            row[color.lower() + "_unknown"] = unknown
            if unknown and missing_player_policy == "unknown":
                game.headers[color] = UNKNOWN_NAME
        models = transform(game, month, "2000-01-01T00:00:00+00:00")
        fact = models["fact_game"][0]
        opening = models["dim_opening"][0]
        control = models["dim_time_control"][0]
        row.update(game_id=fact["game_id"], game_date=date.fromisoformat(fact["date_key"]),
                   white_player_hash=bytes.fromhex(fact["white_player_key"]),
                   black_player_hash=bytes.fromhex(fact["black_player_key"]),
                   white_title=models["dim_player"][0]["latest_title"],
                   black_title=models["dim_player"][1]["latest_title"],
                   opening_hash=bytes.fromhex(fact["opening_key"]), eco=opening["eco"],
                   opening_family=opening["family"], opening_variation=opening["variation"],
                   time_control_code=control["time_control_key"], initial_seconds=control["initial_seconds"],
                   increment_seconds=control["increment_seconds"], white_rating=fact["white_rating"],
                   black_rating=fact["black_rating"], result=fact["result"], ply_count=fact["ply_count"],
                   termination=fact["termination"])
    except (ValueError, OverflowError) as error:
        row["reason"] = str(error)
    return row


def enrich(row):
    """Derive normalized warehouse dimensions and fact attributes from a base game record."""
    row = {**row, **dict.fromkeys(name for name, _ in DERIVED)}
    if row["reason"] is not None:
        return row
    white, black = row["white_rating"], row["black_rating"]
    if white is not None and black is not None:
        row["average_rating"] = (white + black) / 2
        row["rating_difference"] = white - black
    for color in ("white", "black"):
        rating = row[color + "_rating"]
        if rating is not None:
            lower = math.floor(rating / 200) * 200
            row[color + "_rating_band"] = f"{lower}-{lower + 199}"
    row["winner_color"] = {"1-0": "white", "0-1": "black", "1/2-1/2": "draw"}[row["result"]]
    row["move_count"] = (row["ply_count"] + 1) // 2
    if row["initial_seconds"] is None:
        row["speed"] = "unknown"
    else:
        duration = row["initial_seconds"] + 40 * row["increment_seconds"]
        row["speed"] = ("ultrabullet" if duration < 30 else "bullet" if duration < 180 else
                        "blitz" if duration < 480 else "rapid" if duration < 1500 else "classical")
    return row


def spark_enrich(df):
    """Native Catalyst expressions; move legality and Unicode hashing stay shared."""
    from pyspark.sql import functions as f
    valid = f.col("reason").isNull()
    both_ratings = f.col("white_rating").isNotNull() & f.col("black_rating").isNotNull()
    df = df.withColumn("average_rating", f.when(valid & both_ratings, (f.col("white_rating") + f.col("black_rating")) / 2))
    df = df.withColumn("rating_difference", f.when(valid & both_ratings, f.col("white_rating") - f.col("black_rating")))
    for color in ("white", "black"):
        lower = (f.floor(f.col(color + "_rating") / 200) * 200).cast("long")
        df = df.withColumn(color + "_rating_band", f.when(valid & f.col(color + "_rating").isNotNull(),
                                f.concat(lower.cast("string"), f.lit("-"), (lower + 199).cast("string"))))
    df = df.withColumn("winner_color", f.when(valid,
            f.when(f.col("result") == "1-0", "white").when(f.col("result") == "0-1", "black").otherwise("draw")))
    df = df.withColumn("move_count", f.when(valid, f.ceil(f.col("ply_count") / 2).cast("long")))
    duration = f.col("initial_seconds") + 40 * f.col("increment_seconds")
    df = df.withColumn("speed", f.when(valid,
         f.when(f.col("initial_seconds").isNull(), "unknown").when(duration < 30, "ultrabullet")
          .when(duration < 180, "bullet").when(duration < 480, "blitz")
          .when(duration < 1500, "rapid").otherwise("classical")))
    return df.select(*COLUMNS)


def spark_schema(arrow_schema=BASE_SCHEMA):
    """Return the Spark schema matching the shared typed record contract."""
    from pyspark.sql import types as t
    types = {pa.int64(): t.LongType(), pa.string(): t.StringType(), pa.binary(): t.BinaryType(),
             pa.bool_(): t.BooleanType(), pa.date32(): t.DateType(), pa.float64(): t.DoubleType()}
    return t.StructType([t.StructField(field.name, types[field.type], True) for field in arrow_schema])
