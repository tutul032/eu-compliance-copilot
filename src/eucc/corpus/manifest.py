"""Loader and validator for the corpus manifest (corpus/manifest.yaml).

Checks on load: every field known, ids unique and ending in their language, every source present
in both languages, and each URL naming its own CELEX and uppercase language.
"""

from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

Lang = Literal["en", "de"]
LANGS: frozenset[str] = frozenset({"en", "de"})
# Cellar negotiates language with ISO 639-2 codes, not the two-letter codes EUR-Lex uses.
FETCH_LANGS: dict[str, str] = {"en": "eng", "de": "deu"}

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "corpus" / "manifest.yaml"


class ExpectedUnits(BaseModel):
    """How many units the extractor should find in this document."""

    model_config = ConfigDict(extra="forbid")

    articles: int
    recitals: int
    annexes: int


class ManifestEntry(BaseModel):
    """One document in one language."""

    model_config = ConfigDict(extra="forbid")

    id: str
    reg: Literal["GDPR", "AIA", "DORA", "OMNIBUS_AI"]
    celex: str
    lang: Lang
    role: Literal["articles", "recitals", "articles+recitals", "amendments+recitals"]
    version_date: date
    revision: str | None
    source_url: str
    format: Literal["html"]
    expected_units: ExpectedUnits
    # Filled by the extractor (S1-3).
    sha256: str | None

    @model_validator(mode="after")
    def _id_ends_in_lang(self) -> "ManifestEntry":
        if not self.id.endswith(f"-{self.lang}"):
            raise ValueError(f"{self.id}: id must end in -{self.lang}")
        return self

    @model_validator(mode="after")
    def _url_names_celex_and_lang(self) -> "ManifestEntry":
        if f"/{self.lang.upper()}/" not in self.source_url:
            raise ValueError(f"{self.id}: source_url must contain /{self.lang.upper()}/")
        # endswith, so that 02016R0679 doesn't match a URL for 02016R0679-20160504.
        if not self.source_url.endswith(f"CELEX:{self.celex}"):
            raise ValueError(f"{self.id}: source_url must end in CELEX:{self.celex}")
        return self

    @property
    def source(self) -> str:
        """The id without its language suffix: the same for both languages of a document."""
        return self.id.removesuffix(f"-{self.lang}")

    @property
    def fetch_url(self) -> str:
        """Where the extractor downloads from (Cellar); source_url stays for human citations."""
        return f"http://publications.europa.eu/resource/celex/{self.celex}"

    @property
    def fetch_lang(self) -> str:
        """The Accept-Language value that selects this entry's language on Cellar."""
        return FETCH_LANGS[self.lang]


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[ManifestEntry]

    @model_validator(mode="after")
    def _unique_ids(self) -> "Manifest":
        counts = Counter(e.id for e in self.entries)
        duplicates = sorted(eid for eid, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate manifest ids: {', '.join(duplicates)}")
        return self

    @model_validator(mode="after")
    def _every_source_in_both_languages(self) -> "Manifest":
        langs_by_source: dict[str, set[str]] = defaultdict(set)
        for entry in self.entries:
            langs_by_source[entry.source].add(entry.lang)
        incomplete = sorted(s for s, langs in langs_by_source.items() if langs != LANGS)
        if incomplete:
            raise ValueError(f"sources missing a language: {', '.join(incomplete)}")
        return self


def load_manifest(path: Path | str = DEFAULT_PATH) -> Manifest:
    """Load and validate the manifest; raises pydantic.ValidationError if it is invalid."""
    with Path(path).open(encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))
