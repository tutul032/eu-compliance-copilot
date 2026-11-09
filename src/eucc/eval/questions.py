"""Loader and validator for the evaluation question set (eval/questions.yaml).

Checks on load: every field known, ids unique, `expected_behavior` one of the allowed values, and
`expected_sources` present unless the behavior is `tool`, `refuse` or `out_of_scope`.
"""

import re
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

ExpectedBehavior = Literal[
    "answer", "tool", "answer+tool", "out_of_scope", "refuse", "correct_premise"
]

# Behaviors whose answer cites no regulation text, so expected_sources may be omitted.
BEHAVIORS_WITHOUT_SOURCES: frozenset[str] = frozenset({"tool", "refuse", "out_of_scope"})

# In an installed wheel the file sits next to this module (pyproject.toml: force-include); in the
# repo, including editable installs, it is eval/questions.yaml.
_PACKAGED_PATH = Path(__file__).resolve().with_name("questions.yaml")
_REPO_PATH = Path(__file__).resolve().parents[3] / "eval" / "questions.yaml"
DEFAULT_PATH = _PACKAGED_PATH if _PACKAGED_PATH.is_file() else _REPO_PATH

# An article as the index stores it: 33, 4a, 75b. No wildcards: a source names one provision.
ARTICLE_NUMBER = re.compile(r"\d+[a-z]*")


class Source(BaseModel):
    """A citation the answer must contain: an article or an annex of one regulation."""

    model_config = ConfigDict(extra="forbid")

    reg: str
    article: str | None = None
    annex: str | None = None

    @model_validator(mode="after")
    def _article_or_annex(self) -> "Source":
        if (self.article is None) == (self.annex is None):
            raise ValueError("a source needs exactly one of article or annex")
        if self.article is not None and not ARTICLE_NUMBER.fullmatch(self.article):
            raise ValueError(
                f"{self.reg} article {self.article!r}: expected an article number such as "
                "'33' or '4a'; wildcards like 'any' are not sources"
            )
        return self


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    lang: Literal["en", "de"]
    category: str
    question: str
    expected_behavior: ExpectedBehavior
    expected_sources: list[Source] | None = None
    # True: every expected source must be cited; False (default): any one of them is a hit.
    all_required: bool = False
    expected_tool: str | None = None
    # The query whose result a tool answer must match (eucc.jobs.eval_agent: data_correct).
    reference_sql: str | None = None
    # all: every non-zero number of the reference result must be in the answer; any: one is enough.
    data_match: Literal["all", "any"] = "all"
    key_facts: list[str] = Field(default_factory=list)
    notes: str | None = None
    reviewed: bool = False

    @model_validator(mode="after")
    def _sources_unless_exempt(self) -> "Question":
        if self.expected_behavior not in BEHAVIORS_WITHOUT_SOURCES and not self.expected_sources:
            raise ValueError(
                f"{self.id}: expected_sources is required for "
                f"expected_behavior {self.expected_behavior!r}"
            )
        return self


class QuestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # YAML reads `version: 0.1` as a float; keep it as the string it was written as.
    version: Annotated[str, BeforeValidator(str)]
    questions: list[Question]

    @model_validator(mode="after")
    def _unique_ids(self) -> "QuestionSet":
        counts = Counter(q.id for q in self.questions)
        duplicates = sorted(qid for qid, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate question ids: {', '.join(duplicates)}")
        return self


def load_questions(path: Path | str = DEFAULT_PATH) -> QuestionSet:
    """Load and validate the question set; raises pydantic.ValidationError if it is invalid."""
    with Path(path).open(encoding="utf-8") as f:
        return QuestionSet.model_validate(yaml.safe_load(f))
