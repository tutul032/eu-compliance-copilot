"""Index: the vector search index over gold_chunks, and how to tell that it is in sync.

One Delta Sync index holds the EN and DE chunks, embedded by a multilingual model
(docs/adr/0002-embedding-model.md). The functions here take plain describe() responses, so they
run without the network; the job that creates and syncs the index is eucc.jobs.index.
"""

from dataclasses import dataclass
from typing import Any

ENDPOINT = "eucc-vs"
EMBEDDING_MODEL = "databricks-qwen3-embedding-0-6b"
SOURCE_TABLE = "gold_chunks"
INDEX_TABLE = "chunks_idx"
PRIMARY_KEY = "chunk_id"
EMBEDDING_SOURCE_COLUMN = "chunk_text"
PIPELINE_TYPE = "TRIGGERED"
SYNC_COLUMNS = [
    "chunk_id",
    "reg",
    "lang",
    "unit_type",
    "article",
    "paragraph",
    "heading",
    "celex",
    "version_date",
    "source_url",
    "chunk_text",
]
# Online with nothing left to process; any other ONLINE_* state is an update still running.
SETTLED_STATES = frozenset({"ONLINE", "ONLINE_NO_PENDING_UPDATE"})


def full_name(catalog: str, schema: str, table: str) -> str:
    """Vector Search takes unquoted three-part names."""
    return f"{catalog}.{schema}.{table}"


@dataclass(frozen=True)
class IndexStatus:
    ready: bool
    state: str
    indexed_rows: int
    message: str

    @property
    def failed(self) -> bool:
        return "FAILED" in self.state

    def synced_with(self, table_rows: int) -> bool:
        """Ready, no update pending, and every table row indexed."""
        return self.ready and self.state in SETTLED_STATES and self.indexed_rows == table_rows


def index_status(description: dict[str, Any]) -> IndexStatus:
    status = description.get("status", {})
    return IndexStatus(
        ready=bool(status.get("ready")),
        state=status.get("detailed_state", ""),
        indexed_rows=int(status.get("indexed_row_count") or 0),
        message=status.get("message", ""),
    )


def embedding_model(description: dict[str, Any]) -> str:
    """The model the index was built with, which may differ from EMBEDDING_MODEL."""
    columns = description.get("delta_sync_index_spec", {}).get("embedding_source_columns", [])
    return columns[0]["embedding_model_endpoint_name"] if columns else "unknown"


def vector_search_client() -> Any:
    """A client with the SDK's auth: the job identity on Databricks, a CLI profile locally.

    The client's own default asks MLflow for a token, which SDK-based auth does not hand out, so
    the bearer token is taken from the SDK and passed in.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.vector_search.client import VectorSearchClient

    config = WorkspaceClient().config
    token = config.authenticate()["Authorization"].removeprefix("Bearer ")
    return VectorSearchClient(
        workspace_url=config.host, personal_access_token=token, disable_notice=True
    )
