"""Typed deployment configuration: environment > TOML > compatible defaults.

Only non-secret values belong in TOML. Credentials use environment variables or
their *_FILE equivalents, which work with mounted Kubernetes/Docker secrets.
"""
from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import tomllib
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Paths:
    """Stable container mount paths; migrate the corresponding volumes together."""

    data: Path = Path("/data")
    reports: Path = Path("/reports")
    study: Path = Path("/study")
    models: Path = Path("/models")
    evidence: Path = Path("/evidence")
    docs: Path = Path("/app/docs")
    stockfish: Path = Path("/usr/games/stockfish")


@dataclass(frozen=True)
class Server:
    """Listen addresses and same-origin private-service transport limits."""

    host: str = "0.0.0.0"
    web_port: int = 8080
    study_port: int = 8081
    worker_port: int = 8082
    web_threads: int = 4
    study_threads: int = 4
    study_url: str = "http://study-api:8081"
    proxy_timeout_seconds: int = 300
    max_upload_bytes: int = 1099511627776


@dataclass(frozen=True)
class Workers:
    """Single-worker budgets and graceful stop limits; queues remain in PostgreSQL."""

    embedded: bool = False
    poll_seconds: int = 1
    shutdown_seconds: int = 330
    repertoire_game_seconds: int = 30
    archive_poll_seconds: int = 3
    archive_check_seconds: int = 3600


ENV = {
    "paths.data": "CHESS_DATA_ROOT", "paths.reports": "CHESS_REPORTS_ROOT",
    "paths.study": "STUDY_ROOT", "paths.models": "ML_ROOT",
    "paths.evidence": "EVIDENCE_ROOT", "paths.docs": "DOCS_ROOT",
    "paths.stockfish": "STOCKFISH_PATH", "server.host": "CHESS_BIND_HOST",
    "server.web_port": "CHESS_WEB_PORT", "server.study_port": "CHESS_STUDY_PORT",
    "server.worker_port": "CHESS_WORKER_PORT", "server.web_threads": "CHESS_WEB_THREADS",
    "server.study_threads": "CHESS_STUDY_THREADS", "server.study_url": "STUDY_SERVICE_URL",
    "server.proxy_timeout_seconds": "STUDY_PROXY_TIMEOUT_SECONDS",
    "server.max_upload_bytes": "STUDY_MAX_UPLOAD_BYTES", "workers.embedded": "STUDY_WORKER",
    "workers.poll_seconds": "STUDY_WORKER_POLL_SECONDS",
    "workers.shutdown_seconds": "STUDY_SHUTDOWN_SECONDS",
    "workers.repertoire_game_seconds": "STUDY_REPERTOIRE_GAME_SECONDS",
    "workers.archive_poll_seconds": "CHESS_ARCHIVE_POLL_SECONDS",
    "workers.archive_check_seconds": "CHESS_ARCHIVE_CHECK_SECONDS",
}


@dataclass(frozen=True)
class Runtime:
    """Validated configuration shared by command entrypoints and service adapters."""

    paths: Paths
    server: Server
    workers: Workers


def secret(name, default=None):
    """Read a plain or mounted secret; reject conflicting nonempty sources.

    File contents are never printed or inserted into a configuration snapshot.
    A trailing line ending is removed for conventional mounted password files.
    """
    plain, path = os.environ.get(name), os.environ.get(name+"_FILE")
    if plain and path:
        raise ValueError(f"Configure {name} or {name}_FILE, not both")
    if path:
        return Path(path).read_text().rstrip("\r\n")
    return plain if plain is not None else default


def postgres_options():
    """Add mounted-password support while retaining standard libpq PG* settings."""
    value = secret("PGPASSWORD")
    return {"password": value} if value is not None else {}


def _section(name, cls, values):
    allowed = {field.name: field for field in fields(cls)}
    if not isinstance(values, dict) or set(values)-allowed.keys():
        raise ValueError(f"Unknown or invalid runtime {name} settings")
    result = {}
    for key, field in allowed.items():
        raw = os.environ.get(ENV[name+"."+key], values.get(key, field.default))
        try:
            if isinstance(field.default, bool):
                if type(raw) not in (bool, int, str) or raw not in (True, False, 0, 1, "0", "1", "true", "false"):
                    raise ValueError("boolean must be true/false or 0/1")
                value = raw in (True, 1, "1", "true")
            elif isinstance(field.default, int):
                if isinstance(raw, (bool, float)): raise ValueError("expected integer")
                value = int(raw)
            elif isinstance(field.default, Path):
                value = Path(raw)
                if not value.is_absolute(): raise ValueError("container paths must be absolute")
            else:
                if not isinstance(raw, str): raise ValueError("expected text")
                value = raw
        except (ValueError, TypeError):
            raise ValueError(f"Invalid runtime setting: {name}.{key}") from None
        result[key] = value
    return cls(**result)


def load(path=None):
    """Load and validate runtime settings without creating files or connecting services."""
    explicit = path or os.environ.get("CHESS_RUNTIME_CONFIG")
    file = Path(explicit or "config/runtime.toml")
    if explicit and not file.is_file(): raise ValueError("Runtime config file does not exist")
    data = tomllib.loads(file.read_text()) if file.is_file() else {}
    if set(data)-{"paths", "server", "workers"}: raise ValueError("Unknown runtime config section")
    value = Runtime(_section("paths", Paths, data.get("paths", {})),
                    _section("server", Server, data.get("server", {})),
                    _section("workers", Workers, data.get("workers", {})))
    for name in ("web_port", "study_port", "worker_port"):
        if not 1 <= getattr(value.server, name) <= 65535: raise ValueError("Invalid service port")
    for name in ("web_threads", "study_threads"):
        if not 1 <= getattr(value.server, name) <= 64: raise ValueError("Invalid service thread count")
    if not 1 <= value.server.proxy_timeout_seconds <= 3600 or not 1 <= value.server.max_upload_bytes <= 2**50:
        raise ValueError("Invalid HTTP resource limit")
    url = urlsplit(value.server.study_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("STUDY_SERVICE_URL must be an HTTP(S) service URL without credentials or query parameters")
    if not 1 <= value.workers.repertoire_game_seconds <= 300:
        raise ValueError("Repertoire game budget must be 1–300 seconds")
    for name in ("poll_seconds", "archive_poll_seconds", "archive_check_seconds", "shutdown_seconds"):
        if not 1 <= getattr(value.workers, name) <= 86400: raise ValueError("Invalid worker interval")
    return value


def main():
    """Print effective non-secret deployment configuration for operator review."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    args = parser.parse_args()
    value = load(args.config)
    report = asdict(value)
    report["secrets_configured"] = {name: bool(secret(name)) for name in
        ("PGPASSWORD", "PGADMIN_PASSWORD", "LOADER_PASSWORD", "READER_PASSWORD", "STUDY_PASSWORD", "STUDY_API_TOKEN")}
    print(json.dumps(report, default=str, indent=2))


if __name__ == "__main__": main()
