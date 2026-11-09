"""Databricks job: all 40 questions of eval/questions.yaml through the agent, scored, to MLflow.

    eval_agent_job --catalog workspace --schema compliance_dev --experiment /Users/me/eucc_rag
        [--warehouse-id 4228ff3a910b0b5e]

One MLflow run, agent_medium, in the RAG eval's experiment, next to rag_medium: route_correct and
data_correct (eucc.eval.agent), and the eval_rag metrics (eucc.eval.answers) on the same 37
questions as eval_rag (all but the 3 tool-only ones). Each reference_sql runs through the SQL
tool's validator and warehouse right after the agent's answer, so both see the same data. The
per-question CSV has the route, the agent's SQL, the reference result and the numbers in the
answer. mlflow is configured in main only, so run() works with fakes in unit tests.
"""

import argparse
import os
import tempfile
from collections.abc import Callable
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
RUN_NAME = "agent_medium"
SUMMARY_METRICS = (
    "route_correct",
    "data_correct",
    "behavior_correct",
    "citation_hit",
    "key_fact_coverage",
)

AnswerFn = Callable[[str], dict[str, Any]]
ReferenceFn = Callable[[str], dict[str, Any]]


def run_reference(reference_sql: str) -> dict[str, Any]:
    """The reference query through the tool's own checks and warehouse."""
    catalog = sql_tool.load_catalog()
    return sql_tool.run_sql(sql_tool.validate_sql(reference_sql, sql_tool.allowed_tables(catalog)))


def evaluate_question(
    question: Question, answer: AnswerFn, reference: ReferenceFn, judge: JudgeFn
) -> agent_eval.AgentResult:
    try:
        result = answer(question.question)
    except Exception as error:  # One failed call must not lose the whole run.
        result = error_result(f"{type(error).__name__}: {error}")
    route = result.get("route")
    reference_result: dict[str, Any] | None = None
    if question.reference_sql:
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


def run(
    mlflow: Any,
    catalog: str,
    schema: str,
    answer: AnswerFn | None = None,
    reference: ReferenceFn = run_reference,
    judge: JudgeFn = llm_judge,
) -> dict[str, float]:
    """One MLflow run over every question; returns its metrics."""
    if answer is None:

        def answer(question: str) -> dict[str, Any]:
            return agent.run(question, catalog=catalog, schema=schema)

    questions = load_questions().questions
    params = {
        "index": full_name(catalog, schema, INDEX_TABLE),
        "model": rag.MODEL,
        "judge_model": rag.MODEL,
        "temperature": rag.TEMPERATURE,
        "max_tokens": rag.MAX_TOKENS,
        "reasoning_effort": rag.DEFAULT_REASONING_EFFORT,
        "router_reasoning_effort": agent.ROUTER_REASONING_EFFORT,
        "sql_warehouse_id": sql_tool.warehouse_id(),
        "sql_row_limit": sql_tool.ROW_LIMIT,
        "sql_max_attempts": sql_tool.MAX_ATTEMPTS,
        "energy_tables": ",".join(sorted(sql_tool.allowed_tables(sql_tool.load_catalog()))),
        "question_count": len(questions),
        "rag_question_count": len(answerable(questions)),
    }
    with mlflow.start_run(run_name=RUN_NAME):
        mlflow.log_params(params)
        results = [evaluate_question(q, answer, reference, judge) for q in questions]
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
        f"{RUN_NAME}: {summary}, p95_latency_ms={metrics['p95_latency_ms']:.0f}, "
        f"route_fallbacks={metrics['route_fallback_count']}, "
        f"data_tool_errors={metrics['data_tool_error_count']}, errors={metrics['error_count']}"
    )
    return metrics


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--experiment", required=True, help="MLflow experiment path")
    parser.add_argument("--warehouse-id", help=f"SQL warehouse (default {sql_tool.WAREHOUSE_ENV})")
    args = parser.parse_args(argv)
    if args.warehouse_id:
        os.environ[sql_tool.WAREHOUSE_ENV] = args.warehouse_id

    import mlflow

    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(args.experiment)
    mlflow.tracing.disable_notebook_display()
    run(mlflow, args.catalog, args.schema)


if __name__ == "__main__":
    main()
