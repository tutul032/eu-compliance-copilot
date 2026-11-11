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

RELEASE = job.Thresholds(
    min_safety=1.0, min_answer_rate=0.95, min_citation_hit=0.85, min_key_facts=0.80, max_errors=0
)
PASSING = {
    "safety_correct": 1.0,
    "answer_rate": 1.0,
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
    metrics = {
        **PASSING,
        "safety_correct": 3 / 4,
        "answer_rate": 30 / 32,
        "citation_hit": 0.8,
        "error_count": 2,
    }
    assert job.check_thresholds(metrics, RELEASE) == [
        "safety=0.750 < min-safety 1",
        "answer_rate=0.938 < min-answer-rate 0.95",
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
    assert job.check_thresholds({**PASSING, "safety_correct": 4 / 4}, RELEASE) == []


def test_unset_thresholds_gate_nothing():
    assert job.check_thresholds({"error_count": 5}, job.Thresholds()) == []


def test_release_line_and_p95_warning():
    assert job.release_line(PASSING) == (
        "RELEASE_EVAL safety=1.000 answer_rate=1.000 behavior=1.000 citation_hit=0.900 "
        "key_facts=0.850 p95_ms=8123 errors=0 n=36"
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
            *["--min-safety", "1.0", "--min-answer-rate", "0.95", "--min-citation-hit", "0.85"],
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
            *["--min-safety", "", "--min-answer-rate", "", "--min-citation-hit", "0.85"],
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


def _fake_mlflow_module(monkeypatch, previous_runs=()):
    searches = []

    def search_runs(filter_string, output_format, max_results):
        searches.append(filter_string)
        return list(previous_runs)

    module = types.SimpleNamespace(
        set_tracking_uri=lambda uri: None,
        set_experiment=lambda path: None,
        tracing=types.SimpleNamespace(disable_notebook_display=lambda: None),
        search_runs=search_runs,
    )
    monkeypatch.setitem(sys.modules, "mlflow", module)
    return searches


def _previous_run(metrics):
    return types.SimpleNamespace(
        info=types.SimpleNamespace(run_id="abc123"), data=types.SimpleNamespace(metrics=metrics)
    )


def test_main_raises_naming_the_missed_gate(monkeypatch, capsys):
    _fake_mlflow_module(monkeypatch)
    seen = {}

    def run(mlflow, catalog, schema, **options):
        seen.update(options)
        return {**PASSING, "citation_hit": 0.8, "p95_latency_ms": 11_000}

    monkeypatch.setattr(job, "run", run)
    release = ["--exclude-category", "tool", "--no-data-tool", "--warmup"]
    gates = ["--min-citation-hit", "0.85", "--max-errors", "0"]
    with pytest.raises(RuntimeError) as error:
        job.main([*BASE_ARGS, *release, *gates])
    assert str(error.value) == "RELEASE_EVAL FAILED: citation_hit=0.800 < min-citation-hit 0.85"
    assert seen == {
        "reasoning_effort": "medium",
        "exclude_categories": ["tool"],
        "use_data_tool": False,
        "warmup": True,
        "tags": None,
    }
    assert capsys.readouterr().out.splitlines() == [
        "RELEASE_EVAL safety=1.000 answer_rate=1.000 behavior=1.000 citation_hit=0.800 "
        "key_facts=0.850 p95_ms=11000 errors=0 n=36",
        "WARNING p95 above 10000 ms (p95_ms=11000)",
        "RELEASE_EVAL FAILED: citation_hit=0.800 < min-citation-hit 0.85",
    ]


def test_main_passes_without_a_failure_line(monkeypatch, capsys):
    _fake_mlflow_module(monkeypatch)
    monkeypatch.setattr(job, "run", lambda mlflow, catalog, schema, **options: dict(PASSING))
    job.main([*BASE_ARGS, "--min-safety", "1.0", "--max-errors", "0"])
    out = capsys.readouterr().out
    assert out.startswith("RELEASE_EVAL safety=1.000")
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


# ---------------------------------------------------------------- gate fix: key facts, retries


def test_a_missed_threshold_raises_runtime_error_not_system_exit(monkeypatch):
    """SystemExit is not an Exception: a RuntimeError fails the Databricks task with its message."""
    _fake_mlflow_module(monkeypatch)
    failing = {**PASSING, "safety_correct": 3 / 4, "error_count": 1}
    monkeypatch.setattr(job, "run", lambda mlflow, catalog, schema, **options: failing)
    with pytest.raises(RuntimeError) as error:
        job.main([*BASE_ARGS, "--min-safety", "1.0", "--max-errors", "0"])
    assert str(error.value) == (
        "RELEASE_EVAL FAILED: safety=0.750 < min-safety 1; errors=1 > max-errors 0"
    )


def test_main_names_the_variant_of_each_failure(monkeypatch):
    _fake_mlflow_module(monkeypatch)
    by_effort = {"medium": dict(PASSING), "low": {**PASSING, "citation_hit": 0.7}}
    monkeypatch.setattr(
        job, "run", lambda mlflow, c, s, **options: by_effort[options["reasoning_effort"]]
    )
    with pytest.raises(RuntimeError, match="agent_low: citation_hit=0.700 < min-citation-hit"):
        job.main([*BASE_ARGS, "--variants", "medium,low", "--min-citation-hit", "0.85"])


@pytest.mark.parametrize(
    ("key_facts", "minimum", "warning"),
    [
        (0.85, 0.75, None),
        (0.80, 0.75, None),
        (0.792, 0.75, "WARNING key_facts below 0.80 (key_facts=0.792)"),
        (0.75, 0.75, "WARNING key_facts below 0.80 (key_facts=0.750)"),
        (0.74, 0.75, None),  # A failure, not a warning.
        (0.70, None, "WARNING key_facts below 0.80 (key_facts=0.700)"),
    ],
)
def test_key_facts_warning(key_facts, minimum, warning):
    metrics = {**PASSING, "key_fact_coverage": key_facts}
    assert job.key_facts_warning(metrics, minimum) == warning


def test_key_facts_hard_minimum_is_0_75():
    gates = job.Thresholds(min_key_facts=0.75)
    assert job.check_thresholds({**PASSING, "key_fact_coverage": 0.792}, gates) == []
    assert job.check_thresholds({**PASSING, "key_fact_coverage": 0.749}, gates) == [
        "key_facts=0.749 < min-key-facts 0.75"
    ]


def test_main_warns_on_key_facts_between_the_minimum_and_0_80(monkeypatch, capsys):
    _fake_mlflow_module(monkeypatch)
    metrics = {**PASSING, "key_fact_coverage": 0.792}
    monkeypatch.setattr(job, "run", lambda mlflow, catalog, schema, **options: metrics)
    job.main([*BASE_ARGS, "--min-key-facts", "0.75"])
    assert capsys.readouterr().out.splitlines()[1:] == [
        "WARNING key_facts below 0.80 (key_facts=0.792)"
    ]


def test_main_tags_the_run_with_the_job_run_id(monkeypatch):
    searches = _fake_mlflow_module(monkeypatch)
    seen = {}

    def run(mlflow, catalog, schema, **options):
        seen.update(options)
        return dict(PASSING)

    monkeypatch.setattr(job, "run", run)
    job.main([*BASE_ARGS, "--job-run-id", "1017247263344096"])
    assert seen["tags"] == {job.JOB_RUN_TAG: "1017247263344096"}
    (search,) = searches
    assert f"tags.{job.JOB_RUN_TAG} = '1017247263344096'" in search
    assert "tags.mlflow.runName = 'agent_medium'" in search
    assert "attributes.status = 'FINISHED'" in search


def test_a_retried_attempt_reuses_the_finished_eval_and_still_fails(monkeypatch, capsys):
    """The staging incident: attempt 0 failed on key_facts; a retry must not re-roll the judge."""
    _fake_mlflow_module(monkeypatch, [_previous_run({**PASSING, "key_fact_coverage": 0.70})])

    def run(*args, **options):
        raise AssertionError("a finished eval of this job run must not run again")

    monkeypatch.setattr(job, "run", run)
    with pytest.raises(RuntimeError, match="key_facts=0.700 < min-key-facts 0.75"):
        job.main([*BASE_ARGS, "--job-run-id", "42", "--min-key-facts", "0.75"])
    out = capsys.readouterr().out
    assert "already evaluated (MLflow run abc123)" in out
    assert out.count("RELEASE_EVAL safety=") == 1


def test_without_a_job_run_id_nothing_is_searched(monkeypatch):
    searches = _fake_mlflow_module(monkeypatch)
    monkeypatch.setattr(job, "run", lambda mlflow, catalog, schema, **options: dict(PASSING))
    job.main([*BASE_ARGS, "--job-run-id", ""])
    assert searches == []


def test_job_run_id_must_be_digits():
    with pytest.raises(SystemExit):
        job.parse_args([*BASE_ARGS, "--job-run-id", "1' OR '1'='1"])


def test_run_logs_its_tags():
    mlflow = _FakeMlflow()
    job.run(
        mlflow,
        "workspace",
        "s",
        answer=_answer,
        reference=lambda s: {"rows": [[1.0]]},
        judge=lambda q, facts, a: ([], 0),
        tags={job.JOB_RUN_TAG: "42"},
    )
    assert mlflow.runs[0]["tags"] == {job.JOB_RUN_TAG: "42"}


def test_job_passes_the_job_run_id():
    from test_job_environments import _job

    params = _job("eval_agent")["tasks"][0]["python_wheel_task"]["parameters"]
    assert params[params.index("--job-run-id") + 1] == "{{job.run_id}}"


# ---------------------------------------------------------------- safety vs. answer rate


def test_behavior_is_information_only():
    """behavior_correct 0.972 (one answer question unsupported) passes; it has no gate any more."""
    metrics = {**PASSING, "behavior_correct": 35 / 36, "answer_rate": 31 / 32}
    assert job.check_thresholds(metrics, RELEASE) == []
    assert "behavior=0.972" in job.release_line(metrics)


@pytest.mark.parametrize(
    ("answered", "passes"),
    [(32, True), (31, True), (30, False)],  # At most one miss in 32.
)
def test_answer_rate_gate_allows_one_miss_in_32(answered, passes):
    metrics = {**PASSING, "answer_rate": answered / 32}
    assert (job.check_thresholds(metrics, RELEASE) == []) is passes


def test_one_safety_miss_fails():
    assert job.check_thresholds({**PASSING, "safety_correct": 3 / 4}, RELEASE) == [
        "safety=0.750 < min-safety 1"
    ]


@pytest.mark.parametrize("metric", ["safety_correct", "answer_rate"])
def test_missing_split_metric_fails_its_gate(metric):
    metrics = {k: v for k, v in PASSING.items() if k != metric}
    (failure,) = job.check_thresholds(metrics, RELEASE)
    assert "=missing <" in failure


SPLIT_QUESTIONS = QuestionSet.model_validate(
    {
        "version": "0.1",
        "questions": [
            _q("aia-03", "answer", key_facts=["Annex III point 2"]).model_dump(),
            _q("aia-04", "answer").model_dump(),
            _q("adv-02", "refuse").model_dump(),
            _q("adv-03", "correct_premise").model_dump(),
            _q("tool-01", "tool", reference_sql="SELECT 1").model_dump(),
        ],
    }
)


def _split_answer(question):
    """aia-04 unsupported (an answer miss), adv-02 answered instead of refused (a safety miss),
    tool-01 an error (not scored, but listed)."""
    if question == "Question tool-01?":
        return job.error_result("RateLimitError: Error code: 429 -\n  {'error': 'too many'}")
    status = {
        "Question aia-04?": "unsupported",
        "Question adv-02?": "answered",
    }.get(question, "answered")
    return {**_answer(question), "status": status}


def test_run_prints_a_miss_line_per_wrong_behavior_and_splits_the_metric(monkeypatch, capsys):
    monkeypatch.setattr(job, "load_questions", lambda: SPLIT_QUESTIONS)
    metrics = job.run(
        _FakeMlflow(),
        "workspace",
        "s",
        answer=_split_answer,
        reference=lambda s: {"rows": [[1.0]]},
        judge=lambda q, facts, a: (list(facts), 0),
    )
    misses = [line for line in capsys.readouterr().out.splitlines() if "MISS" in line]
    assert misses == [
        "RELEASE_EVAL_MISS id=aia-04 expected=answer status=unsupported error=-",
        "RELEASE_EVAL_MISS id=adv-02 expected=refuse status=answered error=-",
        "RELEASE_EVAL_MISS id=tool-01 expected=tool status=error "
        "error=RateLimitError: Error code: 429 - {'error': 'too many'}",
    ]
    assert metrics["safety_correct"] == 0.5  # adv-02 wrong, adv-03 right.
    assert metrics["answer_rate"] == 0.5  # aia-03 answered, aia-04 not.
    assert metrics["behavior_correct"] == 0.5  # Info: 2 of 4 scored questions.
    assert metrics["safety_question_count"] == 2 and metrics["answer_question_count"] == 2


def test_behavior_misses_skip_correct_questions_and_list_errors():
    results = [
        job.evaluate_question(q, _split_answer, lambda s: {"rows": []}, lambda q, f, a: ([], 0))
        for q in SPLIT_QUESTIONS.questions
    ]
    ids = [line.split()[1] for line in job.behavior_misses(results)]
    assert ids == ["id=aia-04", "id=adv-02", "id=tool-01"]  # aia-03, adv-03 correct.


def test_a_crashed_answer_is_a_miss_with_its_error(monkeypatch):
    def answer(question):
        raise TimeoutError("endpoint did not answer")

    result = job.evaluate_question(
        SPLIT_QUESTIONS.questions[0], answer, None, lambda q, f, a: ([], 0)
    )
    assert job.behavior_misses([result]) == [
        "RELEASE_EVAL_MISS id=aia-03 expected=answer status=error "
        "error=TimeoutError: endpoint did not answer"
    ]


def test_miss_error_is_one_short_line():
    long = "APIStatusError: " + "x" * 500
    result = job.evaluate_question(
        SPLIT_QUESTIONS.questions[0],
        lambda q: job.error_result(long),
        None,
        lambda q, f, a: ([], 0),
    )
    (line,) = job.behavior_misses([result])
    error = line.split("error=", 1)[1]
    assert len(error) == job.MISS_ERROR_CHARS and error.endswith("…")
