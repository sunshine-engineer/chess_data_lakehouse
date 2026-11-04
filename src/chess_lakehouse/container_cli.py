"""Container-only entry points: fetch one archive, run an engine, compare results."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
import tomllib
from uuid import uuid4

from psycopg import sql

from .framing import frame_archive, python_records
from .pipeline import BASE_URL, download, sha256_file, validate_config
from . import warehouse


def settings(path, month=None, chunk_size=None):
    with Path(path).open("rb") as stream:
        config = tomllib.load(stream)
    month = month if month is not None else config["test_month"]
    chunk_size = chunk_size if chunk_size is not None else config["chunk_size"]
    validate_config(config, month, chunk_size)
    return config, month, chunk_size


def archive_path(config, month):
    filename = f"lichess_db_standard_rated_{month}.pgn.zst"
    return Path("/data/bronze") / filename, f"{BASE_URL}/{filename}"


def cgroup_metrics():
    """Whole-container Linux cgroup v2 counters include Spark's JVM and workers."""
    root = Path("/sys/fs/cgroup")
    try:
        cpu = dict(line.split() for line in (root / "cpu.stat").read_text().splitlines())
        return dict(container_cpu_seconds=int(cpu["usage_usec"]) / 1000000,
                    container_peak_memory_mib=int((root / "memory.peak").read_text()) / 1024 ** 2)
    except (OSError, ValueError, KeyError):
        return dict(container_cpu_seconds=None, container_peak_memory_mib=None)


def run_engine(engine, config, month, chunk_size, partitions, reports):
    started = time.perf_counter()
    metrics_before = cgroup_metrics()
    archive, _ = archive_path(config, month)
    # Download is deliberately a separate shared step for fair cached-input timing.
    if not archive.exists():
        raise ValueError("run the fetch service before benchmarking")
    if sha256_file(archive) != config["archive_sha256"]:
        raise ValueError("archive checksum mismatch")
    run_id = f"{engine}_{uuid4().hex[:16]}"
    schema = run_id
    work = Path("/data/work") / run_id
    stamp = datetime.now(timezone.utc).isoformat()
    phases = {}
    conn = warehouse.connect()
    conn.autocommit = True
    locked = False
    initialized = False
    published = False
    try:
        locked = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (f"chess-{engine}",)).fetchone()[0]
        if not locked:
            raise ValueError(f"another {engine} pipeline is active")
        with conn.transaction():
            warehouse.initialize(conn, schema)
            conn.execute("INSERT INTO public.pipeline_runs(run_id,engine,schema_name,status) VALUES (%s,%s,%s,'running')",
                         (run_id, engine, schema))
        initialized = True
        print(f"{engine}: framing verified archive {month}", flush=True)
        phase = time.perf_counter()
        paths, count = frame_archive(archive, work, chunk_size)
        if count != config["expected_games"]:
            raise ValueError(f"expected {config['expected_games']} frames, found {count}")
        phases["frame_seconds"] = round(time.perf_counter() - phase, 3)
        print(f"{engine}: parsing and staging {count} games", flush=True)
        phase = time.perf_counter()
        details = {}
        if engine == "python":
            warehouse.copy_partition(python_records(paths, month, stamp), schema, chunk_size)
        else:
            from .spark_pipeline import run
            details = run(paths, schema, month, stamp, chunk_size, partitions)
        phases["parse_and_stage_seconds"] = round(time.perf_counter() - phase, 3)
        print(f"{engine}: building and validating PostgreSQL models", flush=True)
        phase = time.perf_counter()
        with conn.transaction():
            warehouse.set_schema(conn, schema)
            stats = warehouse.materialize(conn, month, stamp, config["expected_games"])
            conn.execute("DROP TABLE stage_accepted, stage_input")
            phases["model_and_validate_seconds"] = round(time.perf_counter() - phase, 3)
            elapsed = time.perf_counter() - started
            metrics = cgroup_metrics()
            cpu = metrics["container_cpu_seconds"]
            if cpu is not None and metrics_before["container_cpu_seconds"] is not None:
                metrics["container_cpu_seconds"] = round(cpu - metrics_before["container_cpu_seconds"], 3)
            report = dict(run_id=run_id, schema=schema, engine=engine, source_month=month,
                          archive_sha256=config["archive_sha256"], chunk_size=chunk_size,
                          all_months_enabled=False, elapsed_seconds=round(elapsed, 3),
                          input_games_per_second=round(count / elapsed, 2),
                          container_cpu_limit=2, container_memory_limit_mib=3072,
                          phases=phases, **metrics, **stats, **details)
            warehouse.publish(conn, engine, schema, run_id, report)
        published = True
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"{run_id}.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return report
    except BaseException as error:
        if initialized and not published:
            with conn.transaction():
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
                conn.execute("UPDATE public.pipeline_runs SET status='failed',finished_at=now(),error=%s WHERE run_id=%s",
                             (type(error).__name__, run_id))
        raise
    finally:
        if locked:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"chess-{engine}",))
        conn.close()
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "run", "compare"])
    parser.add_argument("--engine", choices=["python", "spark"], default="python")
    parser.add_argument("--config", default="config/pipeline.toml")
    parser.add_argument("--month")
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--partitions", type=int, default=2)
    parser.add_argument("--reports", type=Path, default=Path("/reports"))
    args = parser.parse_args(argv)
    # Avoid accidental host execution; Docker sets this marker at image build time.
    if os.environ.get("CHESS_CONTAINER") != "1":
        parser.error("use docker compose run --rm <service>; this entry point is container-only")
    config, month, size = settings(args.config, args.month, args.chunk_size)
    if not 1 <= args.partitions <= 8:
        parser.error("partitions must be between 1 and 8")
    if args.command == "fetch":
        archive, url = archive_path(config, month)
        download(url, archive, config["archive_sha256"], config["max_download_bytes"])
        print(f"Verified single-month archive: {month}")
    elif args.command == "run":
        run_engine(args.engine, config, month, size, args.partitions, args.reports)
    else:
        from .comparison import compare
        compare(args.reports)


if __name__ == "__main__":
    main()
