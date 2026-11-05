"""Hit matching and recall: no index, no network."""

import csv

import pytest
from pydantic import ValidationError

from eucc.eval import retrieval as r
from eucc.eval.questions import Question, Source, load_questions


def _question(sources: list[dict], all_required: bool = False, **overrides) -> Question:
    fields = {
        "id": "q-01",
        "lang": "en",
        "category": "gdpr",
        "question": "How fast?",
        "expected_behavior": "answer",
        "expected_sources": sources,
        "all_required": all_required,
    }
    return Question.model_validate({**fields, **overrides})


def _chunk(reg="GDPR", article="33", unit_type="article", lang="en", paragraph="1") -> r.Retrieved:
    chunk_id = f"{reg}-{lang}-{unit_type}-{article}-{paragraph}-2016-05-04".lower()
    return r.Retrieved(chunk_id=chunk_id, reg=reg, lang=lang, unit_type=unit_type, article=article)


# Hits


def test_article_source_found_by_any_paragraph_in_either_language():
    source = Source(reg="GDPR", article="33")
    assert r.source_found(source, [_chunk(paragraph="4", lang="de")])


@pytest.mark.parametrize(
    "chunk",
    [
        _chunk(reg="DORA"),  # Same number, other regulation.
        _chunk(article="34"),
        _chunk(article="33", unit_type="recital"),  # Recital 33 is not Article 33.
        _chunk(article="3"),  # No prefix match.
    ],
)
def test_article_source_not_found_by_other_provisions(chunk):
    assert not r.source_found(Source(reg="GDPR", article="33"), [chunk])


def test_annex_source_needs_an_annex_chunk_with_that_number():
    source = Source(reg="AIA", annex="III")
    assert r.source_found(source, [_chunk(reg="AIA", unit_type="annex", article="III")])
    assert not r.source_found(source, [_chunk(reg="AIA", unit_type="annex", article="II")])
    assert not r.source_found(source, [_chunk(reg="AIA", unit_type="article", article="III")])


def test_annex_chunk_matches_its_annex_source():
    chunk = _chunk(reg="AIA", unit_type="annex", article="III")
    assert r.source_found(Source(reg="AIA", annex="III"), [chunk])


@pytest.mark.parametrize("article", ["any", "ANY", "*", "6(2)", ""])
def test_source_without_an_article_number_is_a_validation_error(article):
    # No wildcards: a source that matches every article would make the hit meaningless.
    with pytest.raises(ValidationError, match="expected an article number"):
        Source(reg="OMNIBUS_AI", article=article)


def test_question_with_a_wildcard_source_does_not_load():
    with pytest.raises(ValidationError, match="'any'"):
        _question([{"reg": "AIA", "article": "113"}, {"reg": "OMNIBUS_AI", "article": "any"}])


@pytest.mark.parametrize("article", ["33", "4a", "75b"])
def test_article_numbers_as_the_index_stores_them_are_valid(article):
    assert Source(reg="AIA", article=article).article == article


def test_one_source_is_enough_without_all_required():
    question = _question([{"reg": "GDPR", "article": "45"}, {"reg": "GDPR", "article": "46"}])
    assert r.is_hit(question, [_chunk(article="46")])


def test_all_required_needs_every_source():
    question = _question(
        [{"reg": "GDPR", "article": "33"}, {"reg": "DORA", "article": "19"}], all_required=True
    )
    assert not r.is_hit(question, [_chunk(article="33")])
    assert r.is_hit(question, [_chunk(article="33"), _chunk(reg="DORA", article="19")])


def test_nothing_retrieved_is_a_miss():
    assert not r.is_hit(_question([{"reg": "GDPR", "article": "33"}]), [])


# Running a variant


def test_evaluate_skips_questions_without_sources_and_passes_the_filter():
    questions = [
        _question([{"reg": "GDPR", "article": "33"}], id="with", lang="de"),
        _question(None, id="without", expected_behavior="tool"),
    ]
    calls = []

    def search(text, filters):
        calls.append((text, filters))
        return [_chunk(lang="de")]

    results = r.evaluate(questions, search, r.Variant("lang_filter", True))
    assert calls == [("How fast?", {"lang": "de"})]
    assert [(x.id, x.hits) for x in results] == [("with", {5: True, 10: True})]


def test_no_filter_variant_passes_none():
    calls = []
    r.evaluate(
        [_question([{"reg": "GDPR", "article": "33"}])],
        lambda text, filters: calls.append(filters) or [],
        r.Variant("no_filter", False),
    )
    assert calls == [None]


def _evaluate_ranked(position: int, total: int = 12) -> r.QuestionResult:
    """The only matching chunk at rank `position` (1-based) among `total` results."""
    ranked = [_chunk(article=str(100 + n)) for n in range(total)]
    ranked[position - 1] = _chunk(article="33")
    return r.evaluate(
        [_question([{"reg": "GDPR", "article": "33"}])],
        lambda text, filters: ranked,
        r.VARIANTS[0],
    )[0]


@pytest.mark.parametrize(
    ("position", "hits"),
    [
        (1, {5: True, 10: True}),
        (5, {5: True, 10: True}),
        (6, {5: False, 10: True}),
        (10, {5: False, 10: True}),
        (11, {5: False, 10: False}),
    ],
)
def test_recall_at_5_and_10_come_from_the_same_ranking(position, hits):
    assert _evaluate_ranked(position).hits == hits


def test_only_the_top_ten_are_kept():
    assert len(_evaluate_ranked(1).chunk_ids) == 10


def test_queries_ask_for_the_largest_cutoff():
    assert r.NUM_RESULTS == 10
    assert r.CUTOFFS == (5, 10)


def test_variants_add_the_language_then_the_unit_types():
    question = _question([{"reg": "GDPR", "article": "33"}], lang="de")
    assert [v.name for v in r.VARIANTS] == ["no_filter", "lang_filter", "lang_articles"]
    assert [v.filters(question) for v in r.VARIANTS] == [
        None,
        {"lang": "de"},
        {"lang": "de", "unit_type": ["article", "annex"]},
    ]
    assert [v.filter_param for v in r.VARIANTS] == [
        "none",
        "lang=<question lang>",
        "lang=<question lang>, unit_type in (article, annex)",
    ]


# Recall


def _result(hit: bool, category="gdpr", lang="en", hit_at_10=None) -> r.QuestionResult:
    hits = {5: hit, 10: hit if hit_at_10 is None else hit_at_10}
    return r.QuestionResult("q", lang, category, "GDPR Art. 33", [], hits)


def test_recall_overall_per_category_and_per_language():
    results = [
        _result(True, "gdpr", "en"),
        _result(False, "gdpr", "de", hit_at_10=True),
        _result(True, "dora", "en"),
        _result(True, "dora", "en"),
    ]
    assert r.recall_metrics(results) == {
        "recall_at_10": 1.0,
        "recall_at_10_category_dora": 1.0,
        "recall_at_10_category_gdpr": 1.0,
        "recall_at_10_lang_de": 1.0,
        "recall_at_10_lang_en": 1.0,
        "recall_at_5": 0.75,
        "recall_at_5_category_dora": 1.0,
        "recall_at_5_category_gdpr": 0.5,
        "recall_at_5_lang_de": 0.0,
        "recall_at_5_lang_en": 1.0,
    }


def test_recall_of_nothing_is_an_error_not_zero():
    with pytest.raises(ValueError, match="no questions"):
        r.recall_metrics([])


def test_metric_keys_are_valid_mlflow_keys():
    keys = r.recall_metrics([_result(True, "false_premise")])
    assert all(key.replace("_", "").isalnum() for key in keys)


# Parsing and output


def test_parse_results_uses_the_manifest_column_names():
    response = {
        "manifest": {
            "column_count": 7,
            "columns": [{"name": n} for n in [*r.RESULT_COLUMNS[::-1], "score"]],
        },
        "result": {"row_count": 1, "data_array": [["1", "33", "article", "en", "GDPR", "c1", 0.9]]},
    }
    assert r.parse_results(response) == [
        r.Retrieved(chunk_id="c1", reg="GDPR", lang="en", unit_type="article", article="33")
    ]


def test_parse_results_of_an_empty_response():
    assert r.parse_results({"manifest": {"columns": []}, "result": {"row_count": 0}}) == []


def test_expected_sources_are_described_for_the_csv():
    any_of = _question([{"reg": "AIA", "article": "6"}, {"reg": "AIA", "annex": "III"}])
    all_of = _question(
        [{"reg": "GDPR", "article": "33"}, {"reg": "DORA", "article": "19"}], all_required=True
    )
    assert r.describe_sources(any_of) == "AIA Art. 6 OR AIA Annex III"
    assert r.describe_sources(all_of) == "GDPR Art. 33 AND DORA Art. 19"


def test_csv_has_one_row_per_question(tmp_path):
    path = tmp_path / "per_question.csv"
    result = r.QuestionResult(
        "gdpr-02", "en", "gdpr", "GDPR Art. 33", ["a", "b"], {5: False, 10: True}
    )
    r.write_csv([result], path)
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows == [
        {
            "id": "gdpr-02",
            "lang": "en",
            "expected": "GDPR Art. 33",
            "top10_chunk_ids": "a b",
            "hit_at_5": "False",
            "hit_at_10": "True",
        }
    ]


def test_every_source_of_the_real_question_set_can_be_matched():
    # Regs and annex numbers as the index stores them; catches a typo such as "AI Act" or "3".
    regs = {"GDPR", "AIA", "DORA", "OMNIBUS_AI"}
    questions = r.evaluable(load_questions().questions)
    assert len(questions) == 34
    for question in questions:
        for source in question.expected_sources:
            assert source.reg in regs, question.id
            if source.annex is not None:
                assert set(source.annex) <= set("IVXLC"), question.id
