from dataclasses import replace

import pytest
from conftest import fixture_bytes, make_entry

from eucc.chunk import chunk_provision
from eucc.contract import ContractError, check_corpus, check_entry
from eucc.parse import parse_entry


def _build(source: str = "original_dora", lang: str = "en", articles: int = 3, recitals: int = 1):
    entry = make_entry(
        source,
        lang,
        expected_units={"articles": articles, "recitals": recitals, "annexes": 0},
    )
    provisions = parse_entry(entry, fixture_bytes(source, lang))
    chunks = [c for p in provisions for c in chunk_provision(p)]
    return entry, provisions, chunks


def test_matching_entry_passes(lang):
    check_entry(*_build(lang=lang))


def test_paragraphs_do_not_inflate_the_article_count():
    # Art. 5 has four paragraphs, and counts once among the three articles.
    entry, provisions, chunks = _build()
    assert len([p for p in provisions if p.unit_type == "article"]) > 3
    check_entry(entry, provisions, chunks)


def test_wrong_article_count_names_the_entry():
    entry, provisions, chunks = _build(articles=99)
    with pytest.raises(ContractError, match=r"original_dora-en: expected 99 articles, found 3"):
        check_entry(entry, provisions, chunks)


def test_wrong_recital_count_names_the_entry():
    entry, provisions, chunks = _build(recitals=106)
    with pytest.raises(ContractError, match=r"original_dora-en: expected 106 recitals, found 1"):
        check_entry(entry, provisions, chunks)


def test_annex_count_is_not_part_of_the_contract():
    entry = make_entry(
        "consolidated_aia", "en", expected_units={"articles": 5, "recitals": 0, "annexes": 13}
    )
    provisions = parse_entry(entry, fixture_bytes("consolidated_aia", "en"))
    check_entry(entry, provisions, [c for p in provisions for c in chunk_provision(p)])


def test_empty_provision_text_names_the_entry_and_the_provision():
    entry, provisions, chunks = _build()
    provisions[0] = replace(provisions[0], text="  ")
    with pytest.raises(ContractError, match=rf"original_dora-en: empty.*{provisions[0].article}"):
        check_entry(entry, provisions, chunks)


def test_empty_chunk_text_names_the_entry():
    entry, provisions, chunks = _build()
    chunks[0] = replace(chunks[0], text="")
    with pytest.raises(ContractError, match="original_dora-en: empty"):
        check_entry(entry, provisions, chunks)


def test_duplicate_chunk_id_names_the_entry_and_the_id():
    entry, provisions, chunks = _build()
    chunks.append(chunks[0])
    with pytest.raises(ContractError, match=rf"original_dora-en: duplicate.*{chunks[0].chunk_id}"):
        check_entry(entry, provisions, chunks)


def test_entries_with_distinct_ids_pass_the_corpus_check():
    en = _build(lang="en")[2]
    de = _build(lang="de")[2]
    check_corpus({"en-entry": en, "de-entry": de})


def test_chunk_id_shared_between_entries_names_both():
    chunks = _build()[2]
    with pytest.raises(ContractError, match=r"first, second: duplicate chunk_id"):
        check_corpus({"first": chunks, "second": chunks})
