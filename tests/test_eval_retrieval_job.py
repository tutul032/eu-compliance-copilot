"""The eval job with a fake index and a fake mlflow: what is queried and what is logged."""

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from eucc.eval.questions import QuestionSet
from eucc.jobs import eval_retrieval as job

QUESTIONS = QuestionSet.model_validate(
    {
        "version": "0.1",
        "questions": [
            {
                "id": "gdpr-02",
                "lang": "en",
                "category": "gdpr",
                "question": "How fast must a breach be notified?",
                "expected_behavior": "answer",
                "expected_sources": [{"reg": "GDPR", "article": "33"}],
            },
            {
                "id": "gdpr-03",
                "lang": "de",
                "category": "gdpr",
                "question": "Wann muss eine Datenpanne mitgeteilt werden?",
                "expected_behavior": "answer",
                "expected_sources": [{"reg": "GDPR", "article": "34"}],
            },
            {
                "id": "tool-01",
                "lang": "en",
                "category": "tool",
                "question": "Run the tool.",
                "expected_behavior": "tool",
            },
        ],
    }
)
COLUMNS = ["chunk_id", "reg", "lang", "unit_type", "article", "paragraph", "score"]


def _response(*rows: tuple[str, str, str]) -> dict:
    """rows: (chunk_id, lang, article) of GDPR article chunks."""
    return {
        "manifest": {"columns": [{"name": name} for name in COLUMNS]},
        "result": {
            "row_count": len(rows),
            "data_array": [
                [cid, "GDPR", lang, "article", art, "1", 0.5] for cid, lang, art in rows
            ],
        },
    }


class _FakeMlflow:
    def __init__(self) -> None:
        self.runs: list[dict] = []

    def start_run(self, run_name: str):
        self.runs.append({"name": run_name, "params": {}, "metrics": {}, "csv": None})
        return MagicMock()

    def log_params(self, params: dict) -> None:
        self.runs[-1]["params"] = params

    def log_metrics(self, metrics: dict) -> None:
        self.runs[-1]["metrics"] = metrics

    def log_artifact(self, path: str) -> None:
        with Path(path).open(encoding="utf-8", newline="") as f:
            self.runs[-1]["csv"] = (Path(path).name, list(csv.DictReader(f)))


def _client(ready: bool = True) -> MagicMock:
    client = MagicMock()
    index = client.get_index.return_value
    index.describe.return_value = {
        "status": {
            "ready": ready,
            "detailed_state": "ONLINE_NO_PENDING_UPDATE",
            "indexed_row_count": 3704,
        },
        "delta_sync_index_spec": {
            "embedding_source_columns": [
                {
                    "name": "chunk_text",
                    "embedding_model_endpoint_name": "databricks-qwen3-embedding-0-6b",
                }
            ]
        },
    }

    def search(query_text, columns, num_results, query_type, filters, **_):
        # The German question finds Art. 34 only in German chunks, i.e. with a lang filter.
        if query_text.startswith("Wann"):
            if filters and filters["lang"] == "de":
                return _response(("gdpr-de-article-34-1", "de", "34"))
            return _response(("gdpr-en-article-33-1", "en", "33"))
        return _response(("gdpr-en-article-33-1", "en", "33"), ("gdpr-de-article-33-1", "de", "33"))

    index.similarity_search.side_effect = search
    return client


@pytest.fixture(autouse=True)
def _questions(monkeypatch):
    monkeypatch.setattr(job, "load_questions", lambda: QUESTIONS)


def test_one_run_per_variant_with_params_metrics_and_csv():
    mlflow = _FakeMlflow()
    job.run(_client(), mlflow, "workspace", "compliance_dev")

    assert [run["name"] for run in mlflow.runs] == [
        "retrieval_no_filter",
        "retrieval_lang_filter",
        "retrieval_lang_articles",
    ]
    no_filter, lang_filter, lang_articles = mlflow.runs
    assert no_filter["params"] == {
        "index": "workspace.compliance_dev.chunks_idx",
        "embedding_model": "databricks-qwen3-embedding-0-6b",
        "query_type": "HYBRID",
        "num_results": 10,
        "chunk_count": 3704,
        "question_count": 2,
        "filter": "none",
    }
    assert lang_filter["params"]["filter"] == "lang=<question lang>"
    assert lang_articles["params"]["filter"] == (
        "lang=<question lang>, unit_type in (article, annex)"
    )
    assert no_filter["metrics"]["recall_at_5"] == 0.5
    assert no_filter["metrics"]["recall_at_10"] == 0.5
    assert no_filter["metrics"]["recall_at_5_lang_de"] == 0.0
    assert lang_filter["metrics"]["recall_at_5"] == 1.0
    assert lang_articles["metrics"]["recall_at_10"] == 1.0
    name, rows = lang_filter["csv"]
    assert name == "per_question.csv"
    assert [(row["id"], row["hit_at_5"], row["hit_at_10"]) for row in rows] == [
        ("gdpr-02", "True", "True"),
        ("gdpr-03", "True", "True"),
    ]
    assert rows[0]["top10_chunk_ids"] == "gdpr-en-article-33-1 gdpr-de-article-33-1"


def test_queries_are_hybrid_top_ten_one_per_question_and_variant():
    client = _client()
    job.run(client, _FakeMlflow(), "workspace", "compliance_dev")
    calls = client.get_index.return_value.similarity_search.call_args_list
    assert len(calls) == 6  # 2 questions with sources x 3 variants; @5 and @10 share a query.
    assert {(c.kwargs["query_type"], c.kwargs["num_results"]) for c in calls} == {("HYBRID", 10)}
    assert [c.kwargs["filters"] for c in calls] == [
        None,
        None,
        {"lang": "en"},
        {"lang": "de"},
        {"lang": "en", "unit_type": ["article", "annex"]},
        {"lang": "de", "unit_type": ["article", "annex"]},
    ]
    assert {"reg", "unit_type", "article"} <= set(calls[0].kwargs["columns"])


def test_index_not_ready_logs_nothing():
    mlflow = _FakeMlflow()
    with pytest.raises(job.IndexNotReadyError):
        job.run(_client(ready=False), mlflow, "workspace", "compliance_dev")
    assert mlflow.runs == []


@pytest.mark.parametrize("missing", ["--catalog", "--schema", "--experiment"])
def test_parameters_are_required(missing):
    args = ["--catalog", "workspace", "--schema", "compliance_dev", "--experiment", "/Users/x/e"]
    position = args.index(missing)
    del args[position : position + 2]
    with pytest.raises(SystemExit):
        job.main(args)
