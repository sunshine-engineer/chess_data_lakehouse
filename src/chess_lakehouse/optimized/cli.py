"""Container commands for controlled historical batch processing."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal

from .settings import ENGINES, load
from .storage import atomic_json
from .workflow import event, fetch, prepare_source, run


def interrupted(signum, frame):
    raise InterruptedError(f"received signal {signum}; resume with the recorded run ID")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "prepare", "run", "compare", "migrate", "init-volume", "status", "backfill"])
    parser.add_argument("--engine", choices=ENGINES, default="python")
    parser.add_argument("--config", default="config/pipeline.toml")
    parser.add_argument("--month")
    parser.add_argument("--months", help="Explicit comma-separated allowlisted months, at most six")
    parser.add_argument("--reports", type=Path, default=Path("/reports"))
    parser.add_argument("--benchmark-group", default="manual")
    parser.add_argument("--resume", help="Failed/interrupted run ID; settings/code must match")
    parser.add_argument("--copy-batch-rows", type=int)
    parser.add_argument("--frame-target-bytes", type=int)
    parser.add_argument("--python-workers", type=int)
    parser.add_argument("--spark-partitions", type=int)
    parser.add_argument("--db-writers", type=int)
    parser.add_argument("--spark-master")
    args = parser.parse_args(argv)
    if os.environ.get("CHESS_CONTAINER") != "1":
        parser.error("run this command with Docker Compose")
    signal.signal(signal.SIGTERM, interrupted)
    if args.command == "init-volume":
        if os.geteuid() != 0:
            parser.error("init-volume is an administrative one-shot container")
        for root in (Path("/data"), Path("/reports")):
            root.mkdir(parents=True, exist_ok=True)
            os.chown(root, 10001, 10001)
            for path in root.rglob("*"):
                if not path.is_symlink():
                    os.chown(path, 10001, 10001)
        event("volumes_initialized")
        return
    if args.command == "migrate":
        from .migrate import migrate
        migrate()
        event("migrations_applied", schema_version=1)
        return
    overrides = {name: getattr(args, name) for name in (
        "copy_batch_rows", "frame_target_bytes", "python_workers", "spark_partitions", "db_writers", "spark_master")}
    settings = load(args.config, args.month, **overrides)
    if args.command == "fetch":
        fetch(settings)
    elif args.command == "prepare":
        prepare_source(settings)
    elif args.command == "run":
        run(args.engine, settings, args.reports, args.benchmark_group, args.resume)
    elif args.command == "backfill":
        months = args.months.split(",") if args.months else []
        if not 1 <= len(months) <= 6 or len(set(months)) != len(months):
            parser.error("backfill needs one to six unique, explicitly allowlisted months")
        # Validate every month before any network or database change.
        jobs = [load(args.config, month, **overrides) for month in months]
        for job in jobs:
            run(args.engine, job, args.reports, args.benchmark_group)
    elif args.command == "compare":
        from .comparison import compare
        compare(args.benchmark_group, args.reports)
    elif args.command == "status":
        from .warehouse import connect
        with connect(settings) as conn:
            rows = conn.execute("SELECT run_id,engine,source_month,status,heartbeat_at,error FROM control.pipeline_runs ORDER BY started_at DESC LIMIT 30").fetchall()
        for row in rows:
            print(json.dumps(dict(zip(("run_id", "engine", "source_month", "status", "heartbeat_at", "error"), row)), default=str))


if __name__ == "__main__":
    main()
