"""Databricks job task: create or sync the vector search index over gold_chunks, wait until done.

    index_job --catalog workspace --schema compliance_dev

Runs after build_corpus in the same job. Creating the index starts its first sync; an existing
index gets a triggered sync. Done means ready, no update pending and as many indexed rows as
gold_chunks has; anything else after the timeout fails the task. pyspark and the vector search
client are imported in main only, so the waiting logic runs in unit tests.
"""

import argparse
import time
from collections.abc import Callable
from typing import Any

from eucc.index import (
    EMBEDDING_MODEL,
    EMBEDDING_SOURCE_COLUMN,
    ENDPOINT,
    INDEX_TABLE,
    PIPELINE_TYPE,
    PRIMARY_KEY,
    SOURCE_TABLE,
    SYNC_COLUMNS,
    IndexStatus,
    full_name,
    index_status,
    vector_search_client,
)

TIMEOUT_SECONDS = 30 * 60
POLL_SECONDS = 30


class IndexSyncError(Exception):
    """The index failed or did not catch up with the table in time."""


def wait_for_sync(
    describe: Callable[[], dict[str, Any]],
    table_rows: int,
    timeout: float = TIMEOUT_SECONDS,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> IndexStatus:
    """Polls until the index is in sync with table_rows; raises on failure or timeout.

    The first check comes after one poll interval: right after sync() the index can still report
    the settled state of the previous sync.
    """
    deadline = clock() + timeout
    while True:
        sleep(poll)
        status = index_status(describe())
        print(f"index state={status.state or '?'} indexed={status.indexed_rows}/{table_rows}")
        if status.failed:
            raise IndexSyncError(f"index failed ({status.state}): {status.message}")
        if status.synced_with(table_rows):
            return status
        if clock() >= deadline:
            raise IndexSyncError(
                f"index not in sync after {timeout / 60:.0f} min: state={status.state}, "
                f"indexed {status.indexed_rows} of {table_rows} rows"
            )


def create_or_sync(client: Any, catalog: str, schema: str) -> Any:
    """The index, created if missing, otherwise asked to sync."""
    name = full_name(catalog, schema, INDEX_TABLE)
    if not client.index_exists(endpoint_name=ENDPOINT, index_name=name):
        print(f"creating {name} on {ENDPOINT}")
        return client.create_delta_sync_index(
            endpoint_name=ENDPOINT,
            index_name=name,
            source_table_name=full_name(catalog, schema, SOURCE_TABLE),
            primary_key=PRIMARY_KEY,
            pipeline_type=PIPELINE_TYPE,
            embedding_source_column=EMBEDDING_SOURCE_COLUMN,
            embedding_model_endpoint_name=EMBEDDING_MODEL,
            columns_to_sync=SYNC_COLUMNS,
        )
    index = client.get_index(endpoint_name=ENDPOINT, index_name=name)
    if index_status(index.describe()).ready:
        print(f"syncing {name}")
        index.sync()
    else:
        # Still provisioning from an earlier run: its own sync is pending, a second one is refused.
        print(f"{name} is not ready yet; waiting for its pending sync")
    return index


def run(
    spark: Any, client: Any, catalog: str, schema: str, timeout: float, poll: float = POLL_SECONDS
) -> IndexStatus:
    table_rows = spark.table(f"`{catalog}`.`{schema}`.`{SOURCE_TABLE}`").count()
    index = create_or_sync(client, catalog, schema)
    status = wait_for_sync(index.describe, table_rows, timeout=timeout, poll=poll)
    print(f"{full_name(catalog, schema, SOURCE_TABLE)}: {table_rows} rows")
    print(f"{full_name(catalog, schema, INDEX_TABLE)}: {status.indexed_rows} indexed rows")
    return status


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--timeout-minutes", type=float, default=TIMEOUT_SECONDS / 60)
    args = parser.parse_args(argv)

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    run(spark, vector_search_client(), args.catalog, args.schema, args.timeout_minutes * 60)


if __name__ == "__main__":
    main()
