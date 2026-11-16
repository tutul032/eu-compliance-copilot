"""Backends without the network: OData filters, the hybrid query, chat parameters, and that a
backend switch leaves every prompt the same (mocked Databricks and Azure clients)."""

import csv
import datetime as dt
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from eucc import agent, rag
from eucc.backends import azure as az
from eucc.backends.base import make_backend
from eucc.backends.databricks import DatabricksBackend, databricks_filters
from eucc.eval import agent as agent_eval
from eucc.eval import local_eval
from eucc.eval.questions import load_questions

DIMENSIONS = 1024


def chunk(chunk_id, reg="GDPR", article="33", paragraph="1", unit_type="article", score=0.5):
    label = f"{reg} Art. {article}({paragraph})" if unit_type == "article" else f"{reg} Annex"
    return rag.Chunk(
        chunk_id=chunk_id,
        reg=reg,
        lang="en",
        unit_type=unit_type,
        article=article,
        paragraph=paragraph,
        heading="Notification",
        source_url="https://eur-lex.europa.eu/x",
        text=f"{label} — Notification\nbody of {chunk_id}",
        score=score,
    )


RETRIEVED = [
    chunk("gdpr-33-1", score=0.9),
    chunk("aia-annex-iii", "AIA", "III", None, "annex", score=0.4),
]
ARTICLE_33 = [chunk("gdpr-33-2", paragraph="2", score=0.2), chunk("gdpr-33-1", score=0.3)]


def document(c: rag.Chunk) -> dict:
    """A search result of AzureBackend's select, with the fused score."""
    return {
        "chunk_id": c.chunk_id,
        "reg": c.reg,
        "lang": c.lang,
        "unit_type": c.unit_type,
        "article": c.article,
        "paragraph": c.paragraph,
        "heading": c.heading,
        "source_url": c.source_url,
        "chunk_text": c.text,
        "@search.score": c.score,
    }


def databricks_response(*chunks: rag.Chunk) -> SimpleNamespace:
    columns = [*rag.CHUNK_COLUMNS, "score"]
    rows = [[*(document(c)[name] for name in rag.CHUNK_COLUMNS), c.score] for c in chunks]
    body = {
        "manifest": {"columns": [{"name": name} for name in columns]},
        "result": {"row_count": len(rows), "data_array": rows},
    }
    return SimpleNamespace(as_dict=lambda: body)


def completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
    )


def status_error(code: int) -> openai.APIStatusError:
    response = httpx.Response(code, request=httpx.Request("POST", "https://x"))
    return openai.APIStatusError("error", response=response, body=None)


ROUTE = {
    "needs_regulation": True,
    "needs_data": False,
    "regulation_query": "personal data breach notification to the supervisory authority",
}
ANSWER = {"status": "answered", "answer": "Within 72 hours [1]."}


def reply(**kwargs) -> SimpleNamespace:
    """The model: the router's JSON for the router prompt, else the answer's JSON."""
    system = kwargs["messages"][0]["content"]
    return completion(json.dumps(ROUTE if system == agent.ROUTER_SYSTEM_PROMPT else ANSWER))


class FakeAzureOpenAI:
    def __init__(self):
        self.embedded: list[list[str]] = []
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=MagicMock(side_effect=reply))
        )

    def _embed(self, model, input, dimensions):
        self.embedded.append(list(input))
        assert (model, dimensions) == ("emb-deploy", DIMENSIONS)
        data = [SimpleNamespace(index=i, embedding=[0.1] * dimensions) for i in range(len(input))]
        return SimpleNamespace(data=data)


def azure_search(**kwargs):
    """Hybrid queries return RETRIEVED; a filter-only query for Art. 33 returns its chunks."""
    if "vector_queries" in kwargs:
        return [document(c) for c in RETRIEVED]
    return [document(c) for c in ARTICLE_33] if "article eq '33'" in kwargs["filter"] else []


@pytest.fixture
def vectorized(monkeypatch):
    """VectorizedQuery as a plain namespace: the hybrid query is checkable without the SDK."""
    monkeypatch.setattr(az, "_vectorized_query", lambda **kw: SimpleNamespace(**kw))


@pytest.fixture
def azure(vectorized):
    search = MagicMock()
    search.search.side_effect = azure_search
    client = FakeAzureOpenAI()
    backend = az.AzureBackend(
        search, client, embedding_deployment="emb-deploy", chat_deployment="gpt-4.1-mini"
    )
    return SimpleNamespace(backend=backend, search=search, openai=client)


@pytest.fixture
def databricks(monkeypatch):
    workspace = MagicMock()

    def query(**kwargs):
        filters = json.loads(kwargs["filters_json"])
        if "article" in filters:
            return databricks_response(*(ARTICLE_33 if filters["article"] == "33" else []))
        return databricks_response(*RETRIEVED)

    workspace.vector_search_indexes.query_index.side_effect = query
    workspace.openai.chat.completions.create.side_effect = reply
    monkeypatch.setattr(rag, "_workspace", lambda: workspace)
    monkeypatch.setattr(rag, "_openai_client", lambda: workspace.openai)
    return workspace


# ---------------------------------------------------------------- filters


def test_odata_filter_language_and_unit_types_by_default():
    assert az.build_filter("en") == ("lang eq 'en' and search.in(unit_type, 'article|annex', '|')")


@pytest.mark.parametrize(
    ("regs", "expected"),
    [
        (None, ""),
        ([], ""),
        (["GDPR"], " and reg eq 'GDPR'"),
        (["GDPR", "DORA"], " and search.in(reg, 'GDPR|DORA', '|')"),
    ],
)
def test_odata_filter_regulations(regs, expected):
    base = "lang eq 'de' and search.in(unit_type, 'article|annex', '|')"
    assert az.build_filter("de", regs) == base + expected


def test_odata_filter_one_article():
    assert az.build_filter("en", ["AIA"], ["article"], "5") == (
        "lang eq 'en' and unit_type eq 'article' and reg eq 'AIA' and article eq '5'"
    )


def test_odata_filter_without_unit_types_and_escaped_quotes():
    assert az.build_filter("en", unit_types=None) == "lang eq 'en'"
    assert az.build_filter("o'x", ["a'b", "c"], unit_types=()) == (
        "lang eq 'o''x' and search.in(reg, 'a''b|c', '|')"
    )


def test_odata_filter_rejects_the_in_delimiter():
    with pytest.raises(ValueError):
        az.build_filter("en", ["GDPR|DORA", "AIA"])


def test_databricks_filters_are_todays():
    assert databricks_filters("en") == {"lang": "en", "unit_type": ["article", "annex"]}
    assert databricks_filters("en", ["GDPR"]) == {
        "lang": "en",
        "unit_type": ["article", "annex"],
        "reg": "GDPR",
    }
    assert databricks_filters("de", ["GDPR", "DORA"], ["article"])["reg"] == ["GDPR", "DORA"]


# ---------------------------------------------------------------- Azure hybrid query


def test_retrieve_is_one_hybrid_request_with_a_client_side_vector(azure):
    chunks = azure.backend.retrieve("How fast must a breach be notified?", "en", ["GDPR"], k=5)

    (call,) = azure.search.search.call_args_list
    kwargs = call.kwargs
    assert kwargs["search_text"] == "How fast must a breach be notified?"
    assert kwargs["filter"] == (
        "lang eq 'en' and search.in(unit_type, 'article|annex', '|') and reg eq 'GDPR'"
    )
    assert kwargs["top"] == 5
    assert kwargs["select"] == rag.CHUNK_COLUMNS
    (vector_query,) = kwargs["vector_queries"]
    assert vector_query.fields == "chunk_vector"
    assert len(vector_query.vector) == DIMENSIONS
    assert vector_query.k_nearest_neighbors == az.KNN_CANDIDATES
    assert azure.openai.embedded == [["How fast must a breach be notified?"]]
    assert chunks == RETRIEVED  # Mapped field by field, score from @search.score.


def test_the_same_query_is_embedded_once(azure):
    azure.backend.retrieve("Q?", "en")
    azure.backend.retrieve("Q?", "en", ["GDPR"], k=5)
    azure.backend.retrieve("Other?", "en")
    assert azure.openai.embedded == [["Q?"], ["Other?"]]


def test_a_large_k_asks_for_at_least_k_neighbours(azure):
    params = azure.backend.hybrid_query("Q?", "en", k=80)
    assert params["vector_queries"][0].k_nearest_neighbors == 80
    assert params["top"] == 80


def test_the_sdk_takes_the_vector_query_arguments():
    pytest.importorskip("azure.search.documents")
    query = az._vectorized_query(vector=[0.0] * 3, k_nearest_neighbors=50, fields="chunk_vector")
    assert (query.k_nearest_neighbors, query.fields) == (50, "chunk_vector")


def test_fetch_article_is_a_filter_only_query(azure):
    chunks = azure.backend.fetch_article("GDPR", "en", "33", query="ignored?")
    (call,) = azure.search.search.call_args_list
    assert call.kwargs == {
        "search_text": "*",
        "filter": "lang eq 'en' and unit_type eq 'article' and reg eq 'GDPR' and article eq '33'",
        "top": rag.ARTICLE_FETCH_RESULTS,
        "select": rag.CHUNK_COLUMNS,
    }
    assert [c.chunk_id for c in chunks] == ["gdpr-33-2", "gdpr-33-1"]
    assert azure.openai.embedded == []


def test_chunk_from_document_reads_empty_optionals_as_none():
    doc = {**document(RETRIEVED[0]), "paragraph": "", "heading": None, "@search.score": None}
    parsed = az.chunk_from_document(doc)
    assert (parsed.paragraph, parsed.heading, parsed.score) == (None, None, 0.0)


# ---------------------------------------------------------------- Azure chat


def test_chat_is_json_at_temperature_zero_without_reasoning_effort(azure):
    text, usage = azure.backend.chat(
        [{"role": "user", "content": "Return JSON."}], reasoning_effort="low"
    )
    assert json.loads(text) == ANSWER
    assert usage == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    kwargs = azure.openai.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4.1-mini"
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == rag.MAX_TOKENS
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in kwargs


def test_chat_without_json_has_no_response_format(azure):
    azure.backend.chat([{"role": "user", "content": "x"}], json=False)
    assert "response_format" not in azure.openai.chat.completions.create.call_args.kwargs


def test_chat_retries_429_and_5xx(azure, monkeypatch):
    monkeypatch.setattr(rag, "_sleep", lambda seconds: None)
    create = azure.openai.chat.completions.create
    create.side_effect = [status_error(429), status_error(503), completion('{"a": 1}')]
    assert azure.backend.chat([{"role": "user", "content": "JSON"}])[0] == '{"a": 1}'
    assert create.call_count == 3


def test_repr_shows_no_clients_or_keys(azure):
    assert repr(azure.backend) == "AzureBackend(index='eucc-chunks', chat='gpt-4.1-mini')"


# ---------------------------------------------------------------- Databricks backend


def test_databricks_backend_is_todays_query(databricks):
    backend = DatabricksBackend(schema="s")
    backend.retrieve("Q?", "en", ["GDPR"], k=5)
    backend.fetch_article("GDPR", "en", "33", query="Q?")
    first, second = (c.kwargs for c in databricks.vector_search_indexes.query_index.call_args_list)
    assert first["index_name"] == "workspace.s.chunks_idx"
    assert (first["query_text"], first["query_type"], first["num_results"]) == ("Q?", "HYBRID", 5)
    assert json.loads(first["filters_json"])["reg"] == "GDPR"
    assert second["query_text"] == "Q?"  # The question, as rag.expand sends it today.
    assert second["num_results"] == rag.ARTICLE_FETCH_RESULTS
    assert json.loads(second["filters_json"]) == {
        "reg": "GDPR",
        "lang": "en",
        "unit_type": "article",
        "article": "33",
    }


def test_databricks_backend_chat_is_rag_chat(monkeypatch):
    calls = []
    monkeypatch.setattr(rag, "chat", lambda *a, **kw: calls.append((a, kw)) or ("{}", {}))
    DatabricksBackend().chat([{"role": "user", "content": "x"}], reasoning_effort="low")
    DatabricksBackend(chat_model="other").chat([], json=False)
    assert calls[0] == (([{"role": "user", "content": "x"}], "low"), {})
    assert calls[1] == (([], "medium"), {"model": "other"})


def test_make_backend_databricks_and_unknown():
    backend = make_backend("databricks", schema="s", index="c.s.idx")
    assert (backend.name, backend.index_name, backend.model) == (
        "databricks",
        "c.s.idx",
        rag.MODEL,
    )
    with pytest.raises(ValueError):
        make_backend("aws")


# ---------------------------------------------------------------- same prompts on every backend


def test_rag_answer_sends_the_same_prompt_on_both_backends(databricks, azure):
    question = "How fast must a GDPR breach be notified?"
    on_databricks = rag.answer(question)
    on_azure = rag.answer(question, backend=azure.backend)

    databricks_messages = databricks.openai.chat.completions.create.call_args.kwargs["messages"]
    azure_messages = azure.openai.chat.completions.create.call_args.kwargs["messages"]
    assert azure_messages == databricks_messages
    assert azure_messages[0]["content"] == rag.SYSTEM_PROMPT
    assert azure_messages[1]["content"].startswith(
        "Sources:\n\n[1] GDPR Art. 33 — Notification\n(1) body of gdpr-33-1\n"
        "(2) body of gdpr-33-2\n\n[2] AIA Annex"
    )
    for key in ("status", "answer", "citations", "sources", "retrieved_chunk_ids"):
        assert on_azure[key] == on_databricks[key], key


def test_agent_sends_the_same_router_and_answer_prompts_on_both_backends(databricks, azure):
    question = "Must we notify a personal data breach under the GDPR, and how fast?"
    on_databricks = agent.run(question, use_data_tool=False)
    on_azure = agent.run(question, use_data_tool=False, backend=azure.backend)

    def prompts(create):
        return [call.kwargs["messages"] for call in create.call_args_list]

    databricks_prompts = prompts(databricks.openai.chat.completions.create)
    azure_prompts = prompts(azure.openai.chat.completions.create)
    assert azure_prompts == databricks_prompts
    assert [p[0]["content"] for p in azure_prompts] == [
        agent.ROUTER_SYSTEM_PROMPT,
        agent.AGENT_SYSTEM_PROMPT,
    ]
    # Both searched with the question and the router's rewrite.
    assert [c.kwargs["search_text"] for c in azure.search.search.call_args_list][:2] == [
        question,
        ROUTE["regulation_query"],
    ]
    for key in ("status", "answer", "citations", "route", "retrieved_chunk_ids"):
        assert on_azure[key] == on_databricks[key], key


def test_the_retry_prompt_is_the_same_on_both_backends(azure):
    create = azure.openai.chat.completions.create
    create.side_effect = [completion("prose"), completion(json.dumps(ANSWER))]
    generated = rag.generate("Q?", "en", [rag.Source.single(RETRIEVED[0])], backend=azure.backend)
    assert generated["attempts"] == 2
    assert create.call_args.kwargs["messages"][-1] == {"role": "user", "content": rag.RETRY_PROMPT}


# ---------------------------------------------------------------- local eval files


def test_result_stem_does_not_overwrite(tmp_path):
    day = dt.date(2026, 11, 16)
    assert local_eval.result_stem(tmp_path, "azure", day) == "azure-2026-11-16"
    (tmp_path / "azure-2026-11-16.json").write_text("{}")
    assert local_eval.result_stem(tmp_path, "azure", day) == "azure-2026-11-16-2"


def test_summary_is_one_json_line_with_settings_and_metrics(tmp_path, azure):
    metrics = {"safety_correct": 1.0, "answer_rate": 31 / 32, "p95_latency_ms": 8123.4, "x": 1}
    summary = local_eval.summarize(
        metrics,
        backend=azure.backend,
        date=dt.date(2026, 11, 16),
        reasoning_effort="medium",
        use_data_tool=False,
        excluded_categories=["tool"],
        warmup=True,
        commit="abc1234",
    )
    assert summary["backend"] == "azure"
    assert summary["model"] == "azure-openai/gpt-4.1-mini"
    assert summary["judge_model"] == rag.MODEL
    assert summary["reasoning_effort"] is None
    assert summary["only"] is None  # A full run.
    assert summary["answer_rate"] == 0.9688
    assert "x" not in summary
    _, json_path = local_eval.write_results([], summary, tmp_path, "azure-2026-11-16")
    lines = json_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == summary


def test_local_eval_args():
    args = local_eval.parse_args(
        ["--backend", "azure", "--exclude-category", "tool", "--no-data-tool", "--warmup"]
    )
    assert (args.backend, args.exclude_category, args.use_data_tool, args.warmup) == (
        "azure",
        ["tool"],
        False,
        True,
    )
    assert args.only == []


def test_only_option_takes_commas_and_repeats_without_duplicates():
    args = local_eval.parse_args(
        ["--backend", "databricks", "--only", "aia-03,gdpr-01", "--only", "gdpr-01 gdpr-02"]
    )
    assert args.only == ["aia-03", "gdpr-01", "gdpr-02"]


def test_only_questions_keeps_the_file_order_and_rejects_unknown_ids():
    questions = load_questions().questions
    assert [q.id for q in local_eval.only_questions(questions, ["gdpr-02", "gdpr-01"])] == [
        "gdpr-01",
        "gdpr-02",
    ]
    assert local_eval.only_questions(questions, []) == questions
    with pytest.raises(ValueError, match="nope-01"):
        local_eval.only_questions(questions, ["gdpr-01", "nope-01"])


def fake_answer(asked):
    """An agent result whose model saw three chunks and cited none of them."""

    def answer(question):
        asked.append(question)
        return {
            "status": "answered",
            "answer": "Without delay [1].",
            "citations": [],
            "route": {"needs_regulation": True, "needs_data": False, "method": "llm"},
            "retrieved_chunk_ids": ["gdpr-33-2", "gdpr-33-1", "aia-annex-iii"],
            "latency_ms": 10,
        }

    return answer


def run_local_eval(**kwargs):
    asked = []
    results, metrics = local_eval.evaluate(
        backend=None,
        use_data_tool=False,
        answer=fake_answer(asked),
        judge=lambda question, facts, answer: ([], 0),
        **kwargs,
    )
    return asked, results, metrics


def test_evaluate_runs_only_the_selected_questions():
    by_id = {q.id: q.question for q in load_questions().questions}
    asked, results, metrics = run_local_eval(only=["gdpr-02", "gdpr-01"])
    assert [r.id for r in results] == ["gdpr-01", "gdpr-02"]
    assert asked == [by_id["gdpr-01"], by_id["gdpr-02"]]
    assert metrics["question_count"] == 2


def test_only_and_exclude_category_both_apply():
    asked, results, _ = run_local_eval(only=["gdpr-01", "tool-01"], exclude_categories=["tool"])
    assert [r.id for r in results] == ["gdpr-01"]
    with pytest.raises(ValueError, match="no questions left"):
        run_local_eval(only=["tool-01"], exclude_categories=["tool"])


def test_csv_has_the_retrieved_chunk_ids_in_order(tmp_path):
    _, results, _ = run_local_eval(only=["gdpr-01"])
    csv_path, _ = local_eval.write_results(results, {}, tmp_path, "azure-2026-11-16")
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        [row] = list(reader)
    header = reader.fieldnames
    assert header.index("retrieved_chunk_ids") == header.index("cited_chunk_ids") + 1
    assert row["retrieved_chunk_ids"] == "gdpr-33-2 gdpr-33-1 aia-annex-iii"
    assert row["id"] == "gdpr-01"


def test_release_eval_csv_keeps_its_columns(tmp_path):
    _, results, _ = run_local_eval(only=["gdpr-01"])
    path = tmp_path / "per_question.csv"
    agent_eval.write_csv(results, path)
    with path.open(encoding="utf-8", newline="") as f:
        assert csv.DictReader(f).fieldnames == agent_eval.CSV_FIELDS
