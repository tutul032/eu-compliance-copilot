"""Ask the copilot one question from the command line.

    python -m eucc.ask "How quickly must a breach be notified?" [--schema compliance_dev] [-p free]
        [--reasoning-effort low|medium] [--experiment /Users/me/eucc_traces]

Prints the status, the answer and the cited provisions with their EUR-Lex links. Traces go to an
MLflow experiment only with --experiment; without it, tracing is off (no local mlruns folder).
"""

import argparse
import os
import sys
from typing import Any

# mlflow (imported by eucc.rag) logs a hint for coding agents on import; not useful on a CLI.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

from eucc.rag import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SCHEMA,
    REASONING_EFFORTS,
    UNSUPPORTED_STATUS,
)


def format_result(result: dict[str, Any]) -> str:
    lines = [f"Status: {result['status']}", "", result["answer"], ""]
    if result["citations"]:
        lines.append("Citations:")
        for citation in result["citations"]:
            if citation["unit_type"] == "annex":
                where = f"Annex {citation['article']}"
            else:
                paragraph = f"({citation['paragraph']})" if citation["paragraph"] else ""
                where = f"Art. {citation['article']}{paragraph}"
            heading = f" — {citation['heading']}" if citation["heading"] else ""
            lines.append(f"  [{citation['n']}] {citation['reg']} {where}{heading}")
            if citation["source_url"]:
                lines.append(f"      {citation['source_url']}")
    elif result["status"] != UNSUPPORTED_STATUS:  # Its answer already lists the sources.
        lines.append("Citations: none")
    if result.get("invalid_citations"):
        dropped = ", ".join(map(str, result["invalid_citations"]))
        lines.append(f"Dropped citations without a source: {dropped}")
    usage = result["usage"]
    lines.append(
        f"\n{result['latency_ms']} ms, {usage['prompt_tokens']} prompt + "
        f"{usage['completion_tokens']} completion tokens, reasoning effort "
        f"{result['reasoning_effort']}"
        + (", retried once" if result.get("attempts", 1) > 1 else "")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("-p", "--profile", help="Databricks CLI profile (e.g. free)")
    parser.add_argument("--experiment", help="MLflow experiment for the traces")
    parser.add_argument(
        "--reasoning-effort", choices=REASONING_EFFORTS, default=DEFAULT_REASONING_EFFORT
    )
    args = parser.parse_args(argv)

    if args.profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile
    import mlflow

    from eucc import rag

    if args.experiment:
        mlflow.set_tracking_uri("databricks")
        mlflow.set_experiment(args.experiment)
        mlflow.tracing.disable_notebook_display()  # No Trace(...) list after the answer.
    else:
        mlflow.tracing.disable()
    if hasattr(sys.stdout, "reconfigure"):  # Umlauts on a Windows console.
        sys.stdout.reconfigure(encoding="utf-8")
    result = rag.answer(
        args.question,
        catalog=args.catalog,
        schema=args.schema,
        reasoning_effort=args.reasoning_effort,
    )
    print(format_result(result))


if __name__ == "__main__":
    main()
