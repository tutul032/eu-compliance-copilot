"""Databricks job: build the corpus from the raw volume into Delta tables.

    build_corpus_job --catalog workspace --schema compliance_dev

Only the Spark and Delta side lives here; parsing, chunking and the contract are the functions the
local CLI (eucc.build_corpus) uses. Nothing is written unless every entry passes the contract.
pyspark comes from the serverless runtime and is imported only in main, so that the rest of this
module runs in unit tests without Spark.
"""

import argparse
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from eucc.build_corpus import EntryBuild, build_corpus, format_table, table_rows
from eucc.chunk import Chunk
from eucc.corpus.manifest import load_manifest
from eucc.parse import Provision

MERGE_KEY = "chunk_id"
SOURCE_VIEW = "gold_chunks_source"

SILVER_COLUMNS: dict[str, str] = {
    "provision_id": "STRING NOT NULL",
    "reg": "STRING NOT NULL",
    "celex": "STRING NOT NULL",
    "lang": "STRING NOT NULL",
    "version_date": "DATE NOT NULL",
    "revision": "STRING",
    "unit_type": "STRING NOT NULL",
    "article": "STRING NOT NULL",
    "paragraph": "STRING",
    "heading": "STRING",
    "text": "STRING NOT NULL",
    "amended_by": "ARRAY<STRING> NOT NULL",
    "source_url": "STRING NOT NULL",
}
GOLD_COLUMNS: dict[str, str] = {
    "chunk_id": "STRING NOT NULL",
    "provision_id": "STRING NOT NULL",
    "part": "INT NOT NULL",
    "reg": "STRING NOT NULL",
    "celex": "STRING NOT NULL",
    "lang": "STRING NOT NULL",
    "version_date": "DATE NOT NULL",
    "revision": "STRING",
    "unit_type": "STRING NOT NULL",
    "article": "STRING NOT NULL",
    "paragraph": "STRING",
    "heading": "STRING",
    "text": "STRING NOT NULL",
    "chunk_text": "STRING NOT NULL",
    "amended_by": "ARRAY<STRING> NOT NULL",
    "source_url": "STRING NOT NULL",
}


def raw_root(catalog: str, schema: str) -> Path:
    """Where the extractor uploads to: its --upload-to value."""
    return Path(f"/Volumes/{catalog}/{schema}/raw/eurlex")


def table_name(catalog: str, schema: str, table: str) -> str:
    return f"`{catalog}`.`{schema}`.`{table}`"


def spark_schema(columns: dict[str, str]) -> str:
    return ", ".join(f"`{name}` {spark_type}" for name, spark_type in columns.items())


def to_rows(items: list[Provision] | list[Chunk], columns: dict[str, str]) -> list[tuple]:
    """Tuples in schema order; ISO date strings become dates."""
    rows = []
    for item in items:
        values = asdict(item)
        rows.append(
            tuple(
                date.fromisoformat(values[name]) if spark_type.startswith("DATE") else values[name]
                for name, spark_type in columns.items()
            )
        )
    return rows


def create_gold_sql(table: str) -> str:
    """Change Data Feed is set at creation, so the first MERGE is already in the feed."""
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ({spark_schema(GOLD_COLUMNS)}) USING DELTA "
        "TBLPROPERTIES (delta.enableChangeDataFeed = true)"
    )


def merge_gold_sql(table: str, source: str = SOURCE_VIEW) -> str:
    """Idempotent: an unchanged row is not touched, so a rerun adds nothing to the change feed.

    <=> because revision, paragraph and heading can be null, and null = null is not true.
    """
    unchanged = " AND ".join(
        f"target.`{name}` <=> source.`{name}`" for name in GOLD_COLUMNS if name != MERGE_KEY
    )
    return (
        f"MERGE INTO {table} AS target USING {source} AS source "
        f"ON target.`{MERGE_KEY}` = source.`{MERGE_KEY}` "
        f"WHEN MATCHED AND NOT ({unchanged}) THEN UPDATE SET * "
        "WHEN NOT MATCHED THEN INSERT * "
        "WHEN NOT MATCHED BY SOURCE THEN DELETE"
    )


def write_silver(spark: Any, table: str, provisions: list[Provision]) -> None:
    rows = to_rows(provisions, SILVER_COLUMNS)
    frame = spark.createDataFrame(rows, schema=spark_schema(SILVER_COLUMNS))
    frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(
        table
    )


def merge_gold(spark: Any, table: str, chunks: list[Chunk]) -> dict[str, int]:
    """Returns the MERGE metrics from the statement's own result row."""
    spark.sql(create_gold_sql(table))
    rows = to_rows(chunks, GOLD_COLUMNS)
    frame = spark.createDataFrame(rows, schema=spark_schema(GOLD_COLUMNS))
    frame.createOrReplaceTempView(SOURCE_VIEW)
    result = spark.sql(merge_gold_sql(table)).first()
    return {
        "inserted": result["num_inserted_rows"],
        "updated": result["num_updated_rows"],
        "deleted": result["num_deleted_rows"],
    }


def run(spark: Any, catalog: str, schema: str, raw: Path) -> None:
    builds: list[EntryBuild] = build_corpus(load_manifest(), raw)
    print(format_table(table_rows(builds)))

    provisions = [p for build in builds for p in build.provisions]
    chunks = [c for build in builds for c in build.chunks]
    if not chunks:  # The MERGE would delete all of gold.
        raise ValueError(f"no chunks built from {raw}: refusing to write")

    silver = table_name(catalog, schema, "silver_provisions")
    gold = table_name(catalog, schema, "gold_chunks")
    write_silver(spark, silver, provisions)
    metrics = merge_gold(spark, gold, chunks)

    print(f"{silver}: {spark.table(silver).count()} rows")
    print(f"{gold}: {spark.table(gold).count()} rows")
    print(
        f"MERGE {gold}: inserted={metrics['inserted']} updated={metrics['updated']} "
        f"deleted={metrics['deleted']}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    args = parser.parse_args(argv)

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    run(spark, args.catalog, args.schema, raw_root(args.catalog, args.schema))


if __name__ == "__main__":
    main()
