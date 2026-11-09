"""Agent eval: route and data scoring, and the job with fakes (no network, fake mlflow)."""

import pytest
from test_eval_rag_job import _FakeMlflow

from eucc.eval import agent as agent_eval
from eucc.eval.questions import Question, QuestionSet, load_questions
from eucc.jobs import eval_agent as job


def _q(qid, behavior, **extra) -> Question:
    sources = (
        None if behavior in ("tool", "refuse", "out_of_scope") else [{"reg": "AIA", "article": "6"}]
    )
    return Question.model_validate(
        {
            "id": qid,
            "lang": "en",
            "category": "tool" if "tool" in behavior else "ai_act",
            "question": f"Question {qid}?",
            "expected_behavior": behavior,
            "expected_sources": sources,
            **extra,
        }
    )


def _route(regulation, data):
    return {"needs_regulation": regulation, "needs_data": data, "method": "llm"}


@pytest.mark.parametrize(
    ("behavior", "route", "correct"),
    [
        ("tool", _route(False, True), True),
        ("tool", _route(True, True), False),
        ("answer+tool", _route(True, True), True),
        ("answer+tool", _route(False, True), False),
        ("answer", _route(True, False), True),
        ("refuse", _route(False, False), True),  # No data: fine.
        ("answer", _route(True, True), False),
        ("answer", None, False),  # The agent crashed.
    ],
)
def test_route_correct(behavior, route, correct):
    assert agent_eval.route_correct(_q("x", behavior), route) is correct


def test_repo_tool_questions_have_reference_sql():
    by_id = {q.id: q for q in load_questions().questions}
    for qid in ("tool-01", "tool-02", "tool-03", "tool-04"):
        assert "workspace.energy_prod." in by_id[qid].reference_sql
    assert sum(q.reference_sql is not None for q in by_id.values()) == 4


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The average was 242.13 EUR/MWh [D1].", [242.13]),
        ("Der Durchschnitt lag bei 242,13 EUR/MWh 【D1】.", [242.13]),
        ("Load was 1,234.5 MWh", [1234.5]),
        ("Last: 1.234,5 MWh", [1234.5]),
        ("Price −5.2 EUR/MWh", [-5.2]),
        ("between 2.4-3.2 %", [2.4, 3.2]),
    ],
)
def test_answer_numbers(text, expected):
    assert set(expected) <= set(agent_eval.answer_numbers(text))


def test_data_correct_rounds_to_one_decimal_and_ignores_zeros():
    reference = agent_eval.reference_numbers(
        {"rows": [["2026-11-08", 242.13041, 0], ["2026-11-07", 3.24, True]]}
    )
    assert reference == [242.13041, 3.24]
    assert agent_eval.data_correct(reference, [242.1, 3.2, 2026.0]) is True
    assert agent_eval.data_correct(reference, [242.0, 3.2]) is False
    assert agent_eval.data_correct([], [1.0]) is None


QUESTIONS = QuestionSet.model_validate(
    {
        "version": "0.1",
        "questions": [
            _q("aia-03", "answer", key_facts=["Annex III point 2"]).model_dump(),
            _q("tool-01", "tool", reference_sql="SELECT 1").model_dump(),
            _q("tool-04", "answer+tool", reference_sql="SELECT 2", key_facts=["MAPE"]).model_dump(),
        ],
    }
)


def _answer(question):
    citation = {
        "n": 1,
        "chunk_id": "aia-en-article-6-2",
        "chunk_ids": ["aia-en-article-6-2"],
        "reg": "AIA",
        "lang": "en",
        "unit_type": "article",
        "article": "6",
    }
    if question.startswith("Question tool-01"):
        return {
            "status": "answered",
            "answer": "The average price was 242.13 EUR/MWh [D1].",
            "citations": [],
            "data_citations": [{"n": "D1", "row": {}}],
            "route": _route(False, True),
            "sql": "SELECT ...",
            "data": {"columns": ["p"], "rows": [[242.13]]},
            "latency_ms": 900,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    if question.startswith("Question tool-04"):
        return {
            "status": "answered",
            "answer": "High-risk if a safety component [1]; current MAPE 2.4 % [D1].",
            "citations": [citation],
            "route": _route(True, False),  # Wrong: should be both.
            "sql": None,
            "data": None,
            "latency_ms": 1500,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    return {
        "status": "answered",
        "answer": "Possibly [1].",
        "citations": [citation],
        "route": _route(True, False),
        "latency_ms": 1000,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_job_logs_route_data_and_rag_metrics(monkeypatch):
    monkeypatch.setattr(job, "load_questions", lambda: QUESTIONS)
    references = {"SELECT 1": [[242.1304]], "SELECT 2": [["2026-11-08", 2.36]]}
    mlflow = _FakeMlflow()
    metrics = job.run(
        mlflow,
        "workspace",
        "compliance_dev",
        answer=_answer,
        reference=lambda s: {"columns": ["a"], "rows": references[s]},
        judge=lambda q, facts, a: (list(facts), 7),
    )
    (logged,) = mlflow.runs
    assert logged["name"] == "agent_medium"
    assert metrics["route_correct"] == pytest.approx(2 / 3)
    assert metrics["data_correct"] == 1.0
    assert metrics["question_count"] == 2  # The eval_rag metrics: tool-only questions left out.
    assert metrics["citation_hit"] == 1.0
    assert {"system_prompt.txt", "router_system_prompt.txt", "sql_system_prompt.txt"} <= set(
        logged["texts"]
    )
    name, rows = logged["csv"]
    assert name == "per_question.csv"
    tool01 = next(r for r in rows if r["id"] == "tool-01")
    assert tool01["route"] == "data" and tool01["route_correct"] == "True"
    assert tool01["reference_numbers"] == "242.13" and "242.13" in tool01["agent_numbers"]
    assert tool01["data_correct"] == "True" and tool01["sql"] == "SELECT ..."
    tool04 = next(r for r in rows if r["id"] == "tool-04")
    assert tool04["route"] == "regulation" and tool04["route_correct"] == "False"


def test_job_survives_a_crashing_agent(monkeypatch):
    monkeypatch.setattr(job, "load_questions", lambda: QUESTIONS)

    def answer(question):
        raise TimeoutError("endpoint")

    metrics = job.run(
        _FakeMlflow(),
        "workspace",
        "compliance_dev",
        answer=answer,
        reference=lambda s: {"rows": [[1.5]]},
        judge=lambda q, facts, a: ([], 0),
    )
    assert metrics["route_correct"] == 0.0 and metrics["data_correct"] == 0.0
    assert metrics["error_count"] == 2


def test_data_correct_any_of_the_reference_values():
    reference = [1355.99, 2.36]  # MAE (MWh) and MAPE (%) of the latest issue date.
    assert agent_eval.data_correct(reference, [2.4], "any") is True  # Stated as MAPE only.
    assert agent_eval.data_correct(reference, [1356.0], "any") is True  # As MAE only.
    assert agent_eval.data_correct(reference, [2.4], "all") is False
    assert agent_eval.data_correct(reference, [5.0, 1200.0], "any") is False
    assert agent_eval.data_correct([], [2.4], "any") is None
    with pytest.raises(ValueError, match="data_match"):
        agent_eval.data_correct(reference, [2.4], "most")


def test_tool_04_reference_has_mae_and_mape_and_matches_any():
    tool04 = next(q for q in load_questions().questions if q.id == "tool-04")
    assert "ROUND(mae, 2)" in tool04.reference_sql and "ROUND(mape, 2)" in tool04.reference_sql
    assert "ORDER BY issue_date DESC" in tool04.reference_sql and "LIMIT 1" in tool04.reference_sql
    assert tool04.data_match == "any"
    others = [q for q in load_questions().questions if q.reference_sql and q.id != "tool-04"]
    assert {q.data_match for q in others} == {"all"}


def test_job_applies_the_question_data_match(monkeypatch):
    question = _q("tool-04", "answer+tool", reference_sql="SELECT 2", data_match="any")
    monkeypatch.setattr(
        job, "load_questions", lambda: QuestionSet(version="0.1", questions=[question])
    )
    metrics = job.run(
        _FakeMlflow(),
        "workspace",
        "compliance_dev",
        answer=lambda q: {**_answer("Question tool-04?"), "route": _route(True, True)},
        reference=lambda s: {"rows": [["2026-11-08", 1355.99, 2.36]]},
        judge=lambda q, facts, a: ([], 0),
    )
    assert metrics["data_correct"] == 1.0  # The answer gives only the MAPE (2.4 %).
