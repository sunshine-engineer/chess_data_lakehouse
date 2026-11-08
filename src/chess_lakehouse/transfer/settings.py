"""Non-secret TOML options and independently prefixed libpq environments."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib

import psycopg


@dataclass(frozen=True)
class Options:
    """Bound memory and define the explicit ownership/verification contract."""

    chunk_rows: int = 50000
    chunk_bytes: int = 16 * 1024 * 1024
    max_row_bytes: int = 64 * 1024 * 1024
    dump_jobs: int = 2
    ownership: str = "preserve"
    privileges: str = "preserve"
    create_missing_roles: bool = False
    verify_source_unchanged: bool = True
    min_free_bytes: int = 1024 * 1024 * 1024
    connect_timeout: int = 10
    lock_timeout_seconds: int = 30

    def __post_init__(self):
        """Reject unbounded buffering, unsupported policies and invalid limits."""
        for name, maximum in (("chunk_rows", 1000000), ("chunk_bytes", 256*1024*1024),
                              ("max_row_bytes", 256*1024*1024), ("dump_jobs", 8),
                              ("connect_timeout", 120), ("lock_timeout_seconds", 600)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError(f"Invalid migration option: {name}")
        if self.min_free_bytes < 0:
            raise ValueError("min_free_bytes cannot be negative")
        if self.ownership not in ("preserve", "current-user") or self.privileges not in ("preserve", "omit"):
            raise ValueError("Unsupported ownership or privileges policy")
        if type(self.create_missing_roles) is not bool or type(self.verify_source_unchanged) is not bool:
            raise ValueError("Migration switches must be TOML booleans")

    def fingerprint(self):
        """Bind a resume to semantic options, excluding passwords and file locations."""
        semantic = {k: v for k, v in asdict(self).items() if k not in
                    {"max_row_bytes", "dump_jobs", "min_free_bytes", "connect_timeout", "lock_timeout_seconds"}}
        return hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()


class Endpoint:
    """Read one libpq connection from SOURCE_* or TARGET_* variables.

    Passwords may be supplied through PGPASSWORD_FILE or PGPASSFILE. Connection
    values are passed to subprocesses as environment variables, never arguments.
    """

    def __init__(self, prefix, timeout=10):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix):
            raise ValueError("Invalid connection environment prefix")
        self.prefix, self.timeout = prefix, timeout

    def environment(self):
        """Return an isolated libpq environment without inheriting another endpoint."""
        result = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        for key in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
                    "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE", "PGSSLMODE", "PGSSLROOTCERT",
                    "PGSSLCERT", "PGSSLKEY", "PGCHANNELBINDING", "PGSSLCRL"):
            value = os.environ.get(f"{self.prefix}_{key}")
            if value is not None:
                result[key] = value
        secret_file = os.environ.get(f"{self.prefix}_PGPASSWORD_FILE")
        if secret_file:
            if "PGPASSWORD" in result:
                raise ValueError(f"Use only one {self.prefix} password source")
            result["PGPASSWORD"] = Path(secret_file).read_text().rstrip("\r\n")
        if not result.get("PGDATABASE") or not (result.get("PGHOST") or result.get("PGSERVICE")):
            raise ValueError(f"Set {self.prefix}_PGHOST and {self.prefix}_PGDATABASE (or a libpq service)")
        result.update(PGCONNECT_TIMEOUT=str(self.timeout), PGAPPNAME="chess-transfer",
                      PGCLIENTENCODING="UTF8", PGTZ="UTC")
        # Dump output must remain COPY text and must not inherit search_path flags.
        return result

    def connect(self, autocommit=False):
        """Open this endpoint without changing the parent process's environment."""
        image_variables = {"PGDATA", "PG_MAJOR", "PG_VERSION", "PG_SHA256"}
        if any(key.startswith('PG') and key not in image_variables for key in os.environ):
            raise ValueError("Use isolated SOURCE_/TARGET_ connection variables; remove ambient unprefixed PG connection variables")
        env = self.environment()
        mapping = {"PGHOST": "host", "PGHOSTADDR": "hostaddr", "PGPORT": "port",
                   "PGDATABASE": "dbname", "PGUSER": "user", "PGPASSWORD": "password",
                   "PGPASSFILE": "passfile", "PGSERVICE": "service", "PGSERVICEFILE": None,
                   "PGSSLMODE": "sslmode", "PGSSLROOTCERT": "sslrootcert", "PGSSLCERT": "sslcert",
                   "PGSSLKEY": "sslkey", "PGCHANNELBINDING": "channel_binding", "PGSSLCRL": "sslcrl"}
        if env.get("PGSERVICEFILE"):
            # psycopg's service file path is process-global in libpq; use explicit
            # endpoint fields instead of silently reading a different service file.
            raise ValueError("Use explicit endpoint fields or the default libpq service file; custom PGSERVICEFILE is for CLI tools only")
        kwargs = {dest: env[src] for src, dest in mapping.items() if dest and src in env}
        return psycopg.connect(**kwargs, autocommit=autocommit, connect_timeout=self.timeout,
                               application_name="chess-transfer", options="-c timezone=UTC -c datestyle=ISO -c extra_float_digits=3 -c bytea_output=hex")

    def public(self):
        """Describe the destination for operator review without exposing secrets."""
        env = self.environment()
        return {key.lower()[2:]: env.get(key) for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGSSLMODE")}


def load(path):
    """Load a strict TOML file containing prefixes and non-secret migration options."""
    data = tomllib.loads(Path(path).read_text())
    if set(data) - {"source", "target", "options"}:
        raise ValueError("Unknown migration configuration section")
    prefixes = []
    for section, default in (("source", "SOURCE"), ("target", "TARGET")):
        values = data.get(section, {})
        if set(values) - {"env_prefix"}:
            raise ValueError(f"{section} accepts only env_prefix; keep credentials in the environment or secret files")
        prefixes.append(values.get("env_prefix", default))
    options = Options(**data.get("options", {}))
    return Endpoint(prefixes[0], options.connect_timeout), Endpoint(prefixes[1], options.connect_timeout), options
