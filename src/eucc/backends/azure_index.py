"""Azure AI Search index eucc-chunks over gold_chunks.jsonl, embedded with Azure OpenAI.

    uv sync --extra azure
    python -m eucc.backends.azure_index --input data/build/gold_chunks.jsonl [--recreate]

Config (environment):
    AZURE_SEARCH_ENDPOINT          https://<service>.search.windows.net
    AZURE_SEARCH_KEY               admin key (AzureKeyCredential)
    AZURE_OPENAI_ENDPOINT          https://<resource>.openai.azure.com
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT   default text-embedding-3-small
    AZURE_OPENAI_CHAT_DEPLOYMENT        default gpt-4.1-mini (not used for indexing)
    AZURE_OPENAI_API_VERSION            default 2024-10-21

Azure OpenAI is called with Entra ID (DefaultAzureCredential: az login locally, a managed
identity in Azure), so the identity needs "Cognitive Services OpenAI User" on the resource.

The index has no vectorizer: query vectors are computed client-side, with the same deployment,
dimensions and DefaultAzureCredential as the documents, and sent as vector queries. A
server-side vectorizer would have the search service call Azure OpenAI itself, which needs an
API key or a managed identity of the search service (Basic tier or higher).

The index mirrors the Databricks chunks_idx columns (eucc.index.SYNC_COLUMNS), so the filters
eucc.rag.retrieve uses (lang, unit_type, reg, article) map one to one. Re-running is idempotent:
the index is created or updated in place and documents are merged or uploaded by chunk_id.
--recreate drops the index first (needed when the vector dimensions or the analyzer change).

Embeddings are cached in data/azure/embeddings.jsonl (--cache): one line per chunk with its
vector and a key, the SHA-256 of the embedding deployment, the dimensions and chunk_text. A rerun
embeds only new chunks and chunks whose key changed. After the upload the index statistics are
polled until they count every document (at most 2 minutes); storage above 45 MB is flagged (the
Free tier holds 50 MB). The vectors are neither stored nor retrievable in the index, only in the
HNSW graph, which keeps storage down.

The schema is built as the REST API's JSON; azure-search-documents 12 models take that mapping
as is, so the schema, batching and document mapping are testable without the SDK.
"""

import argparse
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eucc.rag import call_with_retries

logger = logging.getLogger(__name__)

INDEX_NAME = "eucc-chunks"
KEY_FIELD = "chunk_id"
VECTOR_FIELD = "chunk_vector"
TEXT_FIELD = "chunk_text"
DIMENSIONS = 1024
EMBED_BATCH_SIZE = 64
UPLOAD_BATCH_SIZE = 500
ANALYZER = "standard.lucene"
EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"
DEFAULT_CACHE = Path("data/azure/embeddings.jsonl")
STATS_TIMEOUT_SECONDS = 120.0
STATS_POLL_SECONDS = 5.0
FREE_TIER_LIMIT_MB = 50
STORAGE_WARNING_MB = 45

ALGORITHM_NAME = "eucc-hnsw"
PROFILE_NAME = "eucc-vector-profile"

FILTERABLE_FIELDS = (
    "reg",
    "lang",
    "unit_type",
    "article",
    "paragraph",
    "celex",
    "version_date",
)
RETRIEVABLE_FIELDS = ("heading", "source_url")
DOCUMENT_FIELDS = (KEY_FIELD, *FILTERABLE_FIELDS, *RETRIEVABLE_FIELDS, TEXT_FIELD)


@dataclass(frozen=True)
class AzureConfig:
    search_endpoint: str
    search_key: str
    openai_endpoint: str
    embedding_deployment: str = EMBEDDING_MODEL
    chat_deployment: str = "gpt-4.1-mini"
    openai_api_version: str = "2024-10-21"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AzureConfig":
        env = dict(os.environ if env is None else env)
        required = ("AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_KEY", "AZURE_OPENAI_ENDPOINT")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise SystemExit(f"missing environment variables: {', '.join(missing)}")
        return cls(
            search_endpoint=env["AZURE_SEARCH_ENDPOINT"],
            search_key=env["AZURE_SEARCH_KEY"],
            openai_endpoint=env["AZURE_OPENAI_ENDPOINT"],
            embedding_deployment=env.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or EMBEDDING_MODEL,
            chat_deployment=env.get("AZURE_OPENAI_CHAT_DEPLOYMENT") or cls.chat_deployment,
            openai_api_version=env.get("AZURE_OPENAI_API_VERSION") or cls.openai_api_version,
        )


# ---------------------------------------------------------------- pure helpers


def index_definition(name: str = INDEX_NAME) -> dict[str, Any]:
    """The index as REST JSON: keyword fields to filter on, BM25 text, an HNSW cosine vector.

    No vectorizer: callers send query vectors (see the module docstring).
    """
    fields: list[dict[str, Any]] = [
        {"name": KEY_FIELD, "type": "Edm.String", "key": True, "filterable": True},
        *({"name": field, "type": "Edm.String", "filterable": True} for field in FILTERABLE_FIELDS),
        *(
            {"name": field, "type": "Edm.String", "retrievable": True}
            for field in RETRIEVABLE_FIELDS
        ),
        {
            "name": TEXT_FIELD,
            "type": "Edm.String",
            "searchable": True,
            "retrievable": True,
            "analyzer": ANALYZER,
        },
        {
            "name": VECTOR_FIELD,
            "type": "Collection(Edm.Single)",
            "searchable": True,
            # Not stored, not returned: only the HNSW graph is kept, which saves storage on the
            # 50 MB Free tier. The vectors cannot be read back (the local cache keeps them).
            "retrievable": False,
            "stored": False,
            "dimensions": DIMENSIONS,
            "vectorSearchProfile": PROFILE_NAME,
        },
    ]
    return {
        "name": name,
        "fields": fields,
        "vectorSearch": {
            "algorithms": [
                {"name": ALGORITHM_NAME, "kind": "hnsw", "hnswParameters": {"metric": "cosine"}}
            ],
            "profiles": [{"name": PROFILE_NAME, "algorithm": ALGORITHM_NAME}],
        },
    }


def batched[T](items: Iterable[T], size: int) -> Iterator[list[T]]:
    """Lists of at most size items, in order; the last one may be shorter."""
    if size < 1:
        raise ValueError(f"batch size must be positive, got {size}")
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def to_document(row: dict[str, Any], vector: Sequence[float] | None = None) -> dict[str, Any]:
    """A gold_chunks row as a search document: the index's fields only, the vector if given."""
    if not row.get(KEY_FIELD):
        raise ValueError(f"row without {KEY_FIELD}: {str(row)[:100]}")
    document = {field: row.get(field) for field in DOCUMENT_FIELDS}
    if vector is not None:
        if len(vector) != DIMENSIONS:
            raise ValueError(f"{row[KEY_FIELD]}: vector has {len(vector)} dimensions")
        document[VECTOR_FIELD] = list(vector)
    return document


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def megabytes(size_bytes: int | None) -> float:
    return (size_bytes or 0) / 1_048_576


# ---------------------------------------------------------------- embedding cache


def cache_key(text: str, model: str, dimensions: int = DIMENSIONS) -> str:
    """Changes when the chunk text, the embedding deployment or the dimensions change."""
    return hashlib.sha256(f"{model}\n{dimensions}\n{text}".encode()).hexdigest()


def load_cache(path: Path) -> dict[str, tuple[str, list[float]]]:
    """chunk_id -> (key, vector); a later line for a chunk_id wins, a torn last line is skipped."""
    cache: dict[str, tuple[str, list[float]]] = {}
    if not path.exists():
        return cache
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                cache[entry["chunk_id"]] = (entry["key"], entry["vector"])
            except (json.JSONDecodeError, KeyError) as error:
                logger.warning("%s:%d: skipped cache line (%s)", path, number, error)
    return cache


def cache_line(chunk_id: str, key: str, vector: Sequence[float]) -> str:
    return json.dumps({"chunk_id": chunk_id, "key": key, "vector": list(vector)}) + "\n"


def split_cached(
    rows: Sequence[dict[str, Any]],
    cache: dict[str, tuple[str, list[float]]],
    model: str,
) -> tuple[dict[str, list[float]], list[dict[str, Any]]]:
    """(chunk_id -> cached vector, rows to embed): new chunks, changed text, other model/dims."""
    hits: dict[str, list[float]] = {}
    misses: list[dict[str, Any]] = []
    for row in rows:
        cached = cache.get(row[KEY_FIELD])
        if (
            cached
            and cached[0] == cache_key(row[TEXT_FIELD], model)
            and len(cached[1]) == DIMENSIONS
        ):
            hits[row[KEY_FIELD]] = cached[1]
        else:
            misses.append(row)
    return hits, misses


def write_cache(
    path: Path, rows: Sequence[dict[str, Any]], vectors: dict[str, list[float]], model: str
) -> None:
    """Rewrite the cache with the current chunks only (atomic: a temp file, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as out:
        for row in rows:
            chunk_id = row[KEY_FIELD]
            out.write(cache_line(chunk_id, cache_key(row[TEXT_FIELD], model), vectors[chunk_id]))
    temp.replace(path)


def wait_for_count(
    index_client: Any,
    index_name: str,
    expected: int,
    timeout: float = STATS_TIMEOUT_SECONDS,
    interval: float = STATS_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Any, bool]:
    """Poll the index statistics until document_count == expected: (last stats, reached)."""
    deadline = clock() + timeout
    while True:
        stats = index_client.get_index_statistics(index_name)
        if stats.document_count == expected:
            return stats, True
        if clock() >= deadline:
            return stats, False
        logger.info("index has %d/%d documents; waiting", stats.document_count, expected)
        sleep(interval)


def stats_report(stats: Any, index_name: str = INDEX_NAME) -> list[str]:
    """The summary lines, and a warning near the Free tier's 50 MB."""
    storage = megabytes(stats.storage_size)
    lines = [
        f"index {index_name}: {stats.document_count} documents, "
        f"storage {storage:.1f} MB, vector index {megabytes(stats.vector_index_size):.1f} MB"
    ]
    if storage > STORAGE_WARNING_MB:
        lines.append(
            f"WARNING: storage {storage:.1f} MB is above {STORAGE_WARNING_MB} MB "
            f"(Free tier limit {FREE_TIER_LIMIT_MB} MB)"
        )
    return lines


# ---------------------------------------------------------------- Azure calls


def openai_client(config: AzureConfig) -> Any:
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    token_provider = get_bearer_token_provider(DefaultAzureCredential(), OPENAI_SCOPE)
    # The client's own retries are off: call_with_retries is the one retry policy (eucc.rag).
    return AzureOpenAI(
        azure_endpoint=config.openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=config.openai_api_version,
        max_retries=0,
    )


def embed(client: Any, deployment: str, texts: Sequence[str]) -> list[list[float]]:
    """One embeddings call, retried on 429, 5xx and connection errors with backoff."""
    response = call_with_retries(
        lambda: client.embeddings.create(
            model=deployment, input=list(texts), dimensions=DIMENSIONS
        ),
        what="embeddings",
    )
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts):
        raise RuntimeError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
    return vectors


def ensure_index(index_client: Any, definition: dict[str, Any], recreate: bool) -> None:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.search.documents.indexes.models import SearchIndex

    name = definition["name"]
    if recreate:
        try:
            index_client.delete_index(name)
            logger.info("deleted index %s", name)
        except ResourceNotFoundError:
            pass
    index_client.create_or_update_index(SearchIndex(definition))
    logger.info("index %s is in place", name)


def upload(search_client: Any, documents: Sequence[dict[str, Any]]) -> int:
    """merge_or_upload in batches of UPLOAD_BATCH_SIZE; fails on any rejected document."""
    uploaded = 0
    for batch in batched(documents, UPLOAD_BATCH_SIZE):
        results = search_client.merge_or_upload_documents(documents=batch)
        failed = [r for r in results if not r.succeeded]
        if failed:
            first = failed[0]
            raise RuntimeError(
                f"{len(failed)} of {len(batch)} documents rejected, e.g. {first.key}: "
                f"{first.status_code} {first.error_message}"
            )
        uploaded += len(batch)
        logger.info("uploaded %d/%d documents", uploaded, len(documents))
    return uploaded


def embed_with_cache(
    client: Any, model: str, rows: Sequence[dict[str, Any]], cache_path: Path
) -> dict[str, list[float]]:
    """Vectors for all rows: cached ones reused, the rest embedded in batches of 64.

    Each embedded batch is appended to the cache at once, so an interrupted run keeps what it
    paid for; at the end the cache is rewritten with the current chunks only.
    """
    vectors, misses = split_cached(rows, load_cache(cache_path), model)
    print(f"embeddings cached: {len(vectors)}, to embed: {len(misses)}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as out:
        for batch in batched(misses, EMBED_BATCH_SIZE):
            embedded = embed(client, model, [row[TEXT_FIELD] for row in batch])
            for row, vector in zip(batch, embedded, strict=True):
                vectors[row[KEY_FIELD]] = vector
                out.write(cache_line(row[KEY_FIELD], cache_key(row[TEXT_FIELD], model), vector))
            out.flush()
            logger.info("embedded %d/%d chunks", len(vectors), len(rows))
    write_cache(cache_path, rows, vectors, model)
    return vectors


def run(input_path: Path, recreate: bool, config: AzureConfig, cache_path: Path) -> None:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    rows = read_rows(input_path)
    print(f"chunks read: {len(rows)}")
    ids = [row[KEY_FIELD] for row in rows]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{input_path}: duplicate chunk_id values")

    credential = AzureKeyCredential(config.search_key)
    index_client = SearchIndexClient(config.search_endpoint, credential)
    ensure_index(
        index_client,
        index_definition(),
        recreate,
    )

    # The credential asks for a token on the first embeddings call: none when all are cached.
    vectors = embed_with_cache(openai_client(config), config.embedding_deployment, rows, cache_path)
    documents = [to_document(row, vectors[row[KEY_FIELD]]) for row in rows]

    search_client = SearchClient(config.search_endpoint, INDEX_NAME, credential)
    uploaded = upload(search_client, documents)
    print(f"documents uploaded: {uploaded}")

    # Statistics lag the upload; poll until they count every document (at most 2 minutes).
    stats, reached = wait_for_count(index_client, INDEX_NAME, uploaded)
    if not reached:
        print(
            f"WARNING: index statistics show {stats.document_count} documents, not {uploaded}, "
            f"after {STATS_TIMEOUT_SECONDS:.0f} s (still indexing, or documents of chunks no "
            "longer in the input: --recreate drops them)"
        )
    for line in stats_report(stats):
        print(line)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True, help="gold_chunks.jsonl")
    parser.add_argument("--recreate", action="store_true", help="drop the index first")
    parser.add_argument(
        "--cache", type=Path, default=DEFAULT_CACHE, help=f"embedding cache ({DEFAULT_CACHE})"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("azure.core.pipeline.policies.http_logging_policy", "azure.identity", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    run(args.input, args.recreate, AzureConfig.from_env(), args.cache)


if __name__ == "__main__":
    main()
