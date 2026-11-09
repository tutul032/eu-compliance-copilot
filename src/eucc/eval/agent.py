"""Agent eval: right route, right numbers, and the RAG answer metrics for comparison.

Pure logic; the job that calls eucc.agent and the SQL warehouse is eucc.jobs.eval_agent.

- route_correct (all 40 questions): expected_behavior tool -> data only; answer+tool -> both;
  every other behavior -> no data (regulation only; a router that says "neither" is sent to the
  regulation path anyway).
- data_correct (questions with reference_sql): every non-zero number of the reference query's
  result appears in the answer, equal after rounding to 1 decimal (|answer - reference| <= 0.05);
  with data_match "any" one of them is enough (an error stated as MAE or as MAPE).
  Zeros are left out (an answer says "none" rather than "0"); a reference result without numbers
  cannot be checked and leaves the question out of the mean. The answer may contain other
  numbers (dates, hours, extra context).
- The eval_rag metrics (eucc.eval.answers) on the same questions as eval_rag (all but the tool
  ones), so the run compares directly with rag_medium.
"""

import csv
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eucc.eval.answers import AnswerResult
from eucc.eval.questions import Question

DATA_TOLERANCE = 0.05 + 1e-9  # Half of the last digit at 1 decimal, plus float noise.

ROUTE_DATA = "data"
ROUTE_BOTH = "regulation+data"
ROUTE_REGULATION = "regulation"
NO_ROUTE = "none"

CSV_FIELDS = [
    "id",
    "lang",
    "category",
    "expected_behavior",
    "expected_route",
    "route",
    "route_method",
    "regulation_query",
    "route_correct",
    "status",
    "sql",
    "reference_sql",
    "reference_result",
    "reference_numbers",
    "agent_numbers",
    "data_correct",
    "data_error",
    "cited_chunk_ids",
    "data_citations",
    "citation_hit",
    "key_fact_coverage",
    "latency_ms",
    "answer",
]

_CITATION_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】")
# A leading minus only where it cannot be a range or date dash ("2.4-3.2", "2026-11-08").
_NUMBER = re.compile(r"(?:(?<![\w.,])-)?\d+(?:[.,]\d+)*")
_DASHES = str.maketrans({"−": "-", "‑": "-", "‐": "-", "–": "-"})


@dataclass(frozen=True)
class AgentResult:
    id: str
    lang: str
    category: str
    expected_behavior: str
    expected_route: str
    route: str
    route_method: str
    regulation_query: str | None
    route_correct: bool
    status: str
    sql: str | None
    data_error: str | None
    reference_sql: str | None
    reference_result: dict[str, Any] | None
    reference_numbers: list[float]
    agent_numbers: list[float]
    data_correct: bool | None  # None: no reference_sql, or no number in its result.
    data_citations: list[str]
    answer_result: AnswerResult | None  # The eval_rag scores; None for tool-only questions.
    latency_ms: int
    answer: str


def expected_route(question: Question) -> str:
    if question.expected_behavior == "tool":
        return ROUTE_DATA
    if question.expected_behavior == "answer+tool":
        return ROUTE_BOTH
    return ROUTE_REGULATION


def route_name(route: dict[str, Any] | None) -> str:
    if not route:
        return NO_ROUTE
    regulation, data = bool(route.get("needs_regulation")), bool(route.get("needs_data"))
    if regulation and data:
        return ROUTE_BOTH
    return ROUTE_DATA if data else ROUTE_REGULATION if regulation else NO_ROUTE


def route_correct(question: Question, route: dict[str, Any] | None) -> bool:
    expected, actual = expected_route(question), route_name(route)
    if expected == ROUTE_REGULATION:  # "Regulation only or no data".
        return actual in (ROUTE_REGULATION, NO_ROUTE) and route is not None
    return actual == expected


def _readings(token: str) -> set[float]:
    """1.234,5 / 1,234.5 / 85,3 / 85.3 / 3.456: every plausible reading of one number."""
    separators = re.findall(r"[.,]", token)
    if not separators:
        return {float(token)}
    sign = -1.0 if token.startswith("-") else 1.0
    digits = token.lstrip("-")
    groups = re.split(r"[.,]", digits)
    readings = {sign * float("".join(groups[:-1]) + "." + groups[-1])}  # Last one is decimal.
    if all(len(g) == 3 for g in groups[1:]):  # Every separator a thousands separator.
        readings.add(sign * float("".join(groups)))
    return readings


def answer_numbers(text: str) -> list[float]:
    """Every number in the answer (citation brackets removed), each in all its readings."""
    clean = _CITATION_BRACKETS.sub(" ", text.translate(_DASHES))
    values: list[float] = []
    for token in _NUMBER.findall(clean):
        for value in sorted(_readings(token)):
            if value not in values:
                values.append(value)
    return values


def reference_numbers(result: dict[str, Any] | None) -> list[float]:
    """The non-zero numeric cells of a run_sql result, in row order."""
    if not result:
        return []
    return [
        float(value)
        for row in result.get("rows") or []
        for value in row
        if isinstance(value, int | float) and not isinstance(value, bool) and value != 0
    ]


def data_correct(
    reference: Sequence[float], numbers: Sequence[float], match: str = "all"
) -> bool | None:
    """match "all": every reference number is in the answer; "any": at least one is."""
    if not reference:
        return None
    found = [any(abs(n - r) <= DATA_TOLERANCE for n in numbers) for r in reference]
    if match == "any":
        return any(found)
    if match == "all":
        return all(found)
    raise ValueError(f"data_match must be 'all' or 'any', not {match!r}")


def agent_metrics(results: Sequence[AgentResult]) -> dict[str, float]:
    """route_correct (overall, per category and per expected route), data_correct, counts."""
    if not results:
        raise ValueError("no questions to evaluate")
    groups: dict[str, list[float]] = {}
    for r in results:
        for key in (
            "route_correct",
            f"route_correct_category_{r.category}",
            f"route_correct_expected_{r.expected_route.replace('+', '_')}",
        ):
            groups.setdefault(key, []).append(float(r.route_correct))
        if r.data_correct is not None:
            groups.setdefault("data_correct", []).append(float(r.data_correct))
    metrics = {key: sum(v) / len(v) for key, v in sorted(groups.items())}
    metrics.update(
        {
            "agent_question_count": len(results),
            "data_question_count": sum(r.reference_sql is not None for r in results),
            "data_checked_count": sum(r.data_correct is not None for r in results),
            "route_fallback_count": sum(r.route_method == "keywords" for r in results),
            "data_tool_error_count": sum(r.data_error is not None for r in results),
            "tool_answered": _mean(
                [r.status == "answered" for r in results if r.expected_behavior == "tool"]
            ),
        }
    )
    return metrics


def _mean(values: Sequence[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _numbers_text(values: Iterable[float]) -> str:
    return " ".join(f"{v:g}" for v in values)


def write_csv(results: Iterable[AgentResult], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in results:
            scored = r.answer_result
            coverage = scored.key_fact_coverage if scored else None
            writer.writerow(
                {
                    "id": r.id,
                    "lang": r.lang,
                    "category": r.category,
                    "expected_behavior": r.expected_behavior,
                    "expected_route": r.expected_route,
                    "route": r.route,
                    "route_method": r.route_method,
                    "regulation_query": r.regulation_query or "",
                    "route_correct": r.route_correct,
                    "status": r.status,
                    "sql": r.sql or "",
                    "reference_sql": " ".join((r.reference_sql or "").split()),
                    "reference_result": (
                        json.dumps(r.reference_result, ensure_ascii=False, default=str)
                        if r.reference_result is not None
                        else ""
                    ),
                    "reference_numbers": _numbers_text(r.reference_numbers),
                    "agent_numbers": _numbers_text(r.agent_numbers),
                    "data_correct": "" if r.data_correct is None else r.data_correct,
                    "data_error": r.data_error or "",
                    "cited_chunk_ids": " ".join(scored.cited_chunk_ids) if scored else "",
                    "data_citations": " ".join(r.data_citations),
                    "citation_hit": (
                        "" if scored is None or scored.citation_hit is None else scored.citation_hit
                    ),
                    "key_fact_coverage": "" if coverage is None else f"{coverage:.2f}",
                    "latency_ms": r.latency_ms,
                    "answer": r.answer,
                }
            )
