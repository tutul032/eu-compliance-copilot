"""The Azure AI Search backend without the network: schema, batching, row mapping, retries."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from eucc import rag
from eucc.backends import azure_index as az
from eucc.index import SYNC_COLUMNS

ENDPOINT = "https://eucc-aoai.openai.azure.com/"

ROW = {
    "chunk_id": "gdpr-en-article-1-1-2016-05-04",
    "provision_id": "gdpr-en-article-1-1-2016-05-04",
    "part": 1,
    "reg": "GDPR",
    "celex": "02016R0679-20160504",
    "lang": "en",
    "version_date": "2016-05-04",
    "revision": "000.002",
    "unit_type": "article",
    "article": "1",
    "paragraph": "1",
    "heading": "Subject-matter and objectives",
    "text": "This Regulation lays down rules ...",
    "chunk_text": (
        "GDPR Art. 1(1) — Subject-matter and objectives\nThis Regulation lays down rules ..."
    ),
    "amended_by": [],
    "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02016R0679-20160504",
}


def fields_by_name(definition):
    return {field["name"]: field for field in definition["fields"]}


# ---------------------------------------------------------------- schema


def test_index_has_the_databricks_columns_and_the_vector():
    fields = fields_by_name(az.index_definition())
    assert set(fields) == {*SYNC_COLUMNS, "chunk_vector"}
    assert az.index_definition()["name"] == "eucc-chunks"


def test_chunk_id_is_the_only_key():
    fields = fields_by_name(az.index_definition())
    assert [name for name, field in fields.items() if field.get("key")] == ["chunk_id"]


def test_only_the_vector_is_not_stored():
    fields = fields_by_name(az.index_definition())
    assert [name for name, field in fields.items() if field.get("stored") is False] == [
        "chunk_vector"
    ]


def test_filterable_flags():
    fields = fields_by_name(az.index_definition())
    filterable = {name for name, field in fields.items() if field.get("filterable")}
    expected = {"reg", "lang", "unit_type", "article", "paragraph", "celex", "version_date"}
    assert filterable == expected | {"chunk_id"}
    # Every filter eucc.rag.retrieve and expand send is on a filterable field.
    assert {"lang", "unit_type", "reg", "article"} <= filterable
    for name in ("heading", "source_url", "chunk_text", "chunk_vector"):
        assert not fields[name].get("filterable")


def test_only_chunk_text_is_full_text_searchable_with_the_standard_analyzer():
    fields = fields_by_name(az.index_definition())
    text = [n for n, f in fields.items() if f.get("searchable") and f["type"] == "Edm.String"]
    assert text == ["chunk_text"]
    assert fields["chunk_text"]["analyzer"] == "standard.lucene"
    assert fields["chunk_text"]["retrievable"]
    assert fields["heading"]["retrievable"] and fields["source_url"]["retrievable"]


def test_vector_field_hnsw_cosine_without_a_vectorizer():
    definition = az.index_definition()
    vector = fields_by_name(definition)["chunk_vector"]
    assert vector["type"] == "Collection(Edm.Single)"
    assert vector["dimensions"] == 1024
    assert vector["searchable"]
    assert vector["retrievable"] is False
    assert vector["stored"] is False

    search = definition["vectorSearch"]
    [profile] = [p for p in search["profiles"] if p["name"] == vector["vectorSearchProfile"]]
    [algorithm] = [a for a in search["algorithms"] if a["name"] == profile["algorithm"]]
    assert algorithm["kind"] == "hnsw"
    assert algorithm["hnswParameters"]["metric"] == "cosine"
    # Query vectors are computed client-side: no vectorizer, and the profile names none.
    assert "vectorizers" not in search
    assert "vectorizer" not in profile


def test_definition_becomes_typed_sdk_models():
    models = pytest.importorskip("azure.search.documents.indexes.models")
    index = models.SearchIndex(az.index_definition())
    assert isinstance(index.vector_search.algorithms[0], models.HnswAlgorithmConfiguration)
    assert not index.vector_search.vectorizers
    assert index.vector_search.profiles[0].vectorizer_name is None
    vector = next(f for f in index.fields if f.name == "chunk_vector")
    assert vector.vector_search_dimensions == 1024
    assert vector.searchable and vector.stored is False and vector.retrievable is False
    assert vector.vector_search_profile_name == az.PROFILE_NAME
    assert (
        next(f for f in index.fields if f.name == "chunk_text").analyzer_name == "standard.lucene"
    )


# ---------------------------------------------------------------- batching


@pytest.mark.parametrize(
    ("count", "size", "lengths"),
    [(0, 64, []), (1, 64, [1]), (64, 64, [64]), (65, 64, [64, 1]), (3704, 500, [500] * 7 + [204])],
)
def test_batched_sizes(count, size, lengths):
    batches = list(az.batched(range(count), size))
    assert [len(batch) for batch in batches] == lengths
    assert [item for batch in batches for item in batch] == list(range(count))


def test_batched_takes_any_iterable_lazily():
    assert list(az.batched(iter("abcde"), 2)) == [["a", "b"], ["c", "d"], ["e"]]


def test_batched_rejects_a_non_positive_size():
    with pytest.raises(ValueError):
        list(az.batched([1], 0))


def test_upload_merges_in_batches_of_500_and_counts():
    calls = []

    class Client:
        def merge_or_upload_documents(self, documents):
            calls.append(len(documents))
            return [SimpleNamespace(succeeded=True) for _ in documents]

    docs = [{"chunk_id": str(i)} for i in range(1001)]
    assert az.upload(Client(), docs) == 1001
    assert calls == [500, 500, 1]


def test_upload_fails_on_a_rejected_document():
    class Client:
        def merge_or_upload_documents(self, documents):
            bad = SimpleNamespace(succeeded=False, key="x", status_code=400, error_message="bad")
            return [bad]

    with pytest.raises(RuntimeError, match="x: 400 bad"):
        az.upload(Client(), [{"chunk_id": "x"}])


# ---------------------------------------------------------------- row mapping


def test_row_to_document_keeps_the_index_fields_only():
    vector = [0.1] * 1024
    document = az.to_document(ROW, vector)
    assert set(document) == {*SYNC_COLUMNS, "chunk_vector"}
    for field in SYNC_COLUMNS:
        assert document[field] == ROW[field]
    assert document["chunk_vector"] == vector
    assert json.loads(json.dumps(document)) == document  # plain JSON for the REST call


def test_row_to_document_without_a_vector_and_with_nulls():
    row = {**ROW, "paragraph": None, "heading": None}
    document = az.to_document(row)
    assert "chunk_vector" not in document
    assert document["paragraph"] is None and document["heading"] is None


def test_row_to_document_rejects_a_wrong_dimension_or_a_missing_key():
    with pytest.raises(ValueError, match="1536 dimensions"):
        az.to_document(ROW, [0.0] * 1536)
    with pytest.raises(ValueError, match="chunk_id"):
        az.to_document({**ROW, "chunk_id": ""})


def test_read_rows_skips_blank_lines(tmp_path):
    path = tmp_path / "chunks.jsonl"
    path.write_text(json.dumps(ROW) + "\n\n" + json.dumps(ROW) + "\n", encoding="utf-8")
    assert len(az.read_rows(path)) == 2


# ---------------------------------------------------------------- embeddings and config


def status_error(code):
    request = httpx.Request("POST", "https://x/embeddings")
    response = httpx.Response(code, request=request)
    return openai.APIStatusError("error", response=response, body=None)


class FakeEmbeddings:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = []

    def create(self, model, input, dimensions):
        self.calls.append((model, len(input), dimensions))
        if self.failures:
            raise self.failures.pop(0)
        # Out of order on purpose: embed() sorts by index.
        data = [SimpleNamespace(index=i, embedding=[float(i)] * 2) for i in range(len(input))]
        return SimpleNamespace(data=list(reversed(data)))


def test_embed_retries_429_and_5xx_then_returns_vectors_in_order(monkeypatch):
    monkeypatch.setattr(rag, "_sleep", lambda seconds: None)
    embeddings = FakeEmbeddings([status_error(429), status_error(503)])
    client = SimpleNamespace(embeddings=embeddings)
    vectors = az.embed(client, "emb", ["a", "b", "c"])
    assert vectors == [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]
    assert embeddings.calls == [("emb", 3, 1024)] * 3


def test_embed_does_not_retry_a_client_error(monkeypatch):
    monkeypatch.setattr(rag, "_sleep", lambda seconds: None)
    embeddings = FakeEmbeddings([status_error(400)])
    with pytest.raises(openai.APIStatusError):
        az.embed(SimpleNamespace(embeddings=embeddings), "emb", ["a"])
    assert len(embeddings.calls) == 1


def test_config_from_env_defaults_and_missing():
    env = {
        "AZURE_SEARCH_ENDPOINT": "https://s.search.windows.net",
        "AZURE_SEARCH_KEY": "k",
        "AZURE_OPENAI_ENDPOINT": ENDPOINT,
    }
    config = az.AzureConfig.from_env(env)
    assert config.embedding_deployment == "text-embedding-3-small"
    assert config.chat_deployment == "gpt-4.1-mini"
    with pytest.raises(SystemExit, match="AZURE_SEARCH_KEY"):
        az.AzureConfig.from_env({**env, "AZURE_SEARCH_KEY": ""})


# ---------------------------------------------------------------- embedding cache


def row(chunk_id, text):
    return {**ROW, "chunk_id": chunk_id, "chunk_text": text}


def vec(value):
    return [float(value)] * 1024


def test_cache_key_depends_on_text_model_and_dimensions():
    key = az.cache_key("text", "text-embedding-3-small")
    assert key == az.cache_key("text", "text-embedding-3-small", 1024)
    assert key != az.cache_key("text!", "text-embedding-3-small")
    assert key != az.cache_key("text", "text-embedding-3-large")
    assert key != az.cache_key("text", "text-embedding-3-small", 1536)


def test_split_cached_hits_and_misses():
    model = "emb"
    rows = [row("same", "a"), row("changed", "new text"), row("new", "c"), row("other", "d")]
    cache = {
        "same": (az.cache_key("a", model), vec(1)),
        "changed": (az.cache_key("old text", model), vec(2)),
        "other": (az.cache_key("d", "another-deployment"), vec(3)),
        "gone": (az.cache_key("e", model), vec(4)),
    }
    hits, misses = az.split_cached(rows, cache, model)
    assert hits == {"same": vec(1)}
    assert [r["chunk_id"] for r in misses] == ["changed", "new", "other"]


def test_split_cached_rejects_a_vector_of_the_wrong_length():
    cache = {"a": (az.cache_key("a", "emb"), [0.0] * 1536)}
    hits, misses = az.split_cached([row("a", "a")], cache, "emb")
    assert hits == {} and len(misses) == 1


def test_cache_round_trip_keeps_only_current_chunks(tmp_path):
    path = tmp_path / "azure" / "embeddings.jsonl"
    rows = [row("a", "A"), row("b", "B")]
    az.write_cache(path, rows, {"a": vec(1), "b": vec(2), "gone": vec(3)}, "emb")
    cache = az.load_cache(path)
    assert set(cache) == {"a", "b"}
    assert cache["a"] == (az.cache_key("A", "emb"), vec(1))
    assert not path.with_suffix(".jsonl.tmp").exists()


def test_load_cache_missing_file_last_line_wins_and_torn_line_skipped(tmp_path):
    path = tmp_path / "embeddings.jsonl"
    assert az.load_cache(path) == {}
    path.write_text(
        az.cache_line("a", "k1", vec(1))
        + az.cache_line("a", "k2", vec(2))
        + '{"chunk_id": "b", "ke',
        encoding="utf-8",
    )
    assert az.load_cache(path) == {"a": ("k2", vec(2))}


class CountingEmbeddings:
    def __init__(self):
        self.inputs = []

    def create(self, model, input, dimensions):
        self.inputs.append(list(input))
        data = [SimpleNamespace(index=i, embedding=vec(len(t))) for i, t in enumerate(input)]
        return SimpleNamespace(data=data)


def test_embed_with_cache_embeds_only_missing_or_changed(tmp_path):
    path = tmp_path / "azure" / "embeddings.jsonl"
    embeddings = CountingEmbeddings()
    client = SimpleNamespace(embeddings=embeddings)

    first = [row(f"c{i}", "x" * i) for i in range(1, 71)]
    vectors = az.embed_with_cache(client, "emb", first, path)
    assert [len(batch) for batch in embeddings.inputs] == [64, 6]
    assert vectors["c5"] == vec(5)

    embeddings.inputs.clear()
    assert az.embed_with_cache(client, "emb", first, path) == vectors
    assert embeddings.inputs == []  # all cached

    second = [*first[:-1], row("c70", "changed"), row("c71", "new chunk")]
    vectors = az.embed_with_cache(client, "emb", second, path)
    assert embeddings.inputs == [["changed", "new chunk"]]
    assert vectors["c70"] == vec(len("changed"))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 71  # compacted

    embeddings.inputs.clear()
    az.embed_with_cache(client, "other-deployment", second, path)
    assert sum(len(batch) for batch in embeddings.inputs) == 71


def test_embed_with_cache_keeps_finished_batches_when_a_later_one_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "_sleep", lambda seconds: None)
    path = tmp_path / "embeddings.jsonl"
    rows = [row(f"c{i}", f"t{i}") for i in range(100)]
    calls = []

    def create(model, input, dimensions):
        calls.append(len(input))
        if len(calls) == 2:
            raise status_error(400)
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=vec(0)) for i in range(len(input))]
        )

    with pytest.raises(openai.APIStatusError):
        az.embed_with_cache(
            SimpleNamespace(embeddings=SimpleNamespace(create=create)), "emb", rows, path
        )
    hits, misses = az.split_cached(rows, az.load_cache(path), "emb")
    assert len(hits) == 64 and len(misses) == 36


# ---------------------------------------------------------------- index statistics


def stats(count, storage=0, vectors=0):
    return SimpleNamespace(document_count=count, storage_size=storage, vector_index_size=vectors)


class StatsClient:
    def __init__(self, counts):
        self.counts = list(counts)
        self.calls = 0

    def get_index_statistics(self, name):
        self.calls += 1
        return stats(self.counts.pop(0) if len(self.counts) > 1 else self.counts[0])


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_for_count_polls_until_the_count_matches():
    client, clock = StatsClient([0, 1500, 3704]), Clock()
    result, reached = az.wait_for_count(client, "i", 3704, sleep=clock.sleep, clock=clock)
    assert reached and result.document_count == 3704
    assert client.calls == 3 and clock.now == 10.0


def test_wait_for_count_gives_up_after_the_timeout():
    client, clock = StatsClient([10]), Clock()
    result, reached = az.wait_for_count(client, "i", 3704, sleep=clock.sleep, clock=clock)
    assert not reached and result.document_count == 10
    assert clock.now == 120.0 and client.calls == 25  # t = 0, 5, ..., 120


def test_stats_report_in_mebibytes_and_the_free_tier_warning():
    [line] = az.stats_report(stats(3704, storage=20 * 1_048_576, vectors=15_728_640))
    assert line == "index eucc-chunks: 3704 documents, storage 20.0 MB, vector index 15.0 MB"

    lines = az.stats_report(stats(3704, storage=46 * 1_048_576))
    assert len(lines) == 2 and lines[1].startswith("WARNING: storage 46.0 MB is above 45 MB")
    assert len(az.stats_report(stats(1, storage=45 * 1_048_576))) == 1  # 45.0 is not above
