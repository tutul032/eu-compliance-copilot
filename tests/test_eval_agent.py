"""Agent eval: route and data scoring, and the job with fakes (no network, fake mlflow)."""

import sys
import types

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


# ---------------------------------------------------------------- release evaluation

RELEASE = job.Thresholds(min_behavior=1.0, min_citation_hit=0.85, min_key_facts=0.80, max_errors=0)
PASSING = {
    "behavior_correct": 1.0,
    "citation_hit": 0.9,
    "key_fact_coverage": 0.85,
    "p95_latency_ms": 8123.4,
    "error_count": 0,
    "question_count": 36,
}


def test_thresholds_pass():
    assert job.check_thresholds(PASSING, RELEASE) == []


def test_thresholds_name_every_missed_gate():
    metrics = {**PASSING, "behavior_correct": 35 / 36, "citation_hit": 0.8, "error_count": 2}
    assert job.check_thresholds(metrics, RELEASE) == [
        "behavior=0.972 < min-behavior 1",
        "citation_hit=0.800 < min-citation-hit 0.85",
        "errors=2 > max-errors 0",
    ]


@pytest.mark.parametrize(
    ("metric", "value", "failure"),
    [
        ("key_fact_coverage", 0.7999, "key_facts=0.800 < min-key-facts 0.8"),
        ("citation_hit", None, "citation_hit=missing < min-citation-hit 0.85"),
        ("error_count", None, "errors=missing > max-errors 0"),
    ],
)
def test_threshold_failures(metric, value, failure):
    metrics = {k: v for k, v in PASSING.items() if k != metric}
    if value is not None:
        metrics[metric] = value
    assert job.check_thresholds(metrics, RELEASE) == [failure]


def test_threshold_at_the_boundary_passes():
    metrics = {**PASSING, "citation_hit": 0.85, "key_fact_coverage": 0.8, "error_count": 0}
    assert job.check_thresholds(metrics, RELEASE) == []
    assert job.check_thresholds({**PASSING, "behavior_correct": 36 / 36}, RELEASE) == []


def test_unset_thresholds_gate_nothing():
    assert job.check_thresholds({"error_count": 5}, job.Thresholds()) == []


def test_release_line_and_p95_warning():
    assert job.release_line(PASSING) == (
        "RELEASE_EVAL behavior=1.000 citation_hit=0.900 key_facts=0.850 p95_ms=8123 errors=0 n=36"
    )
    assert "citation_hit=n/a" in job.release_line({**PASSING, "citation_hit": None})
    assert job.p95_warning(PASSING) is None
    assert job.p95_warning({**PASSING, "p95_latency_ms": 10_000}) is None
    assert job.p95_warning({**PASSING, "p95_latency_ms": 12_345}) == (
        "WARNING p95 above 10000 ms (p95_ms=12345)"
    )


BASE_ARGS = ["--catalog", "workspace", "--schema", "s", "--experiment", "/Users/me/x"]


def test_parse_args_defaults_are_the_full_eval():
    args = job.parse_args(BASE_ARGS)
    assert args.exclude_category == [] and args.use_data_tool is True
    assert args.variants == ("medium",) and args.warmup is False
    assert args.thresholds == job.Thresholds()


def test_parse_args_release_flags():
    args = job.parse_args(
        [
            *BASE_ARGS,
            *["--exclude-category", "tool", "--no-data-tool", "--variants", "medium", "--warmup"],
            *["--min-behavior", "1.0", "--min-citation-hit", "0.85"],
            *["--min-key-facts", "0.80", "--max-errors", "0"],
        ]
    )
    assert args.exclude_category == ["tool"] and args.use_data_tool is False
    assert args.variants == ("medium",) and args.warmup is True
    assert args.thresholds == RELEASE


def test_parse_args_job_parameter_values():
    """What the job passes from {{job.parameters.*}}: every option with a value, "" unset."""
    args = job.parse_args(
        [
            *BASE_ARGS,
            *["--exclude-category", "tool cross", "--data-tool", "false"],
            *["--variants", "medium low", "--warmup", "true"],
            *["--min-behavior", "", "--min-citation-hit", "0.85"],
            *["--min-key-facts", "", "--max-errors", ""],
        ]
    )
    assert args.exclude_category == ["tool", "cross"] and args.use_data_tool is False
    assert args.variants == ("medium", "low") and args.warmup is True
    assert args.thresholds == job.Thresholds(min_citation_hit=0.85)
    defaults = job.parse_args(
        [*BASE_ARGS, "--exclude-category", "", "--data-tool", "true", "--warmup", "false"]
    )
    assert defaults.exclude_category == [] and defaults.use_data_tool and not defaults.warmup


@pytest.mark.parametrize("bad", [["--variants", "high"], ["--warmup", "maybe"]])
def test_parse_args_rejects_bad_values(bad):
    with pytest.raises(SystemExit):
        job.parse_args([*BASE_ARGS, *bad])


def test_release_run_excludes_tool_questions_and_skips_the_data(monkeypatch):
    monkeypatch.setattr(job, "load_questions", lambda: QUESTIONS)
    asked = []

    def answer(question):
        asked.append(question)
        return _answer(question)

    def reference(sql):
        raise AssertionError("no reference query without the data tool")

    mlflow = _FakeMlflow()
    metrics = job.run(
        mlflow,
        "workspace",
        "compliance_staging",
        answer=answer,
        reference=reference,
        judge=lambda q, facts, a: (list(facts), 7),
        exclude_categories=["tool"],
        use_data_tool=False,
        warmup=True,
    )
    assert asked == ["Question aia-03?", "Question aia-03?"]  # Warmup, then the timed loop.
    (logged,) = mlflow.runs
    assert logged["name"] == "agent_medium"
    assert logged["params"]["use_data_tool"] is False
    assert logged["params"]["excluded_categories"] == "tool"
    assert logged["params"]["warmup"] is True
    assert metrics["question_count"] == 1 and metrics["agent_question_count"] == 1
    assert [r["id"] for r in logged["csv"][1]] == ["aia-03"]


def test_a_failed_warmup_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr(job, "load_questions", lambda: QUESTIONS)
    calls = []

    def answer(question):
        calls.append(question)
        if len(calls) == 1:
            raise TimeoutError("cold")
        return _answer(question)

    metrics = job.run(
        _FakeMlflow(),
        "workspace",
        "s",
        answer=answer,
        reference=lambda s: {"rows": [[242.13]]},
        judge=lambda q, facts, a: ([], 0),
        warmup=True,
    )
    assert metrics["error_count"] == 0


def test_low_variant_names_its_run(monkeypatch):
    monkeypatch.setattr(job, "load_questions", lambda: QUESTIONS)
    mlflow = _FakeMlflow()
    job.run(
        mlflow,
        "workspace",
        "s",
        answer=_answer,
        reference=lambda s: {"rows": [[1.0]]},
        judge=lambda q, facts, a: ([], 0),
        reasoning_effort="low",
    )
    assert mlflow.runs[0]["name"] == "agent_low"
    assert mlflow.runs[0]["params"]["reasoning_effort"] == "low"


def test_excluding_every_question_fails():
    with pytest.raises(ValueError, match="no questions"):
        job.select_questions(QUESTIONS.questions, ["tool", "ai_act"])


def _fake_mlflow_module(monkeypatch):
    module = types.SimpleNamespace(
        set_tracking_uri=lambda uri: None,
        set_experiment=lambda path: None,
        tracing=types.SimpleNamespace(disable_notebook_display=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "mlflow", module)


def test_main_prints_the_release_line_and_exits_non_zero_naming_the_gate(monkeypatch, capsys):
    _fake_mlflow_module(monkeypatch)
    seen = {}

    def run(mlflow, catalog, schema, **options):
        seen.update(options)
        return {**PASSING, "citation_hit": 0.8, "p95_latency_ms": 11_000}

    monkeypatch.setattr(job, "run", run)
    release = ["--exclude-category", "tool", "--no-data-tool", "--warmup"]
    gates = ["--min-citation-hit", "0.85", "--max-errors", "0"]
    with pytest.raises(SystemExit) as exit_info:
        job.main([*BASE_ARGS, *release, *gates])
    assert exit_info.value.code == 1
    assert seen == {
        "reasoning_effort": "medium",
        "exclude_categories": ["tool"],
        "use_data_tool": False,
        "warmup": True,
    }
    assert capsys.readouterr().out.splitlines() == [
        "RELEASE_EVAL behavior=1.000 citation_hit=0.800 key_facts=0.850 p95_ms=11000 errors=0 n=36",
        "WARNING p95 above 10000 ms (p95_ms=11000)",
        "RELEASE_EVAL FAILED: citation_hit=0.800 < min-citation-hit 0.85",
    ]


def test_main_passes_without_a_failure_line(monkeypatch, capsys):
    _fake_mlflow_module(monkeypatch)
    monkeypatch.setattr(job, "run", lambda mlflow, catalog, schema, **options: dict(PASSING))
    job.main([*BASE_ARGS, "--min-behavior", "1.0", "--max-errors", "0"])
    out = capsys.readouterr().out
    assert out.startswith("RELEASE_EVAL behavior=1.000")
    assert "FAILED" not in out and "WARNING" not in out


def test_job_parameter_defaults_are_the_full_eval():
    """Each job parameter feeds its option; the defaults keep a plain bundle run unchanged."""
    from test_job_environments import _job

    definition = _job("eval_agent")
    names = {p["name"] for p in definition["parameters"]}
    params = definition["tasks"][0]["python_wheel_task"]["parameters"]
    for name in names:
        option = "--" + name.replace("_", "-")
        assert params[params.index(option) + 1] == f"{{{{job.parameters.{name}}}}}"
    defaults = {p["name"]: p["default"] for p in definition["parameters"]}
    args = job.parse_args(
        [*BASE_ARGS, *(x for n, v in defaults.items() for x in ("--" + n.replace("_", "-"), v))]
    )
    assert args.thresholds == job.Thresholds() and args.use_data_tool and not args.warmup
    assert args.exclude_category == [] and args.variants == ("medium",)
