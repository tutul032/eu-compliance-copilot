"""Shared helpers: manifest entries and the excerpts in tests/fixtures/ (cut from real files)."""

from pathlib import Path

import pytest

from eucc.corpus.manifest import ManifestEntry

FIXTURES = Path(__file__).parent / "fixtures"

SOURCES = {
    "consolidated_gdpr": ("GDPR", "02016R0679-20160504", "articles", "2016-05-04"),
    "consolidated_aia": ("AIA", "02024R1689-20260727", "articles", "2026-07-27"),
    "original_gdpr": ("GDPR", "32016R0679", "recitals", "2016-05-04"),
    "original_dora": ("DORA", "32022R2554", "articles+recitals", "2022-12-27"),
}


def make_entry(source: str, lang: str, **overrides) -> ManifestEntry:
    reg, celex, role, version_date = SOURCES[source]
    fields = {
        "id": f"{source}-{lang}",
        "reg": reg,
        "celex": celex,
        "lang": lang,
        "role": role,
        "version_date": version_date,
        "revision": None,
        "source_url": (
            f"https://eur-lex.europa.eu/legal-content/{lang.upper()}/TXT/HTML/?uri=CELEX:{celex}"
        ),
        "format": "html",
        "expected_units": {"articles": 0, "recitals": 0, "annexes": 0},
        "sha256": None,
    }
    return ManifestEntry.model_validate({**fields, **overrides})


def fixture_bytes(source: str, lang: str) -> bytes:
    return (FIXTURES / f"{source}_{lang}.xhtml").read_bytes()


@pytest.fixture(params=["en", "de"])
def lang(request) -> str:
    return request.param


@pytest.fixture(autouse=True, scope="session")
def _no_tracing():
    """eucc.rag's @mlflow.trace would otherwise export spans to a local MLflow store."""
    import mlflow

    mlflow.tracing.disable()
    yield
