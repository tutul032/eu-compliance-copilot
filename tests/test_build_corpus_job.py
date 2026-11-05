"""The Spark side with a mock session: which statements run, in which order, with which data.

Whether Delta accepts them is only proven by running the job on Databricks.
"""

import shutil
from dataclasses import fields
from datetime import date
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import pytest
from conftest import FIXTURES, fixture_bytes, make_entry

from eucc.chunk import Chunk, chunk_provision
from eucc.contract import ContractError
from eucc.corpus.manifest import Manifest
from eucc.extract import output_path
from eucc.jobs import build_corpus as job
from eucc.parse import Provision, parse_entry

GOLD = "`workspace`.`compliance_dev`.`gold_chunks`"
SILVER = "`workspace`.`compliance_dev`.`silver_provisions`"


def _spark(inserted: int = 5, updated: int = 2, deleted: int = 1) -> MagicMock:
    spark = MagicMock()
    spark.sql.return_value.first.return_value = {
        "num_affected_rows": inserted + updated + deleted,
        "num_inserted_rows": inserted,
        "num_updated_rows": updated,
        "num_deleted_rows": deleted,
    }
    spark.table.return_value.count.return_value = 7
    return spark


def _raw_and_manifest(tmp_path: Path, monkeypatch, articles: int = 3) -> Path:
    entries = []
    for lang in ("en", "de"):
        entry = make_entry(
            "consolidated_gdpr",
            lang,
            id=f"gdpr-consolidated-{lang}",
            expected_units={"articles": articles, "recitals": 0, "annexes": 0},
        )
        path = output_path(tmp_path, entry)
        path.parent.mkdir(parents=True)
        shutil.copy(FIXTURES / f"consolidated_gdpr_{lang}.xhtml", path)
        entries.append(entry)
    monkeypatch.setattr(job, "load_manifest", lambda: Manifest(entries=entries))
    return tmp_path


def _sql(spark: MagicMock) -> list[str]:
    return [call.args[0] for call in spark.sql.call_args_list]


# Schemas


def test_silver_schema_matches_the_provision_row():
    assert list(job.SILVER_COLUMNS) == [f.name for f in fields(Provision)]


def test_gold_schema_matches_the_chunk_row():
    assert list(job.GOLD_COLUMNS) == [f.name for f in fields(Chunk)]


def test_schema_is_explicit_ddl():
    schema = job.spark_schema({"a": "STRING NOT NULL", "b": "ARRAY<STRING> NOT NULL"})
    assert schema == "`a` STRING NOT NULL, `b` ARRAY<STRING> NOT NULL"


def test_rows_follow_the_schema_and_convert_the_date(lang):
    entry = make_entry("consolidated_gdpr", lang)
    provision = parse_entry(entry, fixture_bytes("consolidated_gdpr", lang))[0]
    row = job.to_rows([provision], job.SILVER_COLUMNS)[0]
    assert len(row) == len(job.SILVER_COLUMNS)
    assert row[0] == provision.provision_id
    assert row[list(job.SILVER_COLUMNS).index("version_date")] == date(2016, 5, 4)
    assert row[list(job.SILVER_COLUMNS).index("amended_by")] == provision.amended_by


# SQL


def test_gold_is_created_with_change_data_feed():
    sql = job.create_gold_sql(GOLD)
    assert sql.startswith(f"CREATE TABLE IF NOT EXISTS {GOLD} (")
    assert "delta.enableChangeDataFeed = true" in sql


def test_merge_has_the_three_clauses_on_chunk_id():
    sql = job.merge_gold_sql(GOLD)
    assert f"MERGE INTO {GOLD} AS target USING gold_chunks_source AS source" in sql
    assert "ON target.`chunk_id` = source.`chunk_id`" in sql
    assert "THEN UPDATE SET *" in sql
    assert "WHEN NOT MATCHED THEN INSERT *" in sql
    assert "WHEN NOT MATCHED BY SOURCE THEN DELETE" in sql


def test_merge_updates_only_changed_rows_with_null_safe_comparison():
    condition = job.merge_gold_sql(GOLD).split("WHEN MATCHED AND NOT (")[1].split(") THEN")[0]
    for name in job.GOLD_COLUMNS:
        if name != "chunk_id":
            assert f"target.`{name}` <=> source.`{name}`" in condition
    assert "`chunk_id`" not in condition


# The run


def test_writes_silver_and_merges_gold(tmp_path, monkeypatch, capsys):
    spark = _spark()
    raw = _raw_and_manifest(tmp_path, monkeypatch)

    job.run(spark, "workspace", "compliance_dev", raw)

    writer = spark.createDataFrame.return_value.write.format.return_value.mode.return_value
    spark.createDataFrame.return_value.write.format.assert_called_once_with("delta")
    writer.option.return_value.saveAsTable.assert_called_once_with(SILVER)
    spark.createDataFrame.return_value.createOrReplaceTempView.assert_called_once_with(
        job.SOURCE_VIEW
    )
    create, merge = _sql(spark)
    assert create.startswith("CREATE TABLE IF NOT EXISTS")
    assert merge.startswith("MERGE INTO")


def test_all_chunks_of_every_entry_reach_the_merge(tmp_path, monkeypatch):
    spark = _spark()
    raw = _raw_and_manifest(tmp_path, monkeypatch)

    job.run(spark, "workspace", "compliance_dev", raw)

    gold_rows = spark.createDataFrame.call_args_list[1].args[0]
    expected = [
        chunk.chunk_id
        for lang in ("en", "de")
        for provision in parse_entry(
            make_entry("consolidated_gdpr", lang), fixture_bytes("consolidated_gdpr", lang)
        )
        for chunk in chunk_provision(provision)
    ]
    assert [row[0] for row in gold_rows] == expected


def test_prints_table_counts_and_merge_metrics(tmp_path, monkeypatch, capsys):
    raw = _raw_and_manifest(tmp_path, monkeypatch)

    job.run(_spark(inserted=5, updated=2, deleted=1), "workspace", "compliance_dev", raw)

    out = capsys.readouterr().out
    assert "gdpr-consolidated-en" in out
    assert f"{SILVER}: 7 rows" in out
    assert f"{GOLD}: 7 rows" in out
    assert "inserted=5 updated=2 deleted=1" in out


def test_contract_failure_writes_nothing(tmp_path, monkeypatch):
    spark = _spark()
    raw = _raw_and_manifest(tmp_path, monkeypatch, articles=4)

    with pytest.raises(ContractError, match="gdpr-consolidated-en: expected 4 articles"):
        job.run(spark, "workspace", "compliance_dev", raw)

    assert spark.mock_calls == []


def test_missing_raw_file_writes_nothing(tmp_path, monkeypatch):
    spark = _spark()
    raw = _raw_and_manifest(tmp_path, monkeypatch)
    shutil.rmtree(raw / "02016R0679-20160504")

    with pytest.raises(ContractError, match="source file not found"):
        job.run(spark, "workspace", "compliance_dev", raw)

    assert spark.mock_calls == []


def test_empty_build_never_reaches_the_delete_clause(tmp_path, monkeypatch):
    spark = _spark()
    monkeypatch.setattr(job, "load_manifest", lambda: Manifest(entries=[]))

    with pytest.raises(ValueError, match="refusing to write"):
        job.run(spark, "workspace", "compliance_dev", tmp_path)

    assert spark.mock_calls == []


def test_raw_root_is_the_volume_of_the_schema():
    root = job.raw_root("workspace", "compliance_staging")
    assert PurePosixPath(root.as_posix()) == PurePosixPath(
        "/Volumes/workspace/compliance_staging/raw/eurlex"
    )


@pytest.mark.parametrize("missing", ["--catalog", "--schema"])
def test_parameters_are_required(missing):
    args = ["--catalog", "workspace", "--schema", "compliance_dev"]
    position = args.index(missing)
    del args[position : position + 2]
    with pytest.raises(SystemExit):
        job.main(args)
