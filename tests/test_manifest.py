import pytest
import yaml
from pydantic import ValidationError

from eucc.corpus.manifest import Manifest, load_manifest


def _entry(lang: str = "en", **overrides) -> dict:
    entry = {
        "id": f"gdpr-consolidated-{lang}",
        "reg": "GDPR",
        "celex": "02016R0679-20160504",
        "lang": lang,
        "role": "articles",
        "version_date": "2016-05-04",
        "revision": None,
        "source_url": (
            f"https://eur-lex.europa.eu/legal-content/{lang.upper()}/TXT/HTML/"
            "?uri=CELEX:02016R0679-20160504"
        ),
        "format": "html",
        "expected_units": {"articles": 1, "recitals": 0, "annexes": 0},
        "sha256": None,
    }
    entry.update(overrides)
    return entry


def test_repo_manifest_is_valid():
    assert len(load_manifest().entries) == 12


def test_load_manifest_reads_path(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"entries": [_entry("en"), _entry("de")]}), encoding="utf-8")
    assert [e.source for e in load_manifest(path).entries] == ["gdpr-consolidated"] * 2


def test_duplicate_id_rejected():
    with pytest.raises(ValidationError, match="duplicate manifest ids: gdpr-consolidated-en"):
        Manifest.model_validate({"entries": [_entry("en"), _entry("en"), _entry("de")]})


@pytest.mark.parametrize("present", ["en", "de"])
def test_missing_language_rejected(present):
    with pytest.raises(ValidationError, match="sources missing a language: gdpr-consolidated"):
        Manifest.model_validate({"entries": [_entry(present)]})


def test_unknown_language_rejected():
    with pytest.raises(ValidationError, match="lang"):
        Manifest.model_validate({"entries": [_entry("fr")]})


def test_id_must_end_in_language():
    with pytest.raises(ValidationError, match="id must end in -en"):
        Manifest.model_validate({"entries": [_entry("en", id="gdpr-consolidated")]})


@pytest.mark.parametrize(
    "url",
    [
        "https://eur-lex.europa.eu/legal-content/en/TXT/HTML/?uri=CELEX:02016R0679-20160504",
        "https://eur-lex.europa.eu/legal-content/DE/TXT/HTML/?uri=CELEX:02016R0679-20160504",
    ],
)
def test_url_needs_uppercase_language(url):
    with pytest.raises(ValidationError, match="source_url must contain /EN/"):
        Manifest.model_validate({"entries": [_entry("en", source_url=url)]})


def test_url_needs_full_celex():
    url = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02016R0679"
    with pytest.raises(ValidationError, match="source_url must end in CELEX"):
        Manifest.model_validate({"entries": [_entry("en", source_url=url)]})


def test_unknown_field_rejected():
    with pytest.raises(ValidationError, match="sha_256"):
        Manifest.model_validate({"entries": [_entry("en", sha_256=None)]})


@pytest.mark.parametrize(("lang", "fetch_lang"), [("en", "eng"), ("de", "deu")])
def test_fetch_properties_are_computed(lang, fetch_lang):
    entry = Manifest.model_validate({"entries": [_entry("en"), _entry("de")]}).entries[
        0 if lang == "en" else 1
    ]
    assert entry.fetch_url == "http://publications.europa.eu/resource/celex/02016R0679-20160504"
    assert entry.fetch_lang == fetch_lang
    assert entry.source_url.startswith("https://eur-lex.europa.eu/")


def test_fetch_url_is_not_a_yaml_field():
    with pytest.raises(ValidationError, match="fetch_url"):
        Manifest.model_validate({"entries": [_entry("en", fetch_url="http://x"), _entry("de")]})
