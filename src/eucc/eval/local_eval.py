"""Local eval of one backend: the release eval's questions and metrics, results as files.

    python -m eucc.eval.local_eval --backend databricks|azure --exclude-category tool \
        --no-data-tool --warmup [-p free] [--catalog workspace --schema compliance_dev] \
        [--reasoning-effort medium] [--out eval/results] [--only gdpr-01,aia-03]

--only runs the given question ids only (comma-separated or repeated; an unknown id is an
error), e.g. to rerun a miss; the summary lists them, so a partial run is not mistaken for a full
one. --exclude-category still applies.

The same run as the release eval (eucc.jobs.eval_agent: the agent, the same question selection,
scoring and metrics), without MLflow: safety_correct, answer_rate, behavior_correct,
citation_hit, key_fact_coverage, errors, p50/p95 latency. Only the backend differs (search and
answer model, eucc.backends); the key-fact judge is always the Databricks gpt-oss endpoint
(eucc.jobs.eval_rag.llm_judge), so both backends are graded by the same judge. Both backends
therefore need Databricks auth (-p profile or the environment).

Writes, under --out (run from the repository root):
    <backend>-<date>.csv    one row per question (eucc.eval.agent.write_csv), plus
                            retrieved_chunk_ids: every chunk given to the answer model, in order
    <backend>-<date>.json   one line: the summary (metrics, backend, models, index, commit)
A second run on the same day gets -2, -3 ... instead of overwriting. The files are committed:
the Azure resources are deleted after the comparison, the results stay.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# mlflow (imported by eucc.rag) logs a hint for coding agents on import; not useful on a CLI.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

from eucc import agent, rag  # noqa: E402
from eucc.backends.base import BACKENDS, Backend, make_backend  # noqa: E402
from eucc.eval import agent as agent_eval  # noqa: E402
from eucc.eval.answers import JudgeFn, answer_metrics  # noqa: E402
from eucc.eval.questions import Question, load_questions  # noqa: E402
from eucc.jobs.eval_agent import (  # noqa: E402
    AnswerFn,
    ReferenceFn,
    behavior_misses,
    evaluate_question,
    key_facts_warning,
    p95_warning,
    release_line,
    run_reference,
    select_questions,
    warm_up,
)
from eucc.jobs.eval_rag import llm_judge  # noqa: E402

DEFAULT_OUT = Path("eval/results")
JUDGE_MODEL = rag.MODEL
# The release eval's columns, with the chunks the model was given next to the ones it cited: an
# expected provision missing there is a retrieval miss, present but uncited a generation miss.
_CITED = agent_eval.CSV_FIELDS.index("cited_chunk_ids")
CSV_FIELDS = [
    *agent_eval.CSV_FIELDS[: _CITED + 1],
    "retrieved_chunk_ids",
    *agent_eval.CSV_FIELDS[_CITED + 1 :],
]
SUMMARY_METRICS = (
    "safety_correct",
    "answer_rate",
    "behavior_correct",
    "citation_hit",
    "key_fact_coverage",
    "route_correct",
    "error_count",
    "unsupported_count",
    "p50_latency_ms",
    "p95_latency_ms",
    "question_count",
    "prompt_tokens_total",
    "completion_tokens_total",
)


def result_stem(out: Path, backend: str, date: dt.date) -> str:
    """<backend>-<date>, or -2, -3 ... when a CSV or JSON of that name exists."""
    base = f"{backend}-{date.isoformat()}"
    stem, n = base, 1
    while (out / f"{stem}.csv").exists() or (out / f"{stem}.json").exists():
        n += 1
        stem = f"{base}-{n}"
    return stem


def git_commit() -> str | None:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return output.stdout.strip() or None


def summarize(
    metrics: Mapping[str, float],
    *,
    backend: Backend,
    date: dt.date,
    reasoning_effort: str,
    use_data_tool: bool,
    excluded_categories: Sequence[str],
    warmup: bool,
    commit: str | None,
    only: Sequence[str] = (),
) -> dict[str, Any]:
    """The summary line: the run's settings, then the metrics (rounded, missing ones left out)."""
    summary: dict[str, Any] = {
        "backend": backend.name,
        "date": date.isoformat(),
        "model": backend.model,
        "judge_model": JUDGE_MODEL,
        "index": backend.index_name,
        # gpt-oss only; Azure's gpt-4.1-mini is not a reasoning model.
        "reasoning_effort": reasoning_effort if backend.name == "databricks" else None,
        "use_data_tool": use_data_tool,
        "excluded_categories": sorted(set(excluded_categories)),
        "only": list(only) or None,  # None: every selected question ran.
        "warmup": warmup,
        "commit": commit,
    }
    for key in SUMMARY_METRICS:
        if key in metrics:
            value = metrics[key]
            summary[key] = round(value, 4) if isinstance(value, float) else value
    return summary


def write_results(
    results: Sequence[agent_eval.AgentResult], summary: Mapping[str, Any], out: Path, stem: str
) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = out / f"{stem}.csv", out / f"{stem}.json"
    agent_eval.write_csv(results, csv_path, CSV_FIELDS)
    json_path.write_text(json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8")
    return csv_path, json_path


def only_questions(questions: Sequence[Question], ids: Sequence[str]) -> list[Question]:
    """The questions with these ids, in question-file order; all of them if ids is empty."""
    if not ids:
        return list(questions)
    known = {q.id for q in questions}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise ValueError(f"unknown question id(s): {', '.join(unknown)}")
    wanted = set(ids)
    return [q for q in questions if q.id in wanted]


def evaluate(
    backend: Backend,
    *,
    reasoning_effort: str = rag.DEFAULT_REASONING_EFFORT,
    exclude_categories: Sequence[str] = (),
    only: Sequence[str] = (),
    use_data_tool: bool = True,
    warmup: bool = False,
    answer: AnswerFn | None = None,
    judge: JudgeFn = llm_judge,
    reference: ReferenceFn = run_reference,
) -> tuple[list[agent_eval.AgentResult], dict[str, float]]:
    """The release eval's loop for one backend: (per-question results, metrics)."""
    if answer is None:

        def answer(question: str) -> dict[str, Any]:
            return agent.run(
                question,
                reasoning_effort=reasoning_effort,
                use_data_tool=use_data_tool,
                backend=backend,
            )

    questions = select_questions(
        only_questions(load_questions().questions, only), exclude_categories
    )
    if warmup:
        warm_up(answer, questions[0].question)
    ref = reference if use_data_tool else None
    results = []
    for n, question in enumerate(questions, start=1):
        result = evaluate_question(question, answer, ref, judge)
        print(f"[{n}/{len(questions)}] {result.id}: {result.status}, {result.latency_ms} ms")
        results.append(result)
    scored = [r.answer_result for r in results if r.answer_result is not None]
    return results, {**answer_metrics(scored), **agent_eval.agent_metrics(results)}


def _words(value: str) -> list[str]:
    return [word for word in value.replace(",", " ").split() if word]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument(
        "--exclude-category",
        action="append",
        type=_words,
        default=[],
        help="question category to leave out; repeatable, or comma-separated",
    )
    parser.add_argument(
        "--no-data-tool",
        dest="use_data_tool",
        action="store_false",
        help="run the agent without the energy SQL tool (no warehouse needed)",
    )
    parser.add_argument("--warmup", action="store_true", help="one untimed call first")
    parser.add_argument(
        "--reasoning-effort", choices=rag.REASONING_EFFORTS, default=rag.DEFAULT_REASONING_EFFORT
    )
    parser.add_argument("--catalog", default=rag.DEFAULT_CATALOG, help="databricks backend")
    parser.add_argument("--schema", default=rag.DEFAULT_SCHEMA, help="databricks backend")
    parser.add_argument("-p", "--profile", help="Databricks CLI profile (judge, databricks)")
    parser.add_argument(
        "--only",
        action="append",
        type=_words,
        default=[],
        help="question id(s) to run, e.g. gdpr-01,aia-03; repeatable",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.exclude_category = list(dict.fromkeys(c for group in args.exclude_category for c in group))
    args.only = list(dict.fromkeys(i for group in args.only for i in group))
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile
    import mlflow

    mlflow.tracing.disable()  # Files only: no traces, no local mlruns folder.
    if hasattr(sys.stdout, "reconfigure"):  # Umlauts on a Windows console.
        sys.stdout.reconfigure(encoding="utf-8")

    backend = make_backend(args.backend, catalog=args.catalog, schema=args.schema)
    date = dt.date.today()
    print(f"backend {backend.name}: model {backend.model}, index {backend.index_name}")
    results, metrics = evaluate(
        backend,
        reasoning_effort=args.reasoning_effort,
        exclude_categories=args.exclude_category,
        only=args.only,
        use_data_tool=args.use_data_tool,
        warmup=args.warmup,
    )
    summary = summarize(
        metrics,
        backend=backend,
        date=date,
        reasoning_effort=args.reasoning_effort,
        use_data_tool=args.use_data_tool,
        excluded_categories=args.exclude_category,
        warmup=args.warmup,
        commit=git_commit(),
        only=args.only,
    )
    csv_path, json_path = write_results(
        results, summary, args.out, result_stem(args.out, backend.name, date)
    )
    for line in behavior_misses(results):
        print(line)
    print(release_line(metrics))
    for warning in (p95_warning(metrics), key_facts_warning(metrics)):
        if warning:
            print(warning)
    print(f"wrote {csv_path} and {json_path}")


if __name__ == "__main__":
    main()
