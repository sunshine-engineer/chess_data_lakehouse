"""Validated independent capacity controls and explicitly allowlisted sources."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib

VERSION = "2"
ENGINES = ("python", "python_mp", "spark")


@dataclass(frozen=True)
class Source:
    month: str
    sha256: str
    expected_games: int
    max_download_bytes: int
    url: str


@dataclass(frozen=True)
class Settings:
    source: Source
    data_root: Path = Path("/data")
    frame_target_bytes: int = 64 * 1024 * 1024
    max_record_bytes: int = 8 * 1024 * 1024
    copy_batch_rows: int = 5000
    parse_batch_rows: int = 1024
    python_workers: int = 2
    spark_partitions: int = 2
    spark_master: str = "local[2]"
    spark_driver_memory: str = "1g"
    db_writers: int = 2
    max_reject_ratio: float = 0.01
    max_unknown_player_ratio: float = 0.01
    min_free_disk_bytes: int = 1024 * 1024 * 1024
    statement_timeout_seconds: int = 1800
    lock_timeout_seconds: int = 30
    missing_player_policy: str = "unknown"

    def __post_init__(self):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", self.source.month):
            raise ValueError("one explicit YYYY-MM month is required")
        if not re.fullmatch(r"[a-f0-9]{64}", self.source.sha256):
            raise ValueError("a trusted source checksum is required")
        if self.source.expected_games < 1 or self.source.max_download_bytes < 1:
            raise ValueError("source volume limits must be positive")
        for field in ("frame_target_bytes", "max_record_bytes", "copy_batch_rows", "parse_batch_rows",
                      "python_workers", "spark_partitions", "db_writers", "statement_timeout_seconds",
                      "lock_timeout_seconds"):
            if getattr(self, field) < 1:
                raise ValueError(f"{field} must be positive")
        if self.copy_batch_rows > 100000 or self.parse_batch_rows > 100000:
            raise ValueError("row batch limits must not exceed 100000")
        if self.python_workers > 32 or self.db_writers > 8 or self.spark_partitions > 10000:
            raise ValueError("worker/partition concurrency exceeds supported bounds")
        if not 0 <= self.max_reject_ratio <= 1 or not 0 <= self.max_unknown_player_ratio <= 1:
            raise ValueError("quality thresholds must be between zero and one")
        if self.min_free_disk_bytes < 0 or self.missing_player_policy not in ("unknown", "reject"):
            raise ValueError("invalid disk limit or missing-player policy")
        if not (self.spark_master.startswith(("local[", "spark://", "k8s://")) or self.spark_master == "yarn"):
            raise ValueError("unsupported Spark master")

    def snapshot(self):
        result = asdict(self)
        result["data_root"] = str(self.data_root)
        result["transform_version"] = VERSION
        return result

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.snapshot(), sort_keys=True).encode()).hexdigest()


def load(path="config/pipeline.toml", month=None, **overrides):
    with Path(path).open("rb") as stream:
        config = tomllib.load(stream)
    month = month or config["test_month"]
    if config.get("all_months_enabled") is not False:
        raise ValueError("all-month execution must remain disabled")
    if month not in config.get("allowed_months", [config["test_month"]]):
        raise ValueError(f"month {month!r} is not explicitly allowlisted")
    archive = config.get("archives", {}).get(month)
    if archive is None:
        if month != config["test_month"]:
            raise ValueError("allowlisted month requires its own checksum and expected count")
        archive = {"sha256": config["archive_sha256"], "expected_games": config["expected_games"],
                   "max_download_bytes": config["max_download_bytes"]}
    source = Source(month, archive["sha256"], archive["expected_games"],
                    archive.get("max_download_bytes", config["max_download_bytes"]),
                    archive.get("url", f"https://database.lichess.org/standard/lichess_db_standard_rated_{month}.pgn.zst"))
    options = dict(config.get("optimized", {}))
    options["data_root"] = Path(os.environ.get("CHESS_DATA_ROOT", "/data"))
    options["spark_master"] = os.environ.get("SPARK_MASTER", options.get("spark_master", "local[2]"))
    options.update({key: value for key, value in overrides.items() if value is not None})
    return Settings(source, **options)
