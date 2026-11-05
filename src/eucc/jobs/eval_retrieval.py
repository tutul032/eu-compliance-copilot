"""Databricks job: recall@5 and @10 of chunks_idx over eval/questions.yaml, logged to MLflow.

    eval_retrieval_job --catalog workspace --schema compliance_dev --experiment /Users/me/eucc

One MLflow run per variant (no filter; the question's language; that language and articles or
annexes only) with the parameters, the recall metrics and a per-question CSV. No Spark: everything
goes through the index. mlflow and the vector search client are imported in main only, so run()
works with fakes in unit tests.
"""

import argparse
import tempfile
from pathlib import Path
from typing import Any

from eucc.eval.questions import load_questions
from eucc.eval.retrieval import (
    NUM_RESULTS,
    QUERY_TYPE,
    RESULT_COLUMNS,
    VARIANTS,
    Filters,
    Retrieved,
    evaluable,
    evaluate,
    parse_results,
    recall_metrics,
    write_csv,
)
from eucc.index import ENDPOINT, INDEX_TABLE, embedding_model, full_name, index_status

CSV_NAME = "per_question.csv"


class IndexNotReadyError(Exception):
    """Evaluating a half-synced index would measure the sync, not the retrieval."""


def run(client: Any, mlflow: Any, catalog: str, schema: str) -> dict[str, dict[str, float]]:
    name = full_name(catalog, schema, INDEX_TABLE)
    index = client.get_index(endpoint_name=ENDPOINT, index_name=name)
    description = index.describe()
    status = index_status(description)
    if not status.ready:
        raise IndexNotReadyError(f"{name} is not ready ({status.state}): {status.message}")

    def search(query_text: str, filters: Filters) -> list[Retrieved]:
        response = index.similarity_search(
            query_text=query_text,
            columns=RESULT_COLUMNS,
            num_results=NUM_RESULTS,
            query_type=QUERY_TYPE,
            filters=filters,
            disable_notice=True,
        )
        return parse_results(response)

    questions = load_questions().questions
    params = {
        "index": name,
        "embedding_model": embedding_model(description),
        "query_type": QUERY_TYPE,
        "num_results": NUM_RESULTS,
        "chunk_count": status.indexed_rows,
        "question_count": len(evaluable(questions)),
    }
    metrics_by_variant = {}
    for variant in VARIANTS:
        results = evaluate(questions, search, variant)
        metrics = recall_metrics(results)
        with mlflow.start_run(run_name=f"retrieval_{variant.name}"):
            mlflow.log_params({**params, "filter": variant.filter_param})
            mlflow.log_metrics(metrics)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / CSV_NAME
                write_csv(results, path)
                mlflow.log_artifact(str(path))
        print(f"{variant.name}: " + ", ".join(f"{k}={v:.3f}" for k, v in metrics.items()))
        metrics_by_variant[variant.name] = metrics
    return metrics_by_variant


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--experiment", required=True, help="MLflow experiment path")
    args = parser.parse_args(argv)

    import mlflow

    from eucc.index import vector_search_client

    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(args.experiment)
    run(vector_search_client(), mlflow, args.catalog, args.schema)


if __name__ == "__main__":
    main()
