# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze ingestion: Auto Loader from the telemetry volume into `force.bronze.events`
# MAGIC
# MAGIC Phase 2 (docs 03 and 05). Run it by hand: it ingests every file that has landed since the last run and
# MAGIC stops (`trigger(availableNow=True)`). Running it twice with no new files adds no rows (exactly-once).
# MAGIC
# MAGIC Open this file from the workspace Git folder and run it on serverless compute. The code cells are the
# MAGIC Python block in `docs/05-platform-setup.md`, "Auto Loader ingestion"; a test keeps them identical.
# MAGIC After the first run, check the `payload` column with the first query in `ingest/phase2_checkpoint.sql`.

# COMMAND ----------

from pyspark.sql import functions as F

LANDING = "/Volumes/force/raw/telemetry"
CHECKPOINT = "/Volumes/force/raw/checkpoints/bronze_events"

df = (
    spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "json")
    .option("cloudFiles.schemaLocation", f"{CHECKPOINT}/schema")
    .option("cloudFiles.inferColumnTypes", "false")
    .option("cloudFiles.schemaHints", "is_synthetic BOOLEAN, synthetic_ingest_ts TIMESTAMP")
    .option("cloudFiles.schemaEvolutionMode", "rescue")
    .option("multiLine", "false")
    .load(LANDING)
)

# COMMAND ----------

out = (
    df.withColumn("_source_file", F.col("_metadata.file_path"))
      .withColumn("_ingest_ts", F.current_timestamp())
      .withColumn("event_time", F.to_timestamp("event_time"))
      .withColumn("schema_version", F.col("schema_version").cast("int"))
      .withColumn("dt", F.to_date("dt"))
      .withColumn("hh", F.col("hh").cast("int"))
      # payload arrives as a STRING of raw JSON (inferColumnTypes is false), so it is parsed directly;
      # a malformed payload becomes NULL instead of failing the stream
      .withColumn("payload", F.expr("try_parse_json(payload)"))
      .select("event_id", "source_id", "source_type", "mode", "scan_id", "sector_id",
              "schema_version", "event_time", "is_synthetic", "synthetic_ingest_ts", "payload",
              "dt", "hh", "_source_file", "_ingest_ts", "_rescued_data")
)

# COMMAND ----------

(
    out.writeStream
    .format("delta")
    .option("checkpointLocation", f"{CHECKPOINT}/write")
    .option("mergeSchema", "true")
    .partitionBy("dt")
    .trigger(availableNow=True)
    .toTable("force.bronze.events")
    .awaitTermination()
)
