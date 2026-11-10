"""eucc.ui: the Streamlit app's pure helpers (texts, badges, citation links, configuration)."""

import re

import pytest

from eucc import rag, ui

CITATION = {
    "n": 1,
    "reg": "GDPR",
    "lang": "de",
    "unit_type": "article",
    "article": "33",
    "paragraph": "1",
    "heading": "Meldung von Verletzungen des Schutzes personenbezogener Daten",
    "source_url": "https://eur-lex.europa.eu/legal-content/DE/TXT/HTML/?uri=CELEX:32016R0679#art_33",
}


# ---------------------------------------------------------------- texts


def test_both_languages_have_the_same_text_keys():
    assert ui.UI_LANGS == ("en", "de")
    assert set(ui.TEXTS) == set(ui.UI_LANGS)
    assert ui.TEXTS["en"].keys() == ui.TEXTS["de"].keys()
    assert all(value.strip() for texts in ui.TEXTS.values() for value in texts.values())


def test_placeholders_match_between_languages():
    def fields(value: str) -> set[str]:
        return set(re.findall(r"{(\w+)}", value))

    for key, english in ui.TEXTS["en"].items():
        assert fields(english) == fields(ui.TEXTS["de"][key]), key


def test_texts_are_in_their_language():
    assert ui.text("en", "examples") == "Example questions"
    assert ui.text("de", "examples") == "Beispielfragen"
    assert "Keine Rechtsberatung" in ui.text("de", "disclaimer")
    assert "Not legal advice" in ui.text("en", "disclaimer")


def test_text_fills_placeholders():
    assert ui.text("de", "data_error", error="timeout").endswith(": timeout")


@pytest.mark.parametrize("lang", [None, "fr", ""])
def test_unknown_ui_language_falls_back_to_english(lang):
    assert ui.ui_lang(lang) == "en"
    assert ui.text(lang, "examples") == "Example questions"


def test_four_examples_per_language_with_the_load_forecast_question():
    for lang in ui.UI_LANGS:
        assert len(ui.EXAMPLES[lang]) == 4
        assert len(set(ui.EXAMPLES[lang])) == 4
    assert any("load forecast" in q for q in ui.EXAMPLES["en"])
    assert any("Lastprognose" in q for q in ui.EXAMPLES["de"])


def test_examples_are_detected_in_their_language():
    # The answer language comes from the question, so a German example must read as German.
    for lang in ui.UI_LANGS:
        assert {rag.detect_language(q) for q in ui.EXAMPLES[lang]} == {lang}


# ---------------------------------------------------------------- badges


@pytest.mark.parametrize(
    ("status", "color"),
    [
        ("answered", "green"),
        ("unsupported", "orange"),
        ("out_of_scope", "blue"),
        ("refused", "violet"),
        ("error", "red"),
    ],
)
def test_badge_colors(status, color):
    _, badge_color, icon = ui.badge(status, "en")
    assert badge_color == color
    assert icon.startswith(":material/")


def test_every_status_the_agent_returns_has_a_badge_and_labels():
    statuses = {*rag.STATUSES, rag.UNSUPPORTED_STATUS, rag.ERROR_STATUS}
    assert statuses == set(ui.BADGES)
    for lang in ui.UI_LANGS:
        assert statuses == set(ui.STATUS_LABELS[lang])


def test_badge_labels_are_localized():
    assert ui.badge("answered", "en")[0] == "Answered"
    assert ui.badge("answered", "de")[0] == "Beantwortet"
    assert ui.badge("refused", "de")[0] == "Abgelehnt"


def test_unknown_status_gets_the_fallback_badge_with_its_own_name():
    assert ui.badge("weird", "de") == ("weird", *ui.BADGE_FALLBACK)


# ---------------------------------------------------------------- citations


def test_citation_header_uses_the_citation_language():
    assert ui.citation_header(CITATION, "en") == (
        "DSGVO Art. 33(1) — Meldung von Verletzungen des Schutzes personenbezogener Daten"
    )
    english = {**CITATION, "lang": "en", "heading": "Notification of a personal data breach"}
    assert ui.citation_header(english, "de") == (
        "GDPR Art. 33(1) — Notification of a personal data breach"
    )


def test_citation_header_of_an_annex_and_of_an_expanded_article():
    annex = {**CITATION, "reg": "AIA", "unit_type": "annex", "article": "III", "heading": None}
    assert ui.citation_header(annex) == "KI-VO Anhang III"
    assert ui.citation_header({**annex, "lang": "en"}) == "AI Act Annex III"
    whole_article = {**CITATION, "reg": "DORA", "article": "19", "paragraph": None}
    assert ui.citation_header(whole_article).startswith("DORA Art. 19 — ")


def test_citation_header_without_a_language_uses_the_ui_language():
    citation = {**CITATION, "lang": None, "heading": None}
    assert ui.citation_header(citation, "en") == "GDPR Art. 33(1)"


def test_citation_markdown_links_the_header():
    line = ui.citation_markdown({**CITATION, "heading": None})
    assert line == rf"\[1\] [DSGVO Art. 33(1)]({CITATION['source_url']})"


def test_citation_markdown_escapes_the_link_text_and_the_target():
    citation = {
        **CITATION,
        "heading": "Pflichten [Anbieter] *KI*",
        "source_url": "https://x.eu/a b(c)",
    }
    line = ui.citation_markdown(citation)
    assert r"Pflichten \[Anbieter\] \*KI\*" in line
    assert line.endswith("](https://x.eu/a%20b(c%29)")


def test_citation_markdown_without_url_is_plain_text():
    assert ui.citation_markdown({**CITATION, "source_url": None, "heading": None}) == (
        r"\[1\] DSGVO Art. 33(1)"
    )


def test_answer_markdown_escapes_only_dollars():
    assert ui.answer_markdown("**$5** per [1]") == r"**\$5** per [1]"


# ---------------------------------------------------------------- data, route, latency


def test_data_rows_are_labelled_as_the_answer_cites_them():
    data = {"columns": ["day", "avg_price"], "rows": [["2026-11-08", 242.13], ["2026-11-09", 1.0]]}
    assert ui.data_rows(data) == [
        {"#": "D1", "day": "2026-11-08", "avg_price": 242.13},
        {"#": "D2", "day": "2026-11-09", "avg_price": 1.0},
    ]


@pytest.mark.parametrize("data", [None, {"error": "boom"}, {"columns": ["a"], "rows": []}])
def test_no_data_rows(data):
    assert ui.data_rows(data) == []


def test_decimal_columns_are_the_floating_and_decimal_ones():
    data = {
        "columns": ["day", "rows", "mae_mwh", "mape_percent", "price"],
        "types": ["DATE", "LONG", "DOUBLE", "FLOAT", "DECIMAL(10,4)"],
        "rows": [["2026-11-08", 24, 1355.9852, 2.4567, 242.1349]],
    }
    assert ui.decimal_columns(data) == ["mae_mwh", "mape_percent", "price"]
    assert ui.DECIMAL_FORMAT % 1355.9852 == "1355.99"


@pytest.mark.parametrize("data", [None, {"error": "boom"}, {"columns": ["a"], "rows": [[1]]}])
def test_no_decimal_columns(data):
    assert ui.decimal_columns(data) == []


def test_route_text():
    both = {"needs_regulation": True, "needs_data": True, "method": "llm"}
    assert ui.route_text(both, "en") == "regulations + data"
    assert ui.route_text({**both, "needs_data": False, "method": "keywords"}, "de") == (
        "Verordnungen (Stichwort-Fallback)"
    )
    assert ui.route_text(None, "en") == "(none)"


def test_latency_text_uses_the_decimal_separator_of_the_language():
    assert ui.latency_text(9840, "en") == "9.8 s"
    assert ui.latency_text(9840, "de") == "9,8 s"
    assert ui.latency_text(None, "en") == "–"


# ---------------------------------------------------------------- configuration


def test_config_defaults_are_the_library_defaults():
    config = ui.AppConfig.from_env({})
    assert config == ui.AppConfig(
        catalog="workspace",
        schema="compliance_dev",
        index="workspace.compliance_dev.chunks_idx",
        warehouse_id="4228ff3a910b0b5e",
        llm_endpoint="databricks-gpt-oss-120b",
        embedding_endpoint="databricks-qwen3-embedding-0-6b",
        tracing=False,
    )


def test_config_from_env():
    config = ui.AppConfig.from_env(
        {
            "EUCC_CATALOG": "c",
            "EUCC_SCHEMA": "s",
            "EUCC_SQL_WAREHOUSE_ID": "w",
            "EUCC_LLM_ENDPOINT": "llm",
            "EUCC_EMBEDDING_ENDPOINT": "emb",
            "EUCC_TRACING": "1",
        }
    )
    assert config.index == "c.s.chunks_idx"
    assert (config.warehouse_id, config.llm_endpoint) == ("w", "llm")
    assert config.embedding_endpoint == "emb"
    assert config.tracing is True
    assert ui.AppConfig.from_env({"EUCC_INDEX": "a.b.idx"}).index == "a.b.idx"
    assert ui.AppConfig.from_env({"EUCC_TRACING": "0"}).tracing is False
