"""Index status and the create/sync/wait task, with fake clients: no network, no Spark."""

from unittest.mock import MagicMock

import pytest

from eucc import index as idx
from eucc.jobs import index as job


def _description(ready=True, state="ONLINE_NO_PENDING_UPDATE", rows=10, message="") -> dict:
    return {
        "status": {
            "ready": ready,
            "detailed_state": state,
            "indexed_row_count": rows,
            "message": message,
        },
        "delta_sync_index_spec": {
            "embedding_source_columns": [
                {"name": "chunk_text", "embedding_model_endpoint_name": "model-x"}
            ]
        },
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def _wait(descriptions: list[dict], table_rows: int = 10, timeout: float = 300):
    clock = _Clock()
    responses = iter(descriptions)
    return job.wait_for_sync(
        lambda: next(responses),
        table_rows,
        timeout=timeout,
        poll=30,
        sleep=clock.sleep,
        clock=clock,
    )


# Status


def test_status_reads_the_describe_response():
    status = idx.index_status(_description(rows=7, message="ok"))
    assert status == idx.IndexStatus(True, "ONLINE_NO_PENDING_UPDATE", 7, "ok")


def test_status_of_a_new_index_without_counts():
    status = idx.index_status({"status": {"ready": False, "detailed_state": "PROVISIONING"}})
    assert (status.ready, status.indexed_rows) == (False, 0)


@pytest.mark.parametrize(
    ("description", "synced"),
    [
        (_description(), True),
        (_description(state="ONLINE"), True),
        (_description(rows=9), False),
        (_description(ready=False), False),
        # Ready and the right count, but an update is still running: rows may be stale.
        (_description(state="ONLINE_TRIGGERED_UPDATE"), False),
        (_description(state="ONLINE_UPDATING_PIPELINE_RESOURCES"), False),
    ],
)
def test_synced_needs_ready_settled_and_every_row(description, synced):
    assert idx.index_status(description).synced_with(10) is synced


def test_embedding_model_comes_from_the_index():
    assert idx.embedding_model(_description()) == "model-x"
    assert idx.embedding_model({}) == "unknown"


def test_names_are_unquoted_three_part_names():
    assert idx.full_name("workspace", "compliance_dev", "chunks_idx") == (
        "workspace.compliance_dev.chunks_idx"
    )


def test_sync_columns_hold_the_key_the_embedded_text_and_the_filter():
    assert {idx.PRIMARY_KEY, idx.EMBEDDING_SOURCE_COLUMN, "lang"} <= set(idx.SYNC_COLUMNS)


def test_sync_columns_exist_in_gold_chunks():
    from eucc.jobs.build_corpus import GOLD_COLUMNS

    assert set(idx.SYNC_COLUMNS) <= set(GOLD_COLUMNS)


# Waiting


def test_wait_returns_once_in_sync():
    status = _wait(
        [
            _description(ready=False, state="PROVISIONING_INITIAL_SNAPSHOT", rows=0),
            _description(state="ONLINE_TRIGGERED_UPDATE", rows=4),
            _description(rows=10),
        ]
    )
    assert status.indexed_rows == 10


def test_wait_checks_only_after_one_interval():
    # Right after sync() the previous, settled status can still be reported.
    clock = _Clock()
    job.wait_for_sync(lambda: _description(), 10, poll=30, sleep=clock.sleep, clock=clock)
    assert clock.now == 30


def test_wait_fails_on_a_failed_index():
    with pytest.raises(job.IndexSyncError, match="OFFLINE_FAILED.*embedding endpoint down"):
        _wait([_description(state="OFFLINE_FAILED", message="embedding endpoint down")])


def test_wait_times_out_with_both_counts():
    with pytest.raises(job.IndexSyncError, match="indexed 9 of 10 rows"):
        _wait([_description(rows=9)] * 20, timeout=90)


# Create or sync


def _client(exists: bool, ready: bool = True) -> MagicMock:
    client = MagicMock()
    client.index_exists.return_value = exists
    client.get_index.return_value.describe.return_value = _description(ready=ready)
    return client


def test_missing_index_is_created_with_the_spec():
    client = _client(exists=False)
    job.create_or_sync(client, "workspace", "compliance_dev")
    client.create_delta_sync_index.assert_called_once_with(
        endpoint_name="eucc-vs",
        index_name="workspace.compliance_dev.chunks_idx",
        source_table_name="workspace.compliance_dev.gold_chunks",
        primary_key="chunk_id",
        pipeline_type="TRIGGERED",
        embedding_source_column="chunk_text",
        embedding_model_endpoint_name="databricks-qwen3-embedding-0-6b",
        columns_to_sync=idx.SYNC_COLUMNS,
    )
    client.get_index.return_value.sync.assert_not_called()


def test_existing_index_is_synced():
    client = _client(exists=True)
    job.create_or_sync(client, "workspace", "compliance_dev")
    client.get_index.return_value.sync.assert_called_once_with()
    client.create_delta_sync_index.assert_not_called()


def test_index_still_provisioning_is_not_synced_again():
    client = _client(exists=True, ready=False)
    job.create_or_sync(client, "workspace", "compliance_dev")
    client.get_index.return_value.sync.assert_not_called()


def test_run_prints_both_counts(capsys):
    spark = MagicMock()
    spark.table.return_value.count.return_value = 10
    client = _client(exists=True)

    job.run(spark, client, "workspace", "compliance_dev", timeout=60, poll=0)

    spark.table.assert_called_once_with("`workspace`.`compliance_dev`.`gold_chunks`")
    out = capsys.readouterr().out
    assert "workspace.compliance_dev.gold_chunks: 10 rows" in out
    assert "workspace.compliance_dev.chunks_idx: 10 indexed rows" in out
