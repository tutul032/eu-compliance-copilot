"""Ask the copilot one question from the command line.

    python -m eucc.ask "How quickly must a breach be notified?" [--schema compliance_dev] [-p free]
        [--reasoning-effort low|medium] [--experiment /Users/me/eucc_traces]
    python -m eucc.ask --agent "What was the average DE-LU day-ahead price yesterday?" -p free

Prints the status, the answer and the cited provisions with their EUR-Lex links. With --agent the
question goes through eucc.agent (router, regulation search and/or the energy SQL tool), and the
route, the SQL used and the data rows are printed too. Traces go to an
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


def _cell(value: Any) -> str:
    return f"{value:.4g}" if isinstance(value, float) else "NULL" if value is None else str(value)


def format_agent_result(result: dict[str, Any]) -> str:
    """The route and the SQL first, then format_result, then the data rows ([D1] ...)."""
    route = result["route"]
    path = [
        name
        for name, needed in (
            ("regulation", route["needs_regulation"]),
            ("data", route["needs_data"]),
        )
        if needed
    ]
    lines = [f"Route: {' + '.join(path)} ({route['method']})"]
    if route.get("regulation_query"):
        lines.append(f"Regulation query: {route['regulation_query']}")
    if route["needs_data"]:
        lines.append(f"SQL: {result.get('sql') or '(none)'}")
    lines += ["", format_result(result)]
    data = result.get("data")
    if data is not None:
        lines.append("")
        if "error" in data:
            lines.append(f"Data tool error: {data['error']}")
        else:
            cited = {c["n"] for c in result.get("data_citations", [])}
            lines.append(f"Data rows ({len(data['rows'])}): " + " | ".join(data["columns"]))
            for n, row in enumerate(data["rows"], start=1):
                mark = "*" if f"D{n}" in cited else " "
                lines.append(f" {mark}[D{n}] " + " | ".join(_cell(v) for v in row))
            if cited:
                lines.append("  (* cited in the answer)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument(
        "--agent", action="store_true", help="route through the agent (regulations + energy data)"
    )
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
    if args.agent:
        from eucc import agent

        result = agent.run(
            args.question,
            catalog=args.catalog,
            schema=args.schema,
            reasoning_effort=args.reasoning_effort,
        )
        print(format_agent_result(result))
        return
    result = rag.answer(
        args.question,
        catalog=args.catalog,
        schema=args.schema,
        reasoning_effort=args.reasoning_effort,
    )
    print(format_result(result))


if __name__ == "__main__":
    main()
