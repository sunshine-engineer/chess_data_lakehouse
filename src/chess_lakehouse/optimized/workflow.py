"""One-month execution, explicit durable resume, structured events and real metrics."""
from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import signal
import time
from uuid import uuid4

from psycopg import sql

from ..pipeline import sha256_file
from .download import download_source
from .engines import transform_shards
from .settings import ENGINES
from .storage import atomic_json, check_disk, file_lock, prepare
from . import warehouse


def event(name, **values):
    print(json.dumps(dict(event=name, timestamp=datetime.now(timezone.utc).isoformat(), **values), default=str), flush=True)


def provenance():
    package = Path(__file__).parents[1]
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.suffix in (".py", ".sql"):
            digest.update(str(path.relative_to(package)).encode())
            digest.update(path.read_bytes())
    code_hash = digest.hexdigest()
    build = Path("/app/BUILD_ID")
    return dict(code_hash=code_hash, image_id=os.environ.get("CHESS_IMAGE_ID") or ("build:" + build.read_text().strip() if build.exists() else "unrecorded"),
                python=platform.python_version(), architecture=platform.machine(), kernel=platform.release(),
                packages={name: version(name) for name in ("chess", "pyarrow", "pyspark", "psycopg", "zstandard")})


def resources():
    root = Path("/sys/fs/cgroup")
    result = dict(container_cpu_seconds=None, container_peak_memory_mib=None, cpu_limit=None, memory_limit_mib=None)
    try:
        cpu = dict(line.split() for line in (root / "cpu.stat").read_text().splitlines())
        result["container_cpu_seconds"] = int(cpu["usage_usec"]) / 1000000
        result["container_peak_memory_mib"] = int((root / "memory.peak").read_text()) / 1024**2
        quota, period = (root / "cpu.max").read_text().split()
        result["cpu_limit"] = None if quota == "max" else int(quota) / int(period)
        maximum = (root / "memory.max").read_text().strip()
        result["memory_limit_mib"] = None if maximum == "max" else int(maximum) / 1024**2
    except (OSError, ValueError, KeyError):
        pass
    return result


def fetch(settings):
    path = settings.data_root / "bronze" / settings.source.sha256 / f"lichess_db_standard_rated_{settings.source.month}.pgn.zst"
    check_disk(path.parent, settings.min_free_disk_bytes)
    with file_lock(path.with_suffix(".download.lock")):
        download_source(settings)
    event("archive_verified", month=settings.source.month, bytes=path.stat().st_size)
    return path


def prepare_source(settings, archive=None):
    archive = Path(archive) if archive is not None else fetch(settings)
    if archive.stat().st_size > settings.source.max_download_bytes:
        raise ValueError("archive exceeds declared size limit")
    if sha256_file(archive) != settings.source.sha256:
        raise ValueError("archive checksum mismatch")
    started = time.perf_counter()
    frames = prepare(archive, settings)
    event("frames_ready", source_month=settings.source.month, shards=len(frames["shards"]),
          rows=settings.source.expected_games, seconds=round(time.perf_counter()-started, 3))
    return frames


def run(engine, settings, reports=Path("/reports"), group="manual", resume=None, archive=None,
        interrupt_after_transform=None, interrupt_after_load=None):
    if engine not in ENGINES:
        raise ValueError("unknown engine")
    runtime = provenance()
    frames = prepare_source(settings, archive)
    run_id = resume or f"{engine}_{uuid4().hex[:20]}"
    # Immutable filenames and filesystem locks work on local/shared Docker volumes.
    warehouse.stage_for(run_id)
    root = settings.data_root / "runs-v2" / run_id
    root.mkdir(parents=True, exist_ok=True)
    conn = warehouse.connect(settings)
    conn.autocommit = True
    locked, registered, published = False, False, False
    start = time.perf_counter()
    before = resources()
    phases = {}
    loaded_this_attempt = 0
    try:
        with file_lock(root / ".run.lock", blocking=False):
            locked = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", ("chess-v2-" + engine,)).fetchone()[0]
            if not locked:
                raise ValueError(f"another {engine} publication is active")
            if resume:
                previous = conn.execute("""SELECT engine,config_hash,code_hash,status,benchmark_group
                    FROM control.pipeline_runs WHERE run_id=%s""", (run_id,)).fetchone()
                if not previous or previous[:3] != (engine, settings.fingerprint(), runtime["code_hash"]):
                    raise ValueError("resume run does not match engine/source/config/code")
                if previous[3] == "complete":
                    raise ValueError("completed run is immutable; start a fresh benchmark run")
                if previous[3] == "abandoned":
                    raise ValueError("abandoned run cannot be resumed")
                group = previous[4]
                conn.execute("UPDATE control.pipeline_runs SET status='running',error=NULL,heartbeat_at=now() WHERE run_id=%s", (run_id,))
            else:
                with conn.transaction():
                    warehouse.create_run(conn, run_id, engine, settings, runtime, group)
            registered = True
            event("run_started", run_id=run_id, engine=engine, month=settings.source.month, resume=bool(resume))
            phase = time.perf_counter()
            transformed = transform_shards(engine, frames, settings, root / "silver", on_shard=lambda shard:
                event("shard_transformed", run_id=run_id, shard_id=shard["shard_id"]), interrupt_after=interrupt_after_transform)
            phases["parse_seconds"] = round(time.perf_counter()-phase, 3)
            phase = time.perf_counter()

            def loaded(result):
                nonlocal loaded_this_attempt
                loaded_this_attempt += 1
                event("shard_loaded", run_id=run_id, **result)
                if interrupt_after_load is not None and loaded_this_attempt >= interrupt_after_load:
                    raise InterruptedError("injected load interruption")

            warehouse.load_shards(run_id, transformed, settings, loaded)
            phases["load_seconds"] = round(time.perf_counter()-phase, 3)
            phase = time.perf_counter()
            with conn.transaction():
                stats = warehouse.publish(conn, run_id, engine, settings)
                phases["publish_seconds"] = round(time.perf_counter()-phase, 3)
                elapsed = time.perf_counter()-start
                metrics = resources()
                if before["container_cpu_seconds"] is not None and metrics["container_cpu_seconds"] is not None:
                    metrics["container_cpu_seconds"] -= before["container_cpu_seconds"]
                report = dict(run_id=run_id, engine=engine, source_month=settings.source.month,
                    source_sha256=settings.source.sha256, config_hash=settings.fingerprint(),
                    code_hash=runtime["code_hash"], image_id=runtime["image_id"], runtime=runtime,
                    benchmark_group=group, resumed=bool(resume), elapsed_seconds=elapsed,
                    input_games_per_second=settings.source.expected_games/elapsed,
                    phases=phases, parquet_bytes=sum(entry["bytes"] for shard in transformed["shards"] for entry in shard["files"]),
                    free_disk_bytes=check_disk(settings.data_root, 0), settings=settings.snapshot(), **metrics, **stats)
                conn.execute("""UPDATE control.pipeline_runs SET status='complete',finished_at=now(),heartbeat_at=now(),report=%s
                    WHERE run_id=%s""", (json.dumps(report), run_id))
            published = True
            # Database publication is authoritative; a report filesystem failure cannot delete it.
            atomic_json(Path(reports) / f"{run_id}.json", report)
            atomic_json(root / "report.json", report)
            event("run_complete", **report)
            return report
    except BaseException as error:
        if registered and not published:
            with suppress(Exception):
                conn.execute("UPDATE control.pipeline_runs SET status='failed',finished_at=now(),error=%s WHERE run_id=%s",
                             (type(error).__name__, run_id))
        event("run_failed" if not published else "report_write_failed", run_id=run_id, error=type(error).__name__, published=published)
        raise
    finally:
        with suppress(Exception):
            if locked:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("chess-v2-"+engine,))
        conn.close()
