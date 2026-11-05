"""Retrieval eval: does the top 5 / top 10 of the index hold the provisions a question needs?

Pure logic only (hit matching, recall, the per-question table); the job that queries the index and
logs to MLflow is eucc.jobs.eval_retrieval. A question counts if it has expected_sources.

A source is found by any chunk of that provision, in either language:
- article source: same reg, unit_type article, same article (no wildcards: Source rejects them);
- annex source: same reg, unit_type annex, same annex number.
all_required: true needs every source found, otherwise one is enough. Each question is queried
once for the top 10; recall@5 and recall@10 both come from that one result list.
"""

import csv
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eucc.eval.questions import Question, Source

CUTOFFS = (5, 10)
NUM_RESULTS = max(CUTOFFS)
QUERY_TYPE = "HYBRID"
RESULT_COLUMNS = ["chunk_id", "reg", "lang", "unit_type", "article", "paragraph"]
CSV_FIELDS = ["id", "lang", "expected", f"top{NUM_RESULTS}_chunk_ids"] + [
    f"hit_at_{k}" for k in CUTOFFS
]

Filters = dict[str, str | list[str]] | None


@dataclass(frozen=True)
class Retrieved:
    chunk_id: str
    reg: str
    lang: str
    unit_type: str
    article: str


@dataclass(frozen=True)
class QuestionResult:
    id: str
    lang: str
    category: str
    expected: str
    chunk_ids: list[str]
    hits: dict[int, bool]  # Cutoff -> hit within the top k.


@dataclass(frozen=True)
class Variant:
    """One way of querying the same questions: the filters that go with each query."""

    name: str
    filter_on_lang: bool
    unit_types: tuple[str, ...] = ()

    def filters(self, question: Question) -> Filters:
        filters: dict[str, str | list[str]] = {}
        if self.filter_on_lang:
            filters["lang"] = question.lang
        if self.unit_types:
            filters["unit_type"] = list(self.unit_types)
        return filters or None

    @property
    def filter_param(self) -> str:
        parts = ["lang=<question lang>"] if self.filter_on_lang else []
        if self.unit_types:
            parts.append(f"unit_type in ({', '.join(self.unit_types)})")
        return ", ".join(parts) or "none"


VARIANTS = [
    Variant("no_filter", False),
    Variant("lang_filter", True),
    # Recitals out: they explain a provision but are not the citation an answer needs.
    Variant("lang_articles", True, ("article", "annex")),
]


def parse_results(response: dict[str, Any]) -> list[Retrieved]:
    """Rows of a similarity_search response, matched to columns by the manifest's names."""
    names = [c["name"] for c in response.get("manifest", {}).get("columns", [])]
    retrieved = []
    for row in response.get("result", {}).get("data_array") or []:
        values = dict(zip(names, row, strict=False))
        retrieved.append(
            Retrieved(**{field: str(values[field]) for field in Retrieved.__match_args__})
        )
    return retrieved


def source_found(source: Source, retrieved: Iterable[Retrieved]) -> bool:
    for chunk in retrieved:
        if chunk.reg != source.reg:
            continue
        if source.annex is not None:
            if chunk.unit_type == "annex" and chunk.article == source.annex:
                return True
        elif chunk.unit_type == "article" and chunk.article == source.article:
            return True
    return False


def is_hit(question: Question, retrieved: list[Retrieved]) -> bool:
    found = [source_found(source, retrieved) for source in question.expected_sources or []]
    return all(found) if question.all_required else any(found)


def describe_sources(question: Question) -> str:
    """GDPR Art. 33 AND DORA Art. 19 (all required) / AIA Art. 6 OR AIA Annex III (any)."""
    labels = [
        f"{s.reg} Annex {s.annex}" if s.annex is not None else f"{s.reg} Art. {s.article}"
        for s in question.expected_sources or []
    ]
    return (" AND " if question.all_required else " OR ").join(labels)


def evaluable(questions: Iterable[Question]) -> list[Question]:
    return [q for q in questions if q.expected_sources]


def evaluate(
    questions: Iterable[Question],
    search: Callable[[str, Filters], list[Retrieved]],
    variant: Variant,
) -> list[QuestionResult]:
    """Runs every question with expected_sources through search(query_text, filters)."""
    results = []
    for question in evaluable(questions):
        retrieved = search(question.question, variant.filters(question))[:NUM_RESULTS]
        results.append(
            QuestionResult(
                id=question.id,
                lang=question.lang,
                category=question.category,
                expected=describe_sources(question),
                chunk_ids=[c.chunk_id for c in retrieved],
                hits={k: is_hit(question, retrieved[:k]) for k in CUTOFFS},
            )
        )
    return results


def recall_metrics(results: list[QuestionResult]) -> dict[str, float]:
    """recall_at_<k> for every cutoff: overall, per category and per language.

    MLflow metric keys allow no "@", hence recall_at_5 for recall@5.
    """
    if not results:
        raise ValueError("no questions with expected_sources to evaluate")
    groups: dict[str, list[bool]] = defaultdict(list)
    for result in results:
        for k, hit in result.hits.items():
            groups[f"recall_at_{k}"].append(hit)
            groups[f"recall_at_{k}_category_{result.category}"].append(hit)
            groups[f"recall_at_{k}_lang_{result.lang}"].append(hit)
    return {key: sum(hits) / len(hits) for key, hits in sorted(groups.items())}


def write_csv(results: list[QuestionResult], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "id": result.id,
                    "lang": result.lang,
                    "expected": result.expected,
                    f"top{NUM_RESULTS}_chunk_ids": " ".join(result.chunk_ids),
                    **{f"hit_at_{k}": hit for k, hit in result.hits.items()},
                }
            )
