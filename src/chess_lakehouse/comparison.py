"""Compare PostgreSQL model contents and observed container benchmark samples."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from importlib.metadata import version
from pathlib import Path
import platform
from statistics import median

from psycopg import sql

from . import warehouse


def compare(reports: Path):
    with warehouse.connect() as conn:
        runs = conn.execute("""SELECT engine,schema_name,report FROM public.pipeline_runs
            WHERE status='complete' ORDER BY finished_at""").fetchall()
        grouped = {engine: [r for r in runs if r[0] == engine] for engine in ("python", "spark")}
        if any(not values for values in grouped.values()):
            raise ValueError("both engines need a successful run")
        latest = {engine: values[-1] for engine, values in grouped.items()}
        signatures = {(r[2]["archive_sha256"], r[2]["source_month"], r[2]["chunk_size"],
                       r[2]["container_cpu_limit"], r[2]["container_memory_limit_mib"]) for r in latest.values()}
        if len(signatures) != 1:
            raise ValueError("latest runs are not comparable: source/configuration differs")
        signature = next(iter(signatures))
        for engine in grouped:
            grouped[engine] = [r for r in grouped[engine] if (
                r[2]["archive_sha256"], r[2]["source_month"], r[2]["chunk_size"],
                r[2]["container_cpu_limit"], r[2]["container_memory_limit_mib"]) == signature]
        left, right = latest["python"][1], latest["spark"][1]
        equality = {}
        for table in warehouse.TABLES:
            cols = conn.execute("""SELECT column_name FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s AND column_name <> 'ingested_at'
                ORDER BY ordinal_position""", (left, table)).fetchall()
            projection = sql.SQL(",").join(sql.Identifier(c[0]) for c in cols)
            for a, b in ((left, right), (right, left)):
                query = sql.SQL("SELECT EXISTS(SELECT {} FROM {}.{} EXCEPT ALL SELECT {} FROM {}.{})").format(
                    projection, sql.Identifier(a), sql.Identifier(table), projection, sql.Identifier(b), sql.Identifier(table))
                if conn.execute(query).fetchone()[0]:
                    raise ValueError(f"model mismatch: {table}")
            equality[table] = True
        summary = {}
        for engine, values in grouped.items():
            samples = [r[2] for r in values]
            summary[engine] = dict(samples=len(samples))
            for metric in ("elapsed_seconds", "input_games_per_second", "container_cpu_seconds",
                           "container_peak_memory_mib", "model_bytes"):
                observations = [r[metric] for r in samples if r[metric] is not None]
                summary[engine][f"median_{metric}"] = round(median(observations), 3) if observations else None
            summary[engine]["latest_counts"] = {k: values[-1][2][k] for k in
                                               ("input_games", "accepted_games", "rejected_games", "duplicate_games", "tables")}
        result = dict(generated_at=datetime.now(timezone.utc).isoformat(), source_month=signature[1],
                      archive_sha256=signature[0], equality=equality, summary=summary,
                      environment=dict(python=platform.python_version(), kernel=platform.release(),
                                       architecture=platform.machine(),
                                       postgres=conn.execute("SHOW server_version").fetchone()[0],
                                       packages={name: version(name) for name in
                                                 ("pyspark", "psycopg", "chess", "zstandard", "pyarrow")}),
                      samples=[r[2] for r in runs if r in grouped[r[0]]])
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
        lines = ["# Python and Spark — measured Docker comparison", "", f"Source month: **{signature[1]}**.", "",
                 "| Metric (median) | Python | Spark |", "|---|---:|---:|"]
        labels = {"samples": "Completed samples", "median_elapsed_seconds": "Total processing seconds",
                  "median_input_games_per_second": "Input games/second", "median_container_cpu_seconds": "Container CPU seconds",
                  "median_container_peak_memory_mib": "Container peak memory MiB", "median_model_bytes": "PostgreSQL model bytes"}
        for metric, label in labels.items():
            lines.append(f"| {label} | {summary['python'][metric]} | {summary['spark'][metric]} |")
        ratio = summary["spark"]["median_elapsed_seconds"] / summary["python"]["median_elapsed_seconds"]
        lines += ["", f"Observed Spark/Python elapsed-time ratio: **{ratio:.2f}x** (lower favors Spark).", "",
                  "All six latest model tables match, excluding ingestion timestamps.", "",
                  "Both containers have 2 CPU and 3 GiB limits. PostgreSQL is shared and runs separately.",
                  "Timing includes checksum verification, per-engine framing, Spark startup/shutdown where applicable,",
                  "parsing, staging, SQL modeling, and validation. Download, image build and comparison are excluded.",
                  "Memory is the whole-container cgroup peak (JVM/workers included). CPU is the container cgroup usage delta.",
                  "PostgreSQL CPU/memory are not attributed to either client; model bytes include indexes but exclude staging.",
                  "This is a local single-machine test on one small month, not a distributed scaling benchmark.",
                  "Cache order and background machine load can affect results; inspect raw per-run phase timings.", ""]
        (reports / "comparison.md").write_text("\n".join(lines))
        print("\n".join(lines))
        return result
