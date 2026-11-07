"""The RAG eval job with fake answers, a fake judge and a fake mlflow: what is logged."""

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from eucc.eval.questions import QuestionSet
from eucc.jobs import eval_rag as job

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
                "key_facts": ["72 hours"],
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


class _FakeMlflow:
    def __init__(self) -> None:
        self.runs: list[dict] = []

    def start_run(self, run_name: str):
        self.runs.append({"name": run_name, "params": {}, "metrics": {}, "texts": {}, "csv": None})
        return MagicMock()

    def log_params(self, params: dict) -> None:
        self.runs[-1]["params"] = params

    def log_metrics(self, metrics: dict) -> None:
        self.runs[-1]["metrics"] = metrics

    def log_text(self, text: str, artifact_file: str) -> None:
        self.runs[-1]["texts"][artifact_file] = text

    def log_artifact(self, path: str) -> None:
        with Path(path).open(encoding="utf-8", newline="") as f:
            self.runs[-1]["csv"] = (Path(path).name, list(csv.DictReader(f)))


@pytest.fixture(autouse=True)
def _questions(monkeypatch):
    monkeypatch.setattr(job, "load_questions", lambda: QUESTIONS)


def test_one_run_per_reasoning_effort_with_params_metrics_prompts_and_csv():
    asked = []

    def answer(question, reasoning_effort):
        asked.append((question, reasoning_effort))
        return {
            "status": "answered",
            "answer": "Within 72 hours [1].",
            "citations": [
                {
                    "n": 1,
                    "chunk_id": "gdpr-en-article-33-1",
                    "reg": "GDPR",
                    "lang": "en",
                    "unit_type": "article",
                    "article": "33",
                }
            ],
            "latency_ms": 1200 if reasoning_effort == "medium" else 600,
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "context_tokens": 100,
        }

    mlflow = _FakeMlflow()
    by_effort = job.run(mlflow, "workspace", "compliance_dev", answer, lambda q, f, a: (list(f), 4))

    question = "How fast must a breach be notified?"
    assert asked == [(question, "medium"), (question, "low")]  # tool-01 skipped.
    assert [run["name"] for run in mlflow.runs] == ["rag_medium", "rag_low"]
    medium, low = mlflow.runs
    assert medium["params"]["reasoning_effort"] == "medium"
    assert low["params"]["reasoning_effort"] == "low"
    assert medium["params"]["index"] == "workspace.compliance_dev.chunks_idx"
    assert medium["params"]["model"] == "databricks-gpt-oss-120b"
    assert medium["params"]["max_tokens"] == 1500
    assert medium["params"]["question_count"] == 1
    assert medium["metrics"] == by_effort["medium"]
    assert low["metrics"] == by_effort["low"]
    metrics = by_effort["medium"]
    assert metrics["citation_hit"] == 1.0
    assert metrics["behavior_correct"] == 1.0
    assert metrics["key_fact_coverage"] == 1.0
    assert metrics["max_tokens_in_context"] == 100
    assert metrics["p95_latency_ms"] == 1200
    assert by_effort["low"]["p95_latency_ms"] == 600
    assert set(medium["texts"]) == {"system_prompt.txt", "judge_system_prompt.txt"}
    name, rows = low["csv"]
    assert name == "per_question.csv"
    assert [(r["id"], r["status"], r["citation_hit"]) for r in rows] == [
        ("gdpr-02", "answered", "True")
    ]


def test_default_answer_passes_catalog_schema_and_effort(monkeypatch):
    calls = []

    def answer(question, **kwargs):
        calls.append(kwargs)
        return {"status": "answered", "answer": "x [1]", "citations": [], "latency_ms": 1}

    monkeypatch.setattr(job.rag, "answer", answer)
    job.run(_FakeMlflow(), "workspace", "s", judge=lambda q, f, a: ([], 0))
    assert calls == [
        {"catalog": "workspace", "schema": "s", "reasoning_effort": "medium"},
        {"catalog": "workspace", "schema": "s", "reasoning_effort": "low"},
    ]


def test_llm_judge_uses_the_chat_model(monkeypatch):
    sent = []

    def chat(messages):
        sent.append(messages)
        return '{"present": [2]}', {"total_tokens": 42}

    monkeypatch.setattr(job.rag, "chat", chat)
    assert job.llm_judge("Q?", ["a", "b"], "answer") == (["b"], 42)
    assert "2. b" in sent[0][1]["content"]


@pytest.mark.parametrize("missing", ["--catalog", "--schema", "--experiment"])
def test_parameters_are_required(missing):
    args = ["--catalog", "workspace", "--schema", "compliance_dev", "--experiment", "/Users/x/e"]
    position = args.index(missing)
    del args[position : position + 2]
    with pytest.raises(SystemExit):
        job.main(args)
