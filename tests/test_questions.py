import pytest
from pydantic import ValidationError

from eucc.eval.questions import QuestionSet, load_questions


def _question(**overrides) -> dict:
    q = {
        "id": "gdpr-01",
        "lang": "en",
        "category": "gdpr",
        "question": "What are the lawful bases?",
        "expected_behavior": "answer",
        "expected_sources": [{"reg": "GDPR", "article": "6"}],
        "key_facts": ["consent"],
    }
    q.update(overrides)
    return q


def _set(*questions: dict) -> dict:
    return {"version": 0.1, "questions": list(questions)}


def test_repo_question_set_is_valid():
    qs = load_questions()
    assert qs.version == "0.1"
    assert len(qs.questions) == 40


def test_duplicate_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate question ids: gdpr-01"):
        QuestionSet.model_validate(_set(_question(), _question()))


def test_unknown_expected_behavior_rejected():
    with pytest.raises(ValidationError, match="expected_behavior"):
        QuestionSet.model_validate(_set(_question(expected_behavior="guess")))


@pytest.mark.parametrize("behavior", ["answer", "answer+tool", "correct_premise"])
def test_sources_required(behavior):
    with pytest.raises(ValidationError, match="expected_sources is required"):
        QuestionSet.model_validate(
            _set(_question(expected_behavior=behavior, expected_sources=None))
        )


@pytest.mark.parametrize("behavior", ["tool", "refuse", "out_of_scope"])
def test_sources_optional_for_exempt_behaviors(behavior):
    q = _question(expected_behavior=behavior)
    del q["expected_sources"]
    QuestionSet.model_validate(_set(q))


def test_empty_sources_rejected():
    with pytest.raises(ValidationError, match="expected_sources is required"):
        QuestionSet.model_validate(_set(_question(expected_sources=[])))


def test_source_needs_article_or_annex():
    with pytest.raises(ValidationError, match="exactly one of article or annex"):
        QuestionSet.model_validate(_set(_question(expected_sources=[{"reg": "GDPR"}])))


def test_unknown_field_rejected():
    with pytest.raises(ValidationError, match="expected_source"):
        QuestionSet.model_validate(_set(_question(expected_source=[])))
