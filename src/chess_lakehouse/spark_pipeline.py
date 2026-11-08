"""Actual PySpark tasks parse/validate PGN partitions and COPY them into PostgreSQL."""
from functools import partial

from .framing import parse_frame
from .warehouse import copy_partition


def parse_partition(rows, month, stamp):
    """Transform records within one Spark partition using the shared chess model."""
    for row in rows:
        yield parse_frame(row.asDict(), month, stamp)


def run(paths, schema, month, stamp, chunk_size, partitions):
    """Execute the baseline Spark pipeline and retain its structured processing results."""
    from pyspark.sql import SparkSession
    from pyspark.sql.types import LongType, StringType, StructField, StructType

    spark = (SparkSession.builder.appName("chess-single-month")
             .master("local[2]")
             .config("spark.driver.memory", "1g")
             .config("spark.sql.shuffle.partitions", str(partitions))
             .config("spark.ui.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    try:
        source_schema = StructType([StructField("record_number", LongType(), False),
                                    StructField("pgn", StringType(), False)])
        result_schema = StructType([StructField("record_number", LongType(), False),
                                    StructField("game_id", StringType()),
                                    StructField("models", StringType()),
                                    StructField("reason", StringType())])
        frames = spark.read.schema(source_schema).option("mode", "FAILFAST").json([str(p) for p in paths])
        frames = frames.repartition(partitions)
        transformed = frames.rdd.mapPartitions(partial(parse_partition, month=month, stamp=stamp))
        records = spark.createDataFrame(transformed, result_schema)
        # No collect()/toPandas(): workers stream COPY batches over Docker's network.
        records.foreachPartition(partial(copy_partition, schema=schema, chunk_size=chunk_size))
        return dict(spark_version=spark.version, spark_master="local[2]", partitions=partitions)
    finally:
        spark.stop()
