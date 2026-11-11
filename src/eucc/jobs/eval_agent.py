"""Databricks job: all 40 questions of eval/questions.yaml through the agent, scored, to MLflow.

    eval_agent_job --catalog workspace --schema compliance_dev --experiment /Users/me/eucc_rag
        [--warehouse-id 4228ff3a910b0b5e] [--exclude-category tool] [--no-data-tool]
        [--variants medium,low] [--warmup] [--min-behavior 1.0] [--min-citation-hit 0.85]
        [--min-key-facts 0.80] [--max-errors 0]

One MLflow run per reasoning effort in --variants (default medium: agent_medium) in the RAG eval's
experiment, next to rag_medium: route_correct and
data_correct (eucc.eval.agent), and the eval_rag metrics (eucc.eval.answers) on the same 37
questions as eval_rag (all but the 3 tool-only ones). Each reference_sql runs through the SQL
tool's validator and warehouse right after the agent's answer, so both see the same data. The
per-question CSV has the route, the agent's SQL, the reference result and the numbers in the
answer. mlflow is configured in main only, so run() works with fakes in unit tests.

Release evaluation (docs/runbook.md): --exclude-category drops a category of eval/questions.yaml
(tool: the questions that need the energy data); --no-data-tool runs the agent without its SQL
tool (agent.AgentDeps.use_data_tool) and skips the reference queries, so no warehouse is needed;
--warmup makes one untimed agent call first (cold endpoints and caches stay out of the p95).
After each MLflow run, main prints

    RELEASE_EVAL behavior=1.000 citation_hit=0.889 key_facts=0.853 p95_ms=8123 errors=0 n=36

and a WARNING line when p95 exceeds P95_WARNING_MS (no failure). With thresholds set, a missed
one is printed as "RELEASE_EVAL FAILED: <which>" and the job exits with code 1. Every option also
takes a value (--data-tool false, --warmup true, --min-behavior "" for unset), so the job's
parameters can pass them through "databricks bundle run --params".
"""

import argparse
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eucc import agent, rag
from eucc.eval import agent as agent_eval
from eucc.eval.answers import (
    JUDGE_SYSTEM_PROMPT,
    JudgeFn,
    answer_metrics,
    answerable,
    error_result,
    score,
)
from eucc.eval.questions import Question, load_questions
from eucc.index import INDEX_TABLE, full_name
from eucc.jobs.eval_rag import llm_judge
from eucc.tools import sql as sql_tool

CSV_NAME = "per_question.csv"
RUN_PREFIX = "agent_"
RUN_NAME = f"{RUN_PREFIX}{rag.DEFAULT_REASONING_EFFORT}"
DEFAULT_VARIANTS = (rag.DEFAULT_REASONING_EFFORT,)
# The latency target of an interactive answer; above it the release eval warns, never fails.
P95_WARNING_MS = 10_000
# Means are ratios of small counts: 36/36 must pass 1.0 whatever the float rounding.
_EPSILON = 1e-9
SUMMARY_METRICS = (
    "route_correct",
    "data_correct",
    "behavior_correct",
    "citation_hit",
    "key_fact_coverage",
)

AnswerFn = Callable[[str], dict[str, Any]]
ReferenceFn = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class Thresholds:
    """Release gates; None leaves a gate off."""

    min_behavior: float | None = None
    min_citation_hit: float | None = None
    min_key_facts: float | None = None
    max_errors: int | None = None


# (name in the release line and the failure message, metric, Thresholds field)
_MINIMUMS = (
    ("behavior", "behavior_correct", "min_behavior"),
    ("citation_hit", "citation_hit", "min_citation_hit"),
    ("key_facts", "key_fact_coverage", "min_key_facts"),
)


def check_thresholds(metrics: Mapping[str, float], thresholds: Thresholds) -> list[str]:
    """One message per missed threshold, e.g. "citation_hit=0.833 < min-citation-hit 0.85".

    A gated metric that is missing (no question to compute it on) counts as missed.
    """
    failures = []
    for name, key, field in _MINIMUMS:
        minimum = getattr(thresholds, field)
        if minimum is None:
            continue
        value = metrics.get(key)
        if value is None or value < minimum - _EPSILON:
            shown = "missing" if value is None else f"{value:.3f}"
            failures.append(f"{name}={shown} < {field.replace('_', '-')} {minimum:g}")
    if thresholds.max_errors is not None:
        errors = metrics.get("error_count")
        if errors is None or errors > thresholds.max_errors:
            shown = "missing" if errors is None else f"{errors:.0f}"
            failures.append(f"errors={shown} > max-errors {thresholds.max_errors}")
    return failures


def _format(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def release_line(metrics: Mapping[str, float]) -> str:
    """The one-line summary the release workflow greps for (RELEASE_EVAL ...)."""
    return (
        f"RELEASE_EVAL behavior={_format(metrics.get('behavior_correct'), '.3f')} "
        f"citation_hit={_format(metrics.get('citation_hit'), '.3f')} "
        f"key_facts={_format(metrics.get('key_fact_coverage'), '.3f')} "
        f"p95_ms={_format(metrics.get('p95_latency_ms'), '.0f')} "
        f"errors={_format(metrics.get('error_count'), '.0f')} "
        f"n={_format(metrics.get('question_count'), '.0f')}"
    )


def p95_warning(metrics: Mapping[str, float], limit_ms: float = P95_WARNING_MS) -> str | None:
    p95 = metrics.get("p95_latency_ms")
    if p95 is None or p95 <= limit_ms:
        return None
    return f"WARNING p95 above {limit_ms:.0f} ms (p95_ms={p95:.0f})"


def run_name(reasoning_effort: str) -> str:
    return f"{RUN_PREFIX}{reasoning_effort}"


def run_reference(reference_sql: str) -> dict[str, Any]:
    """The reference query through the tool's own checks and warehouse."""
    catalog = sql_tool.load_catalog()
    return sql_tool.run_sql(sql_tool.validate_sql(reference_sql, sql_tool.allowed_tables(catalog)))


def evaluate_question(
    question: Question, answer: AnswerFn, reference: ReferenceFn | None, judge: JudgeFn
) -> agent_eval.AgentResult:
    """reference None: the reference query is not run (no data tool); data_correct is None."""
    try:
        result = answer(question.question)
    except Exception as error:  # One failed call must not lose the whole run.
        result = error_result(f"{type(error).__name__}: {error}")
    route = result.get("route")
    reference_result: dict[str, Any] | None = None
    if question.reference_sql and reference is not None:
        try:
            reference_result = reference(question.reference_sql)
        except Exception as error:
            reference_result = {"error": f"{type(error).__name__}: {error}"}
    ref_numbers = agent_eval.reference_numbers(reference_result)
    numbers = agent_eval.answer_numbers(result["answer"])
    data = result.get("data") or {}
    scored = score(question, result, judge) if answerable([question]) else None
    return agent_eval.AgentResult(
        id=question.id,
        lang=question.lang,
        category=question.category,
        expected_behavior=question.expected_behavior,
        expected_route=agent_eval.expected_route(question),
        route=agent_eval.route_name(route),
        route_method=(route or {}).get("method") or "",
        regulation_query=(route or {}).get("regulation_query"),
        route_correct=agent_eval.route_correct(question, route),
        status=result["status"],
        sql=result.get("sql"),
        data_error=data.get("error"),
        reference_sql=question.reference_sql,
        reference_result=reference_result,
        reference_numbers=ref_numbers,
        agent_numbers=numbers,
        data_correct=(
            agent_eval.data_correct(ref_numbers, numbers, question.data_match)
            if question.reference_sql
            else None
        ),
        data_citations=[c["n"] for c in result.get("data_citations") or []],
        answer_result=scored,
        latency_ms=int(result.get("latency_ms") or 0),
        answer=result["answer"],
    )


def select_questions(
    questions: Iterable[Question], exclude_categories: Iterable[str] = ()
) -> list[Question]:
    excluded = set(exclude_categories)
    selected = [q for q in questions if q.category not in excluded]
    if not selected:
        raise ValueError(f"no questions left after excluding categories {sorted(excluded)}")
    return selected


def warm_up(answer: AnswerFn, question: str) -> None:
    """One untimed call: endpoint, index and client caches warm before the timed loop."""
    start = time.perf_counter()
    try:
        result = answer(question)
        outcome = f"status={result.get('status')}"
    except Exception as error:  # A failed warmup only means the first timed call is cold.
        outcome = f"failed: {type(error).__name__}: {error}"
    print(f"warmup: {(time.perf_counter() - start) * 1000:.0f} ms, {outcome}")


def run(
    mlflow: Any,
    catalog: str,
    schema: str,
    answer: AnswerFn | None = None,
    reference: ReferenceFn = run_reference,
    judge: JudgeFn = llm_judge,
    *,
    reasoning_effort: str = rag.DEFAULT_REASONING_EFFORT,
    exclude_categories: Sequence[str] = (),
    use_data_tool: bool = True,
    warmup: bool = False,
) -> dict[str, float]:
    """One MLflow run over the selected questions; returns its metrics."""
    if answer is None:

        def answer(question: str) -> dict[str, Any]:
            return agent.run(
                question,
                catalog=catalog,
                schema=schema,
                reasoning_effort=reasoning_effort,
                use_data_tool=use_data_tool,
            )

    questions = select_questions(load_questions().questions, exclude_categories)
    if warmup:  # Before the run: its trace is not one of the run's answers.
        warm_up(answer, questions[0].question)
    params = {
        "index": full_name(catalog, schema, INDEX_TABLE),
        "model": rag.MODEL,
        "judge_model": rag.MODEL,
        "temperature": rag.TEMPERATURE,
        "max_tokens": rag.MAX_TOKENS,
        "reasoning_effort": reasoning_effort,
        "use_data_tool": use_data_tool,
        "excluded_categories": ",".join(sorted(set(exclude_categories))),
        "warmup": warmup,
        "router_reasoning_effort": agent.ROUTER_REASONING_EFFORT,
        "sql_warehouse_id": sql_tool.warehouse_id(),
        "sql_row_limit": sql_tool.ROW_LIMIT,
        "sql_max_attempts": sql_tool.MAX_ATTEMPTS,
        "energy_tables": ",".join(sorted(sql_tool.allowed_tables(sql_tool.load_catalog()))),
        "question_count": len(questions),
        "rag_question_count": len(answerable(questions)),
    }
    name = run_name(reasoning_effort)
    with mlflow.start_run(run_name=name):
        mlflow.log_params(params)
        ref = reference if use_data_tool else None
        results = [evaluate_question(q, answer, ref, judge) for q in questions]
        scored = [r.answer_result for r in results if r.answer_result is not None]
        metrics = {**answer_metrics(scored), **agent_eval.agent_metrics(results)}
        mlflow.log_metrics(metrics)
        mlflow.log_text(agent.AGENT_SYSTEM_PROMPT, "system_prompt.txt")
        mlflow.log_text(agent.ROUTER_SYSTEM_PROMPT, "router_system_prompt.txt")
        mlflow.log_text(sql_tool.SQL_SYSTEM_PROMPT, "sql_system_prompt.txt")
        mlflow.log_text(JUDGE_SYSTEM_PROMPT, "judge_system_prompt.txt")
        mlflow.log_text(sql_tool.CATALOG_PATH.read_text(encoding="utf-8"), "energy_catalog.yaml")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / CSV_NAME
            agent_eval.write_csv(results, path)
            mlflow.log_artifact(str(path))
    summary = ", ".join(f"{k}={metrics[k]:.3f}" for k in SUMMARY_METRICS if k in metrics)
    print(
        f"{name}: {summary}, p95_latency_ms={metrics['p95_latency_ms']:.0f}, "
        f"route_fallbacks={metrics['route_fallback_count']}, "
        f"data_tool_errors={metrics['data_tool_error_count']}, errors={metrics['error_count']}"
    )
    return metrics


# ---------------------------------------------------------------- command line
# Job parameters arrive as strings, an unset one as "": every value parser accepts "".


def _words(value: str) -> list[str]:
    return [word for word in re.split(r"[\s,]+", value) if word]


def _bool(value: str) -> bool:
    word = value.strip().lower()
    if word in ("true", "1", "yes"):
        return True
    if word in ("false", "0", "no", ""):
        return False
    raise argparse.ArgumentTypeError(f"not a boolean: {value!r}")


def _optional(convert: Callable[[str], Any]) -> Callable[[str], Any]:
    def parse(value: str) -> Any:
        return convert(value) if value.strip() else None

    parse.__name__ = convert.__name__  # argparse names the type in its error message.
    return parse


def _variants(value: str) -> tuple[str, ...]:
    efforts = tuple(dict.fromkeys(_words(value))) or DEFAULT_VARIANTS
    unknown = [e for e in efforts if e not in rag.REASONING_EFFORTS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown reasoning effort(s) {unknown}; choose from {rag.REASONING_EFFORTS}"
        )
    return efforts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--experiment", required=True, help="MLflow experiment path")
    parser.add_argument("--warehouse-id", help=f"SQL warehouse (default {sql_tool.WAREHOUSE_ENV})")
    parser.add_argument(
        "--exclude-category",
        action="append",
        type=_words,
        default=[],
        help="question category to leave out; repeatable, or comma-separated",
    )
    data_tool = parser.add_mutually_exclusive_group()
    data_tool.add_argument(
        "--data-tool", dest="use_data_tool", type=_bool, default=True, help="true (default)|false"
    )
    data_tool.add_argument(
        "--no-data-tool",
        dest="use_data_tool",
        action="store_false",
        help="run the agent without the energy SQL tool",
    )
    parser.add_argument(
        "--variants",
        type=_variants,
        default=DEFAULT_VARIANTS,
        help="reasoning efforts, comma-separated; one MLflow run each (default medium)",
    )
    parser.add_argument(
        "--warmup",
        type=_bool,
        nargs="?",
        const=True,
        default=False,
        help="one untimed agent call before the timed loop",
    )
    parser.add_argument("--min-behavior", type=_optional(float))
    parser.add_argument("--min-citation-hit", type=_optional(float))
    parser.add_argument("--min-key-facts", type=_optional(float))
    parser.add_argument("--max-errors", type=_optional(int))
    args = parser.parse_args(argv)
    args.exclude_category = list(dict.fromkeys(c for group in args.exclude_category for c in group))
    args.thresholds = Thresholds(
        min_behavior=args.min_behavior,
        min_citation_hit=args.min_citation_hit,
        min_key_facts=args.min_key_facts,
        max_errors=args.max_errors,
    )
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.warehouse_id:
        os.environ[sql_tool.WAREHOUSE_ENV] = args.warehouse_id

    import mlflow

    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(args.experiment)
    mlflow.tracing.disable_notebook_display()
    failures = []
    for effort in args.variants:
        metrics = run(
            mlflow,
            args.catalog,
            args.schema,
            reasoning_effort=effort,
            exclude_categories=args.exclude_category,
            use_data_tool=args.use_data_tool,
            warmup=args.warmup,
        )
        print(release_line(metrics))
        warning = p95_warning(metrics)
        if warning:
            print(warning)
        prefix = f"{run_name(effort)}: " if len(args.variants) > 1 else ""
        failures += [prefix + failure for failure in check_thresholds(metrics, args.thresholds)]
    if failures:
        # stdout, so the line lands in the job log next to RELEASE_EVAL; then a failed task.
        print(f"RELEASE_EVAL FAILED: {'; '.join(failures)}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
