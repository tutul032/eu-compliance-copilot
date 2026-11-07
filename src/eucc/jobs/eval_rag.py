"""Databricks job: the RAG answers to eval/questions.yaml, scored and logged to MLflow.

    eval_rag_job --catalog workspace --schema compliance_dev --experiment /Users/me/eucc_rag

Every question except expected_behavior == tool goes through eucc.rag.answer, once per reasoning
effort: one MLflow run each, rag_medium and rag_low, with the parameters, the metrics of
eucc.eval.answers (overall, per category, per language), the system prompts and a per-question
CSV. The answers' traces land in the same experiment, linked to their run. mlflow is configured
in main only, so run() works with fakes in unit tests.
"""

import argparse
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from eucc import rag
from eucc.eval.answers import (
    JUDGE_SYSTEM_PROMPT,
    JudgeFn,
    answer_metrics,
    answerable,
    evaluate,
    judge_messages,
    parse_judge,
    write_csv,
)
from eucc.eval.questions import load_questions
from eucc.index import INDEX_TABLE, full_name

CSV_NAME = "per_question.csv"
# Default first: the configuration in use; low shows what cheaper reasoning costs in quality.
REASONING_EFFORTS = ("medium", "low")
SUMMARY_METRICS = ("behavior_correct", "citation_hit", "key_fact_coverage")

# answer(question text, reasoning effort) -> eucc.rag.answer result.
EffortAnswerFn = Callable[[str, str], dict[str, Any]]


def llm_judge(question: str, key_facts: Sequence[str], answer: str) -> tuple[list[str] | None, int]:
    """The key facts in the answer, by the answer model at its default reasoning effort."""
    text, usage = rag.chat(judge_messages(question, key_facts, answer))
    return parse_judge(text, key_facts), usage["total_tokens"]


def run_name(reasoning_effort: str) -> str:
    return f"rag_{reasoning_effort}"


def run(
    mlflow: Any,
    catalog: str,
    schema: str,
    answer: EffortAnswerFn | None = None,
    judge: JudgeFn = llm_judge,
    reasoning_efforts: Sequence[str] = REASONING_EFFORTS,
) -> dict[str, dict[str, float]]:
    """One MLflow run per reasoning effort; returns their metrics by effort."""
    if answer is None:

        def answer(question: str, reasoning_effort: str) -> dict[str, Any]:
            return rag.answer(
                question, catalog=catalog, schema=schema, reasoning_effort=reasoning_effort
            )

    questions = load_questions().questions
    params = {
        "index": full_name(catalog, schema, INDEX_TABLE),
        "model": rag.MODEL,
        "judge_model": rag.MODEL,
        "temperature": rag.TEMPERATURE,
        "max_tokens": rag.MAX_TOKENS,
        "query_type": rag.QUERY_TYPE,
        "unit_types": ",".join(rag.UNIT_TYPES),
        "num_results": rag.NUM_RESULTS,
        "per_regulation_results": rag.PER_REGULATION_RESULTS,
        "multi_regulation_keep": rag.MULTI_REGULATION_KEEP,
        "expanded_articles": rag.EXPANDED_ARTICLES,
        "article_word_budget": rag.ARTICLE_WORD_BUDGET,
        "context_word_budget": rag.CONTEXT_WORD_BUDGET,
        "max_sources": rag.MAX_SOURCES,
        "question_count": len(answerable(questions)),
    }
    metrics_by_effort = {}
    for effort in reasoning_efforts:
        with mlflow.start_run(run_name=run_name(effort)):
            mlflow.log_params({**params, "reasoning_effort": effort})
            results = evaluate(questions, lambda q, effort=effort: answer(q, effort), judge)
            metrics = answer_metrics(results)
            mlflow.log_metrics(metrics)
            mlflow.log_text(rag.SYSTEM_PROMPT, "system_prompt.txt")
            mlflow.log_text(JUDGE_SYSTEM_PROMPT, "judge_system_prompt.txt")
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / CSV_NAME
                write_csv(results, path)
                mlflow.log_artifact(str(path))
        summary = ", ".join(f"{k}={metrics[k]:.3f}" for k in SUMMARY_METRICS if k in metrics)
        print(
            f"{run_name(effort)}: {summary}, p95_latency_ms={metrics['p95_latency_ms']:.0f}, "
            f"max_tokens_in_context={metrics['max_tokens_in_context']}, "
            f"unsupported={metrics['unsupported_count']}, errors={metrics['error_count']}"
        )
        metrics_by_effort[effort] = metrics
    return metrics_by_effort


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--experiment", required=True, help="MLflow experiment path")
    args = parser.parse_args(argv)

    import mlflow

    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(args.experiment)
    # On Databricks, mlflow displays the traces of a "cell" at its end: here a Trace(...) list
    # of every answer in the job output.
    mlflow.tracing.disable_notebook_display()
    run(mlflow, args.catalog, args.schema)


if __name__ == "__main__":
    main()
