"""AzureBackend: hybrid search on Azure AI Search eucc-chunks, answers from Azure OpenAI.

    uv sync --extra azure
    python -m eucc.ask --backend azure "How quickly must a breach be notified?"

Configuration: the environment of eucc.backends.azure_index (AZURE_SEARCH_ENDPOINT,
AZURE_SEARCH_KEY, AZURE_OPENAI_ENDPOINT, deployment names; a query key is enough for search).

- retrieve: one hybrid request, search_text (BM25, standard analyzer) plus a VectorizedQuery of
  the question, embedded client-side (text-embedding-3-small, 1024 dimensions, Entra ID: the
  index has no vectorizer). The service fuses both rankings (RRF) and returns the top k. The
  vector side contributes KNN_CANDIDATES neighbours, so a chunk ranked lower by one side can still
  be lifted by the other. An OData filter restricts lang, unit_type and reg.
- fetch_article: a filter-only query (search_text "*") for one article's chunks.
- chat: deployment gpt-4.1-mini (AZURE_OPENAI_CHAT_DEPLOYMENT), temperature 0, max_tokens as rag,
  response_format json_object (every prompt asks for a JSON object), Entra ID. reasoning_effort
  is ignored: gpt-4.1-mini is not a reasoning model.

Retries: embeddings and chat go through eucc.rag.call_with_retries (429, 5xx, timeouts, with
backoff and Retry-After), the OpenAI client's own retries off; search requests use the Azure SDK
retry policy (429/5xx with exponential backoff, Retry-After honoured) with the same number of
retries.
"""

import functools
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from eucc import rag
from eucc.backends.azure_index import (
    INDEX_NAME,
    VECTOR_FIELD,
    AzureConfig,
    embed,
    openai_client,
)
from eucc.backends.base import DEFAULT_K, DEFAULT_UNIT_TYPES, Usage

SELECT_FIELDS = tuple(rag.CHUNK_COLUMNS)
KNN_CANDIDATES = 50  # Vector neighbours fused with the BM25 ranking before the top k is cut.
SCORE_FIELD = "@search.score"
_IN_DELIMITER = "|"


# ---------------------------------------------------------------- pure helpers


def odata_literal(value: str) -> str:
    """A string literal for an OData filter: quoted, with ' doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def _match(field: str, values: Sequence[str]) -> str:
    """field eq 'x' for one value, search.in for several (faster than or-chains)."""
    if len(values) == 1:
        return f"{field} eq {odata_literal(values[0])}"
    if any(_IN_DELIMITER in value for value in values):
        raise ValueError(f"{field} values must not contain {_IN_DELIMITER!r}: {list(values)}")
    joined = _IN_DELIMITER.join(values).replace("'", "''")
    return f"search.in({field}, '{joined}', '{_IN_DELIMITER}')"


def build_filter(
    lang: str,
    regs: Sequence[str] | None = None,
    unit_types: Sequence[str] | None = DEFAULT_UNIT_TYPES,
    article: str | None = None,
) -> str:
    """The OData filter: lang always; unit_type, reg and article when given."""
    parts = [_match("lang", [lang])]
    if unit_types:
        parts.append(_match("unit_type", list(unit_types)))
    if regs:
        parts.append(_match("reg", list(regs)))
    if article is not None:
        parts.append(_match("article", [article]))
    return " and ".join(parts)


def chunk_from_document(document: Mapping[str, Any]) -> rag.Chunk:
    """A search result as rag.Chunk (the same fields as a chunks_idx row; score from the fusion)."""
    return rag.Chunk(
        chunk_id=str(document["chunk_id"]),
        reg=str(document["reg"]),
        lang=str(document["lang"]),
        unit_type=str(document["unit_type"]),
        article=str(document["article"]),
        paragraph=rag._str_or_none(document.get("paragraph")),
        heading=rag._str_or_none(document.get("heading")),
        source_url=rag._str_or_none(document.get("source_url")),
        text=str(document.get("chunk_text") or ""),
        score=float(document.get(SCORE_FIELD) or 0.0),
    )


def _vectorized_query(**kwargs: Any) -> Any:
    from azure.search.documents.models import VectorizedQuery

    return VectorizedQuery(**kwargs)


# ---------------------------------------------------------------- the backend


class AzureBackend:
    name: ClassVar[str] = "azure"

    def __init__(
        self,
        search_client: Any,
        openai: Any,
        *,
        embedding_deployment: str,
        chat_deployment: str,
        index_name: str = INDEX_NAME,
    ) -> None:
        self._search = search_client
        self._openai = openai
        self.embedding_deployment = embedding_deployment
        self.chat_deployment = chat_deployment
        self._index_name = index_name
        self._embed_cached = functools.lru_cache(maxsize=256)(self._embed)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AzureBackend":
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        config = AzureConfig.from_env(None if env is None else dict(env))
        search_client = SearchClient(
            config.search_endpoint,
            INDEX_NAME,
            AzureKeyCredential(config.search_key),
            retry_total=rag.LLM_MAX_RETRIES,
        )
        client = openai_client(config).with_options(timeout=rag.LLM_TIMEOUT_SECONDS)
        return cls(
            search_client,
            client,
            embedding_deployment=config.embedding_deployment,
            chat_deployment=config.chat_deployment,
        )

    def __repr__(self) -> str:
        # MLflow traces record arguments: never the clients (and their credentials).
        return f"AzureBackend(index={self._index_name!r}, chat={self.chat_deployment!r})"

    @property
    def model(self) -> str:
        return f"azure-openai/{self.chat_deployment}"

    @property
    def index_name(self) -> str:
        return self._index_name

    def _embed(self, text: str) -> tuple[float, ...]:
        [vector] = embed(self._openai, self.embedding_deployment, [text])
        return tuple(vector)

    def hybrid_query(
        self,
        query: str,
        lang: str,
        regs: Sequence[str] | None = None,
        unit_types: Sequence[str] = DEFAULT_UNIT_TYPES,
        k: int = DEFAULT_K,
    ) -> dict[str, Any]:
        """The keyword arguments of SearchClient.search for one hybrid request."""
        vector = list(self._embed_cached(query))  # Per-regulation queries reuse the vector.
        return {
            "search_text": query,
            "vector_queries": [
                _vectorized_query(
                    vector=vector,
                    k_nearest_neighbors=max(k, KNN_CANDIDATES),
                    fields=VECTOR_FIELD,
                )
            ],
            "filter": build_filter(lang, regs, unit_types),
            "top": k,
            "select": list(SELECT_FIELDS),
        }

    def retrieve(
        self,
        query: str,
        lang: str,
        regs: Sequence[str] | None = None,
        unit_types: Sequence[str] = DEFAULT_UNIT_TYPES,
        k: int = DEFAULT_K,
    ) -> list[rag.Chunk]:
        results = self._search.search(**self.hybrid_query(query, lang, regs, unit_types, k))
        return [chunk_from_document(document) for document in results]

    def fetch_article(
        self, reg: str, lang: str, article: str, *, query: str | None = None
    ) -> list[rag.Chunk]:
        results = self._search.search(
            search_text="*",
            filter=build_filter(lang, [reg], ["article"], article),
            top=rag.ARTICLE_FETCH_RESULTS,
            select=list(SELECT_FIELDS),
        )
        return [chunk_from_document(document) for document in results]

    def chat(
        self,
        messages: list[dict[str, str]],
        json: bool = True,
        *,
        reasoning_effort: str = rag.DEFAULT_REASONING_EFFORT,
    ) -> tuple[str, Usage]:
        kwargs: dict[str, Any] = {
            "model": self.chat_deployment,
            "messages": messages,
            "temperature": rag.TEMPERATURE,
            "max_tokens": rag.MAX_TOKENS,
        }
        if json:
            kwargs["response_format"] = {"type": "json_object"}
        response = rag.call_with_retries(
            lambda: self._openai.chat.completions.create(**kwargs),
            what=f"{self.chat_deployment} chat",
        )
        return rag.extract_text(response.choices[0].message.content), rag._usage(response.usage)
