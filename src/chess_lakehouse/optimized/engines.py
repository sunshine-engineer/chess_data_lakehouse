"""Parse immutable shards with serial Python, bounded processes, or native Spark enrichment."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from functools import partial
import json
import multiprocessing
import os
from pathlib import Path
import shutil

import pyarrow.parquet as pq

from .records import BASE_SCHEMA, COLUMNS, SCHEMA, enrich, parse_base, spark_enrich, spark_schema
from .storage import atomic_json, check_disk, verify_file, write_parquet


def _python_shard(entry, month, policy, output, batch_rows):
    def batches():
        for batch in pq.ParquetFile(entry["path"]).iter_batches(batch_size=batch_rows):
            yield [enrich(parse_base(row, month, policy)) for row in batch.to_pylist()]
    result = write_parquet(output, batches(), SCHEMA)
    result["shard_id"] = entry["shard_id"]
    return result


def _spark_rows(rows, month, policy):
    for row in rows:
        parsed = parse_base(row.asDict(), month, policy)
        yield tuple(parsed[field.name] for field in BASE_SCHEMA)


def _spark_batch(spark, entries, settings, root):
    """A committed Spark output batch can be replayed without re-parsing PGN."""
    from pyspark.sql import functions as f
    from pyspark.sql.types import LongType, StructField, StructType
    from ..pipeline import sha256_file
    output = root / ("spark-batch-" + "-".join(str(e["shard_id"]) for e in entries))
    if not (output / "_SUCCESS").exists():
        shutil.rmtree(output, ignore_errors=True)
        source = spark.read.parquet(*[e["path"] for e in entries])
        # Preserve source record numbers; no driver collect of game rows.
        parsed = source.repartition(settings.spark_partitions).rdd.mapPartitions(partial(
            _spark_rows, month=settings.source.month, policy=settings.missing_player_policy))
        df = spark.createDataFrame(parsed, spark_schema())
        df = spark_enrich(df)
        shard_expr = None
        for entry in entries:
            predicate = f.col("record_number").between(entry["first_record"], entry["last_record"])
            shard_expr = f.when(predicate, f.lit(entry["shard_id"])) if shard_expr is None else shard_expr.when(predicate, entry["shard_id"])
        df = df.withColumn("shard_id", shard_expr.otherwise(-1).cast("long"))
        df.repartition(settings.spark_partitions, "shard_id").write.mode("overwrite").partitionBy("shard_id").parquet(str(output))
    results = []
    for entry in entries:
        files = sorted((output / f"shard_id={entry['shard_id']}").glob("*.parquet"))
        if not files:
            raise ValueError("Spark did not write a required input shard")
        # Normally one file per hash-partitioned shard; support multiple files.
        metadata = [dict(path=str(p), sha256=sha256_file(p), rows=pq.ParquetFile(p).metadata.num_rows,
                         bytes=p.stat().st_size) for p in files]
        if sum(file["rows"] for file in metadata) != entry["rows"]:
            raise ValueError("Spark shard row count mismatch")
        results.append(dict(shard_id=entry["shard_id"], files=metadata))
    return results


def transform_shards(engine, frames, settings, root, on_shard=None, interrupt_after=None):
    """Manifest checkpoints are written after each durable output shard/batch."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else dict(
        fingerprint=settings.fingerprint(), engine=engine, shards=[])
    if manifest["fingerprint"] != settings.fingerprint() or manifest["engine"] != engine:
        raise ValueError("resume settings differ from the original run")
    for shard in manifest["shards"]:
        for entry in shard["files"]:
            verify_file(entry, SCHEMA)
    completed = {shard["shard_id"] for shard in manifest["shards"]}
    pending = [entry for entry in frames["shards"] if entry["shard_id"] not in completed]

    def commit(result):
        if "files" not in result:
            result = dict(shard_id=result["shard_id"], files=[{k:v for k,v in result.items() if k != "shard_id"}])
        manifest["shards"].append(result)
        manifest["shards"].sort(key=lambda row: row["shard_id"])
        atomic_json(path, manifest)
        if on_shard:
            on_shard(result)
        if interrupt_after is not None and len(manifest["shards"]) >= interrupt_after:
            raise InterruptedError("injected transform interruption")

    if engine == "python":
        for entry in pending:
            check_disk(root, settings.min_free_disk_bytes)
            commit(_python_shard(entry, settings.source.month, settings.missing_player_policy,
                                 root / f"part-{entry['shard_id']:06d}.parquet", settings.parse_batch_rows))
    elif engine == "python_mp":
        # At most one outstanding shard per process; never queue the entire archive.
        with ProcessPoolExecutor(max_workers=settings.python_workers,
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            entries = iter(pending)
            active = set()
            while True:
                while len(active) < settings.python_workers:
                    entry = next(entries, None)
                    if entry is None:
                        break
                    check_disk(root, settings.min_free_disk_bytes)
                    active.add(pool.submit(_python_shard, entry, settings.source.month,
                        settings.missing_player_policy, root / f"part-{entry['shard_id']:06d}.parquet",
                        settings.parse_batch_rows))
                if not active:
                    break
                done, active = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    commit(future.result())
    elif engine == "spark":
        from pyspark.sql import SparkSession
        events = settings.data_root / "spark-events"
        events.mkdir(parents=True, exist_ok=True)
        builder = (SparkSession.builder.appName("chess-historical")
            .master(settings.spark_master).config("spark.driver.memory", settings.spark_driver_memory)
            .config("spark.sql.shuffle.partitions", str(settings.spark_partitions))
            .config("spark.sql.session.timeZone", "UTC").config("spark.ui.enabled", "false")
            .config("spark.eventLog.enabled", "true").config("spark.eventLog.dir", str(events)))
        if settings.spark_master.startswith("spark://"):
            builder = builder.config("spark.driver.host", os.environ.get("SPARK_DRIVER_HOST", "spark"))
        spark = builder.getOrCreate()
        spark.sparkContext.setLogLevel("WARN")
        try:
            width = max(1, settings.spark_partitions)
            for start in range(0, len(pending), width):
                check_disk(root, settings.min_free_disk_bytes)
                for result in _spark_batch(spark, pending[start:start+width], settings, root):
                    commit(result)
        finally:
            spark.stop()
    else:
        raise ValueError(f"unsupported engine: {engine}")
    manifest["complete"] = True
    atomic_json(path, manifest)
    return manifest
