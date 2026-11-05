from dataclasses import replace

import pytest
from conftest import fixture_bytes, make_entry

from eucc.chunk import MAX_WORDS, OVERLAP_WORDS, chunk_provision, header, split_words
from eucc.parse import Provision, parse_entry


def _provision(**overrides) -> Provision:
    fields = {
        "provision_id": "gdpr-en-article-33-1-2016-05-04",
        "reg": "GDPR",
        "celex": "02016R0679-20160504",
        "lang": "en",
        "version_date": "2016-05-04",
        "revision": None,
        "unit_type": "article",
        "article": "33",
        "paragraph": "1",
        "heading": "Notification",
        "text": "Some text.",
        "amended_by": [],
        "source_url": "https://example.test",
    }
    return Provision(**{**fields, **overrides})


def _words(count: int, start: int = 0) -> str:
    return " ".join(f"w{i}" for i in range(start, start + count))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "GDPR Art. 33(1) — Notification"),
        ({"lang": "de"}, "DSGVO Art. 33 Abs. 1 — Notification"),
        ({"paragraph": None}, "GDPR Art. 33 — Notification"),
        ({"lang": "de", "paragraph": None}, "DSGVO Art. 33 — Notification"),
        ({"reg": "AIA"}, "AI Act Art. 33(1) — Notification"),
        ({"reg": "AIA", "lang": "de"}, "KI-VO Art. 33 Abs. 1 — Notification"),
        ({"reg": "DORA", "lang": "de"}, "DORA Art. 33 Abs. 1 — Notification"),
        ({"reg": "OMNIBUS_AI"}, "AI Omnibus Art. 33(1) — Notification"),
        ({"reg": "OMNIBUS_AI", "lang": "de"}, "KI-Omnibus Art. 33 Abs. 1 — Notification"),
        (
            {"unit_type": "recital", "article": "85", "paragraph": None, "heading": None},
            "GDPR Recital 85",
        ),
        (
            {
                "unit_type": "recital",
                "article": "85",
                "paragraph": None,
                "heading": None,
                "lang": "de",
            },
            "DSGVO Erwägungsgrund 85",
        ),
        (
            {
                "unit_type": "annex",
                "article": "III",
                "paragraph": None,
                "reg": "AIA",
                "heading": "High-risk",
            },
            "AI Act Annex III — High-risk",
        ),
        (
            {
                "unit_type": "annex",
                "article": "III",
                "paragraph": None,
                "reg": "AIA",
                "lang": "de",
                "heading": "Hochrisiko",
            },
            "KI-VO Anhang III — Hochrisiko",
        ),
        (
            {
                "unit_type": "annex",
                "article": "XIV",
                "paragraph": None,
                "reg": "AIA",
                "heading": None,
            },
            "AI Act Annex XIV",
        ),
    ],
)
def test_header(overrides, expected):
    assert header(_provision(**overrides)) == expected


def test_chunk_text_is_header_newline_text():
    chunks = chunk_provision(_provision())
    assert [c.chunk_text for c in chunks] == ["GDPR Art. 33(1) — Notification\nSome text."]


def test_header_from_real_excerpt(lang):
    entry = make_entry("consolidated_gdpr", lang)
    provisions = parse_entry(entry, fixture_bytes("consolidated_gdpr", lang))
    paragraph = next(p for p in provisions if p.article == "43" and p.paragraph == "3")
    expected = {
        "en": "GDPR Art. 43(3) — Certification bodies\nThe accreditation",
        "de": "DSGVO Art. 43 Abs. 3 — Zertifizierungsstellen\nDie Akkreditierung",
    }[lang]
    assert chunk_provision(paragraph)[0].chunk_text.startswith(expected)


def test_text_at_the_limit_is_not_split():
    assert split_words(_words(MAX_WORDS)) == [_words(MAX_WORDS)]


def test_text_over_the_limit_is_split_with_overlap():
    parts = split_words(_words(MAX_WORDS + 1))
    assert parts == [_words(MAX_WORDS), _words(OVERLAP_WORDS + 1, start=MAX_WORDS - OVERLAP_WORDS)]


def test_parts_cover_the_text_in_order_and_respect_the_limit():
    total = 1234
    parts = [part.split() for part in split_words(_words(total))]
    assert all(len(part) <= MAX_WORDS for part in parts)
    for before, after in zip(parts, parts[1:], strict=False):
        assert before[-OVERLAP_WORDS:] == after[:OVERLAP_WORDS]
    rebuilt = parts[0] + [word for part in parts[1:] for word in part[OVERLAP_WORDS:]]
    assert rebuilt == _words(total).split()


def test_no_trailing_part_is_only_overlap():
    # Exactly filling the last part must not add one that repeats its tail.
    step = MAX_WORDS - OVERLAP_WORDS
    assert len(split_words(_words(MAX_WORDS + step))) == 2


def test_split_chunks_get_a_suffix_and_repeat_the_header():
    chunks = chunk_provision(_provision(text=_words(800)))
    assert [c.chunk_id for c in chunks] == [
        "gdpr-en-article-33-1-2016-05-04",
        "gdpr-en-article-33-1-2016-05-04-p2",
        "gdpr-en-article-33-1-2016-05-04-p3",
    ]
    assert all(c.chunk_text.startswith("GDPR Art. 33(1) — Notification\n") for c in chunks)
    assert all(c.provision_id == "gdpr-en-article-33-1-2016-05-04" for c in chunks)
    assert [c.part for c in chunks] == [1, 2, 3]


def test_chunk_id_is_lowercase_with_0_for_no_paragraph(lang):
    provisions = parse_entry(
        make_entry("consolidated_aia", lang), fixture_bytes("consolidated_aia", lang)
    )
    annex = next(p for p in provisions if p.unit_type == "annex")
    assert chunk_provision(annex)[0].chunk_id == f"aia-{lang}-annex-i-0-2026-07-27"


def test_chunk_id_for_inserted_article_and_two_part_reg():
    provision = _provision(
        provision_id="omnibus_ai-en-article-4a-1-2026-07-24", reg="OMNIBUS_AI", article="4a"
    )
    assert chunk_provision(provision)[0].chunk_id == "omnibus_ai-en-article-4a-1-2026-07-24"


def test_chunk_id_is_stable_across_runs_and_edits(lang):
    def ids() -> list[str]:
        provisions = parse_entry(
            make_entry("consolidated_gdpr", lang), fixture_bytes("consolidated_gdpr", lang)
        )
        return [c.chunk_id for p in provisions for c in chunk_provision(p)]

    assert ids() == ids()
    original = _provision()
    edited = replace(original, text="Different words.", heading="Another title")
    assert chunk_provision(original)[0].chunk_id == chunk_provision(edited)[0].chunk_id


def test_chunk_id_changes_with_the_version():
    original = _provision()
    later = _provision(provision_id="gdpr-en-article-33-1-2026-01-01", version_date="2026-01-01")
    assert chunk_provision(original)[0].chunk_id != chunk_provision(later)[0].chunk_id
