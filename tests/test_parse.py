import re

import pytest
from conftest import fixture_bytes, make_entry

from eucc.parse import Provision, anchored_url, clean_text, parse_entry


def _parse(source: str, lang: str, **overrides) -> list[Provision]:
    return parse_entry(make_entry(source, lang, **overrides), fixture_bytes(source, lang))


def _article(provisions: list[Provision], article: str) -> list[Provision]:
    return [p for p in provisions if p.unit_type == "article" and p.article == article]


def _paragraph(provisions: list[Provision], article: str, paragraph: str | None) -> Provision:
    found = [p for p in _article(provisions, article) if p.paragraph == paragraph]
    assert len(found) == 1, (article, paragraph, found)
    return found[0]


def _xhtml(body: str) -> str:
    return f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'


def _consolidated_article(body: str) -> str:
    return _xhtml(
        '<div class="eli-subdivision" id="art_1">'
        '<p class="title-article-norm">Article 1</p>'
        '<div class="eli-title" id="art_1.tit_1"><p class="stitle-article-norm">Title</p></div>'
        f"{body}</div>"
    )


def _numbered(number: str, text: str) -> str:
    return (
        f'<div class="norm"><span class="no-parag">{number}&#160;&#160;</span>'
        f'<div class="norm inline-element">{text}</div></div>'
    )


# Consolidated articles


def test_numbered_paragraphs_come_from_the_markup(lang):
    # DE writes "(1)" where EN writes "1."; the number comes out the same.
    provisions = _parse("consolidated_gdpr", lang)
    assert [p.paragraph for p in _article(provisions, "43")] == [str(n) for n in range(1, 10)]


def test_row_carries_the_entry_and_the_title(lang):
    expected_title = {"en": "Certification bodies", "de": "Zertifizierungsstellen"}[lang]
    entry = make_entry("consolidated_gdpr", lang, revision="000.002")
    paragraph = _paragraph(parse_entry(entry, fixture_bytes("consolidated_gdpr", lang)), "43", "1")
    assert paragraph.provision_id == f"gdpr-{lang}-article-43-1-2016-05-04"
    assert paragraph.reg == "GDPR"
    assert paragraph.celex == "02016R0679-20160504"
    assert paragraph.lang == lang
    assert paragraph.version_date == "2016-05-04"
    assert paragraph.revision == "000.002"
    assert paragraph.unit_type == "article"
    assert paragraph.heading == expected_title
    assert paragraph.source_url == f"{entry.source_url}#art_43"


def test_number_is_removed_from_the_text(lang):
    for provision in _article(_parse("consolidated_gdpr", lang), "43"):
        assert not re.match(r"^\(?\d+[.)]", provision.text), provision.text[:30]


def test_later_subparagraphs_stay_with_their_paragraph(lang):
    # They are siblings of the numbered div.norm in the markup, not children of it.
    subparagraph = {"en": "Point (f) of the first subparagraph", "de": "Unterabsatz 1 Buchstabe f"}
    assert subparagraph[lang] in _paragraph(_parse("consolidated_gdpr", lang), "6", "1").text


def test_lists_keep_their_labels(lang):
    label = {"en": "(a) the data subject has given consent", "de": "a) Die betroffene Person"}
    assert label[lang] in _paragraph(_parse("consolidated_gdpr", lang), "6", "1").text


def test_article_without_numbered_paragraphs_is_one_unit(lang):
    # GDPR Art. 77 is numbered; AI Act Art. 105 only quotes a number from the act it amends.
    assert all(p.paragraph for p in _article(_parse("consolidated_gdpr", lang), "77"))
    assert [p.paragraph for p in _article(_parse("consolidated_aia", lang), "105")] == [None]


def test_quoted_number_is_not_a_paragraph_of_this_article(lang):
    # AI Act Art. 105 adds paragraph 5 to a directive: not "Art. 105(5)".
    quote = {"en": "‘5.", "de": "„(5)"}[lang]
    provision = _paragraph(_parse("consolidated_aia", lang), "105", None)
    assert quote in provision.text


def test_inserted_article_keeps_its_letter(lang):
    assert _article(_parse("consolidated_aia", lang), "4a")


def test_footnote_reference_is_dropped_but_not_an_exponent(lang):
    provisions = _parse("consolidated_aia", lang)
    assert "10^25" in _paragraph(provisions, "51", "2").text
    text = " ".join(p.text for p in _article(provisions, "40"))
    assert not re.search(r"\s\(\*?\d\)", text)
    assert "()" not in text
    if lang == "en":
        assert "of the European Parliament and of the Council and without undue delay" in text


# Markers


def test_inline_markers_are_stripped_and_kept(lang):
    expected = {"en": ["C1"], "de": ["C2"]}[lang]
    start = {"en": "The accreditation of", "de": "Die Akkreditierung von"}[lang]
    provision = _paragraph(_parse("consolidated_gdpr", lang), "43", "3")
    assert provision.amended_by == expected
    assert provision.text.startswith(start)


@pytest.mark.parametrize("source", ["consolidated_gdpr", "consolidated_aia"])
def test_no_marker_survives_in_any_text(source, lang):
    for provision in _parse(source, lang):
        assert not re.search("[►▼▲◄­ ]", provision.text), provision.provision_id
        assert "  " not in provision.text


def test_stripping_an_end_marker_leaves_no_space_before_punctuation():
    assert " , wenn" not in _paragraph(_parse("consolidated_gdpr", "de"), "77", "1").text
    assert "Verstoßes, wenn" in _paragraph(_parse("consolidated_gdpr", "de"), "77", "1").text


def test_block_marker_stays_in_force_across_paragraphs():
    document = _consolidated_article(
        '<p class="modref"><a title="x: REPLACED">▼M1</a></p>'
        + _numbered("1.", "One.")
        + _numbered("2.", "Two.")
        + '<p class="modref"><a title="x">▼B</a></p>'
        + _numbered("3.", "Three.")
    )
    provisions = parse_entry(make_entry("consolidated_gdpr", "en"), document)
    assert [p.amended_by for p in provisions] == [["M1"], ["M1"], []]


def test_block_marker_before_the_article_applies_to_it(lang):
    provisions = _article(_parse("consolidated_aia", lang), "4a")
    assert all(p.amended_by == ["M1"] for p in provisions)


def test_base_act_marker_is_not_an_amendment():
    document = _consolidated_article(
        '<p class="modref"><a title="x">▼B</a></p>' + _numbered("1.", "One.")
    )
    assert parse_entry(make_entry("consolidated_gdpr", "en"), document)[0].amended_by == []


def test_clean_text():
    assert clean_text("Daten­schutz") == "Datenschutz"
    assert clean_text("►C1 eins\xa0\n zwei ◄ drei") == "eins zwei drei"
    assert clean_text("►C1 Verstoßes ◄ , wenn") == "Verstoßes, wenn"
    assert clean_text("erhoben werden ►C2 ◄ , in dem") == "erhoben werden, in dem"
    assert clean_text("the text.’ ; (2)") == "the text.’; (2)"
    assert clean_text("up to EUR 750 000 .") == "up to EUR 750 000 ."  # As published.


# Deleted provisions


def test_deleted_paragraph_is_dropped(lang):
    # AI Act Art. 10(5) was deleted by M1; the numbering keeps its gap.
    provisions = _article(_parse("consolidated_aia", lang), "10")
    assert [p.paragraph for p in provisions] == ["1", "2", "3", "4", "6"]
    assert not any("—————" in p.text for p in provisions)


def test_deletion_is_not_attributed_to_the_paragraph_before_it(lang):
    provisions = _parse("consolidated_aia", lang)
    assert _paragraph(provisions, "10", "4").amended_by == []
    assert _paragraph(provisions, "10", "1").amended_by == ["M1"]
    assert _paragraph(provisions, "10", "6").amended_by == ["M1"]


@pytest.mark.parametrize("line", ["__________", "—————"])
def test_block_with_only_a_deletion_line_is_dropped(line):
    document = _consolidated_article(_numbered("1.", "Kept.") + _numbered("2.", line))
    provisions = parse_entry(make_entry("consolidated_gdpr", "en"), document)
    assert [p.paragraph for p in provisions] == ["1"]


def test_deleted_list_point_is_dropped_with_its_label():
    document = _consolidated_article(
        _numbered("1.", "Kept:")
        + '<div class="grid-container grid-list"><div class="list grid-list-column-1">'
        '<span>(a) </span></div><div class="grid-list-column-2"><p>__________</p></div></div>'
    )
    assert parse_entry(make_entry("consolidated_gdpr", "en"), document)[0].text == "Kept:"


def test_single_dash_bullets_are_not_deletions(lang):
    # Annex I and the OJ lists use one dash as a bullet: only a run of them means deleted.
    document = _consolidated_article('<p class="norm">— a bullet;</p>')
    provisions = parse_entry(make_entry("consolidated_gdpr", lang), document)
    assert provisions[0].text == "— a bullet;"


# Annexes


def test_annex_is_one_unit_with_its_title(lang):
    title = {
        "en": "List of Union harmonisation legislation",
        "de": "Liste der Harmonisierungsrechtsvorschriften der Union",
    }[lang]
    start = {"en": "Section A", "de": "Abschnitt A"}[lang]
    annexes = [p for p in _parse("consolidated_aia", lang) if p.unit_type == "annex"]
    assert [(a.article, a.paragraph, a.heading) for a in annexes] == [("I", None, title)]
    assert annexes[0].text.startswith(start)  # The titles are not repeated in the text.


def test_deletion_inside_an_annex_counts_as_amended(lang):
    # Annex I, Section A, point 1 was deleted by M1: the annex is the unit that changed.
    annex = next(p for p in _parse("consolidated_aia", lang) if p.unit_type == "annex")
    assert annex.amended_by == ["M1"]
    assert "—————" not in annex.text
    assert "2009/48/" in annex.text


# Original OJ acts


def test_recital_number_comes_from_the_id(lang):
    provisions = _parse("original_gdpr", lang)
    assert [(p.unit_type, p.article, p.paragraph, p.heading) for p in provisions] == [
        ("recital", "85", None, None)
    ]
    assert not provisions[0].text.startswith("(85)")
    assert provisions[0].provision_id == f"gdpr-{lang}-recital-85-0-2016-05-04"


def test_recitals_role_skips_the_articles_of_the_original(lang):
    assert not _article(_parse("original_gdpr", lang), "33")


def test_articles_role_skips_recitals(lang):
    entry = make_entry("original_dora", lang, role="articles")
    provisions = parse_entry(entry, fixture_bytes("original_dora", lang))
    assert not [p for p in provisions if p.unit_type == "recital"]
    assert _article(provisions, "5")


def test_both_units_for_articles_and_recitals_role(lang):
    provisions = _parse("original_dora", lang)
    assert [p.article for p in provisions if p.unit_type == "recital"] == ["2"]
    assert _article(provisions, "5")


def test_oj_numbered_paragraphs_keep_subparagraphs_and_lists(lang):
    provisions = _parse("original_dora", lang)
    assert [p.paragraph for p in _article(provisions, "5")] == ["1", "2", "3", "4"]
    paragraph = _paragraph(provisions, "5", "2")
    label = {"en": "(a) bear the ultimate responsibility", "de": "a) Das Leitungsorgan trägt"}[lang]
    assert label in paragraph.text
    assert not re.match(r"^\(?2[.)]", paragraph.text)
    assert paragraph.heading


def test_oj_article_without_numbered_paragraphs_is_one_unit(lang):
    provisions = _article(_parse("original_dora", lang), "64")
    assert [p.paragraph for p in provisions] == [None]


def test_oj_footnote_inside_an_article_is_not_text(lang):
    # Art. 59 carries the footnote for the regulation it refers to inside the article div.
    text = _paragraph(_parse("original_dora", lang), "59", None).text
    assert "(*1)" not in text
    assert "OJ L" not in text and "ABl. L" not in text


def test_oj_has_no_amended_by(lang):
    assert all(p.amended_by == [] for p in _parse("original_dora", lang))


URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679"


@pytest.mark.parametrize(
    ("unit_type", "number", "expected"),
    [
        ("article", "33", f"{URL}#art_33"),
        ("article", "4a", f"{URL}#art_4a"),
        ("recital", "85", f"{URL}#rct_85"),
        ("annex", "III", f"{URL}#anx_III"),
        ("article", None, URL),
        ("chapter", "1", URL),
    ],
)
def test_anchored_url(unit_type, number, expected):
    assert anchored_url(URL, unit_type, number) == expected


def test_anchored_url_replaces_an_existing_fragment():
    assert anchored_url(f"{URL}#art_33", "article", "34") == f"{URL}#art_34"
    assert anchored_url(anchored_url(URL, "annex", "I"), "annex", "I") == f"{URL}#anx_I"


@pytest.mark.parametrize(
    ("source", "unit_type", "prefix"),
    [
        ("consolidated_aia", "article", "art"),
        ("consolidated_aia", "annex", "anx"),
        ("original_gdpr", "recital", "rct"),
        ("original_dora", "recital", "rct"),
    ],
)
def test_every_provision_links_to_its_anchor_in_the_document(source, unit_type, prefix, lang):
    entry = make_entry(source, lang)
    provisions = [p for p in _parse(source, lang) if p.unit_type == unit_type]
    assert provisions
    for p in provisions:
        assert p.source_url == f"{entry.source_url}#{prefix}_{p.article}"
        # The anchor is an element id of the fixture, so the link lands on the provision.
        assert f'id="{prefix}_{p.article}"' in fixture_bytes(source, lang).decode("utf-8")


def test_inserted_article_anchor_keeps_its_letter(lang):
    provisions = _article(_parse("consolidated_aia", lang), "4a")
    assert provisions and all(p.source_url.endswith("#art_4a") for p in provisions)
