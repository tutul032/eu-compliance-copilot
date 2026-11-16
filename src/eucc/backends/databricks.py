"""DatabricksBackend: today's code behind the backend interface, unchanged.

Vector Search hybrid queries on chunks_idx (rag._query) and the gpt-oss serving endpoint
(rag.chat). Both are looked up on eucc.rag at call time, so rag's retries, timeouts and the
tests' fakes (rag._workspace, rag._openai_client, rag.chat) apply as before.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from eucc import rag
from eucc.backends.base import DEFAULT_K, DEFAULT_UNIT_TYPES, Usage
from eucc.index import INDEX_TABLE, full_name


def databricks_filters(
    lang: str, regs: Sequence[str] | None = None, unit_types: Sequence[str] = DEFAULT_UNIT_TYPES
) -> dict[str, Any]:
    """filters_json of a query_index call: a list means IN, one regulation a plain value."""
    filters: dict[str, Any] = {"lang": lang, "unit_type": list(unit_types)}
    if regs:
        filters["reg"] = regs[0] if len(regs) == 1 else list(regs)
    return filters


@dataclass(frozen=True)
class DatabricksBackend:
    name: ClassVar[str] = "databricks"
    catalog: str = rag.DEFAULT_CATALOG
    schema: str = rag.DEFAULT_SCHEMA
    index: str | None = None  # A full name; overrides catalog.schema.chunks_idx.
    chat_model: str = rag.MODEL

    @property
    def model(self) -> str:
        return self.chat_model

    @property
    def index_name(self) -> str:
        return self.index or full_name(self.catalog, self.schema, INDEX_TABLE)

    def retrieve(
        self,
        query: str,
        lang: str,
        regs: Sequence[str] | None = None,
        unit_types: Sequence[str] = DEFAULT_UNIT_TYPES,
        k: int = DEFAULT_K,
    ) -> list[rag.Chunk]:
        return rag._query(self.index_name, query, databricks_filters(lang, regs, unit_types), k)

    def fetch_article(
        self, reg: str, lang: str, article: str, *, query: str | None = None
    ) -> list[rag.Chunk]:
        # A hybrid query needs text; the filter alone decides which chunks come back.
        text = query or f"{reg} Art. {article}"
        filters = rag._article_filters((reg, lang, article))
        return rag._query(self.index_name, text, filters, rag.ARTICLE_FETCH_RESULTS)

    def chat(
        self,
        messages: list[dict[str, str]],
        json: bool = True,
        *,
        reasoning_effort: str = rag.DEFAULT_REASONING_EFFORT,
    ) -> tuple[str, Usage]:
        # json: every prompt already asks gpt-oss for the JSON object; the endpoint gets no
        # response_format, as before.
        if self.chat_model == rag.MODEL:
            return rag.chat(messages, reasoning_effort)
        return rag.chat(messages, reasoning_effort, model=self.chat_model)
