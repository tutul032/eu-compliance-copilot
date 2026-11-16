"""The backend interface: where chunks are searched and which model answers.

    retrieve(query, lang, regs=None, unit_types=("article", "annex"), k=10) -> ranked chunks
    fetch_article(reg, lang, article)                  -> every chunk of one article (small-to-big)
    chat(messages, json=True)                          -> (text, usage)

eucc.rag and eucc.agent take a backend parameter; without one they run the Databricks code
exactly as before (DatabricksBackend wraps it). Prompts, context building, the citation check and
the guardrails live in rag/agent and are the same for every backend: a backend only searches and
completes. The eval judge is not a backend call; it stays on Databricks gpt-oss for every backend.

    databricks  eucc.backends.databricks.DatabricksBackend  (no extra dependencies)
    azure       eucc.backends.azure.AzureBackend             (uv sync --extra azure)
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from eucc.rag import Chunk

DEFAULT_UNIT_TYPES: tuple[str, ...] = ("article", "annex")  # ADR-0004: no recitals.
DEFAULT_K = 10
BACKENDS = ("databricks", "azure")

Usage = dict[str, int]  # prompt_tokens, completion_tokens, total_tokens


class Backend(Protocol):
    name: str

    @property
    def model(self) -> str:
        """The chat model, for logs and eval results."""
        ...

    @property
    def index_name(self) -> str: ...

    def retrieve(
        self,
        query: str,
        lang: str,
        regs: Sequence[str] | None = None,
        unit_types: Sequence[str] = DEFAULT_UNIT_TYPES,
        k: int = DEFAULT_K,
    ) -> list["Chunk"]:
        """The k best chunks for query in lang, best first; regs limits the regulations."""
        ...

    def fetch_article(
        self, reg: str, lang: str, article: str, *, query: str | None = None
    ) -> list["Chunk"]:
        """Every chunk of one article, in any order (rag sorts them by paragraph).

        query: the question, for a backend whose filtered query needs text (Databricks hybrid);
        it does not change which chunks come back.
        """
        ...

    def chat(
        self, messages: list[dict[str, str]], json: bool = True, *, reasoning_effort: str = ...
    ) -> tuple[str, Usage]:
        """One completion at temperature 0. json: ask for a JSON object where the model can be
        told so; reasoning_effort: for reasoning models (gpt-oss), ignored by others."""
        ...


def make_backend(
    name: str,
    *,
    catalog: str | None = None,
    schema: str | None = None,
    index: str | None = None,
) -> Backend:
    """databricks: chunks_idx in catalog.schema (or index); azure: from the environment
    (eucc.backends.azure_index.AzureConfig)."""
    if name == "databricks":
        from eucc import rag
        from eucc.backends.databricks import DatabricksBackend

        return DatabricksBackend(
            catalog=catalog or rag.DEFAULT_CATALOG,
            schema=schema or rag.DEFAULT_SCHEMA,
            index=index,
        )
    if name == "azure":
        try:
            from eucc.backends.azure import AzureBackend
        except ImportError as error:
            raise SystemExit(
                f"the azure backend needs the azure extra (uv sync --extra azure): {error}"
            ) from error
        return AzureBackend.from_env()
    raise ValueError(f"unknown backend {name!r}; choose from {BACKENDS}")
