"""UI helpers of the Streamlit app (app/app.py): texts, badges, citation links, configuration.

Pure functions without Streamlit, so they run in unit tests. The UI language (DE/EN switch) only
changes these texts and the example questions; the answer's language is still detected from the
question (eucc.rag.detect_language).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from eucc import rag
from eucc.index import EMBEDDING_MODEL, INDEX_TABLE, full_name
from eucc.tools import sql as sql_tool

UI_LANGS = ("en", "de")
DEFAULT_UI_LANG = "en"

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "title": "EU Compliance Copilot",
        "subtitle": "GDPR, AI Act and DORA, with the company's energy data.",
        "language": "Language",
        "examples": "Example questions",
        "placeholder": "Ask about the GDPR, the AI Act, DORA or the energy data …",
        "thinking": "Searching the regulations and the data …",
        "warming": "Warming up data tools …",
        "warm_hint": "The first data question after idle may take ~2 min (warehouse start).",
        "warm_failed": "The data tools did not start: {error}",
        "citations": "Sources",
        "no_citations": "No cited sources.",
        "data": "Data rows",
        "no_rows": "The data query returned no rows.",
        "data_error": "The energy data tool failed: {error}",
        "details": "Details: SQL and regulation query",
        "sql": "SQL",
        "regulation_query": "Regulation query",
        "none": "(none)",
        "route": "Route",
        "route_regulation": "regulations",
        "route_data": "data",
        "route_keywords": "keyword fallback",
        "latency": "Latency",
        "failed": "The question could not be answered: {error}",
        "about": "Model {llm} · embeddings {embedding} · index {index}",
        "disclaimer": (
            "Information only, not legal advice. Answers come from the GDPR, the AI Act and "
            "DORA as published on EUR-Lex and may be incomplete or wrong; check the cited "
            "provisions and consult a qualified lawyer for decisions."
        ),
    },
    "de": {
        "title": "EU Compliance Copilot",
        "subtitle": "DSGVO, KI-Verordnung und DORA, mit den Energiedaten des Unternehmens.",
        "language": "Sprache",
        "examples": "Beispielfragen",
        "placeholder": "Fragen zu DSGVO, KI-Verordnung, DORA oder den Energiedaten …",
        "thinking": "Durchsuche die Verordnungen und die Daten …",
        "warming": "Datenwerkzeuge werden gestartet …",
        "warm_hint": (
            "Die erste Datenfrage nach einer Pause kann ~2 Min. dauern (Start des Warehouse)."
        ),
        "warm_failed": "Die Datenwerkzeuge sind nicht gestartet: {error}",
        "citations": "Quellen",
        "no_citations": "Keine zitierten Quellen.",
        "data": "Datenzeilen",
        "no_rows": "Die Datenabfrage hat keine Zeilen geliefert.",
        "data_error": "Das Energiedaten-Werkzeug ist fehlgeschlagen: {error}",
        "details": "Details: SQL und Rechtsabfrage",
        "sql": "SQL",
        "regulation_query": "Rechtsabfrage",
        "none": "(keine)",
        "route": "Route",
        "route_regulation": "Verordnungen",
        "route_data": "Daten",
        "route_keywords": "Stichwort-Fallback",
        "latency": "Antwortzeit",
        "failed": "Die Frage konnte nicht beantwortet werden: {error}",
        "about": "Modell {llm} · Embeddings {embedding} · Index {index}",
        "disclaimer": (
            "Nur zur Information, keine Rechtsberatung. Die Antworten beruhen auf DSGVO, "
            "KI-Verordnung und DORA in der auf EUR-Lex veröffentlichten Fassung und können "
            "unvollständig oder falsch sein; prüfen Sie die zitierten Vorschriften und ziehen Sie "
            "für Entscheidungen eine qualifizierte Anwältin oder einen qualifizierten Anwalt hinzu."
        ),
    },
}

# Four per language: law only, data only, law + data (the load forecast of ADR-0006), DORA.
EXAMPLES: dict[str, list[str]] = {
    "en": [
        "How quickly must a personal data breach be notified to the supervisory authority?",
        "What was the average DE-LU day-ahead price yesterday?",
        "Our day-ahead load forecast supports grid operations. Could it be high-risk under the "
        "AI Act, and what is its current error?",
        "What must a financial entity do after a major ICT-related incident under DORA?",
    ],
    "de": [
        "Wie schnell muss eine Verletzung des Schutzes personenbezogener Daten der "
        "Aufsichtsbehörde gemeldet werden?",
        "Wie hoch war der durchschnittliche Day-Ahead-Preis DE-LU gestern?",
        "Unsere Day-Ahead-Lastprognose unterstützt den Netzbetrieb. Ist sie nach der "
        "KI-Verordnung hochriskant, und wie hoch ist ihr aktueller Fehler?",
        "Was muss ein Finanzunternehmen nach DORA nach einem schwerwiegenden IKT-bezogenen "
        "Vorfall tun?",
    ],
}

# status -> (st.badge color, Material icon). Unknown statuses get the fallback.
BADGES: dict[str, tuple[str, str]] = {
    "answered": ("green", ":material/check_circle:"),
    rag.UNSUPPORTED_STATUS: ("orange", ":material/report:"),
    "out_of_scope": ("blue", ":material/info:"),
    "refused": ("violet", ":material/block:"),
    rag.ERROR_STATUS: ("red", ":material/error:"),
}
BADGE_FALLBACK = ("gray", ":material/help:")
STATUS_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "answered": "Answered",
        rag.UNSUPPORTED_STATUS: "Not supported by the sources",
        "out_of_scope": "Out of scope",
        "refused": "Declined",
        rag.ERROR_STATUS: "Error",
    },
    "de": {
        "answered": "Beantwortet",
        rag.UNSUPPORTED_STATUS: "Nicht durch die Quellen gestützt",
        "out_of_scope": "Außerhalb des Umfangs",
        "refused": "Abgelehnt",
        rag.ERROR_STATUS: "Fehler",
    },
}

# Short names as each language cites them; the codes are the corpus's reg values.
REGULATION_NAMES: dict[str, dict[str, str]] = {
    "en": {"GDPR": "GDPR", "AIA": "AI Act", "DORA": "DORA"},
    "de": {"GDPR": "DSGVO", "AIA": "KI-VO", "DORA": "DORA"},
}
UNIT_NAMES: dict[str, dict[str, str]] = {
    "en": {"article": "Art.", "annex": "Annex"},
    "de": {"article": "Art.", "annex": "Anhang"},
}

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>$])")


def ui_lang(lang: str | None) -> str:
    """A supported UI language; anything else (None from a cleared switch) is English."""
    return lang if lang in UI_LANGS else DEFAULT_UI_LANG


def text(lang: str | None, key: str, **values: Any) -> str:
    template = TEXTS[ui_lang(lang)][key]
    return template.format(**values) if values else template


def badge(status: str, lang: str | None) -> tuple[str, str, str]:
    """(label, color, icon) for st.badge."""
    color, icon = BADGES.get(status, BADGE_FALLBACK)
    label = STATUS_LABELS[ui_lang(lang)].get(status, status)
    return label, color, icon


def escape_markdown(value: str) -> str:
    r"""Backslash-escape what Streamlit markdown would turn into links, emphasis, HTML or LaTeX."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", value)


def answer_markdown(answer: str) -> str:
    """The answer as Streamlit markdown: only $ (LaTeX) is escaped, the model's markdown stays."""
    return answer.replace("$", r"\$")


def citation_header(citation: Mapping[str, Any], lang: str | None = None) -> str:
    """DSGVO Art. 33(1) — Meldung … : in the citation's language (that of its heading)."""
    language = ui_lang(citation.get("lang") or lang)
    reg = REGULATION_NAMES[language].get(citation["reg"], citation["reg"])
    unit = citation.get("unit_type") or "article"
    unit_name = UNIT_NAMES[language].get(unit, UNIT_NAMES[language]["article"])
    paragraph = citation.get("paragraph")
    where = f"{unit_name} {citation['article']}"
    if unit != "annex" and paragraph:
        where += f"({paragraph})"
    heading = citation.get("heading")
    return f"{reg} {where}" + (f" — {heading}" if heading else "")


def citation_markdown(citation: Mapping[str, Any], lang: str | None = None) -> str:
    r"""\[1\] [header](EUR-Lex URL); the header as plain text when there is no URL."""
    header = escape_markdown(citation_header(citation, lang))
    number = escape_markdown(f"[{citation['n']}]")
    url = citation.get("source_url")
    if not url:
        return f"{number} {header}"
    # ) and spaces would end the link target early.
    target = str(url).replace(" ", "%20").replace(")", "%29")
    return f"{number} [{header}]({target})"


# SQL types shown with 2 decimals in the data table; integers and strings stay as they are.
DECIMAL_TYPES = frozenset({"FLOAT", "DOUBLE", "DECIMAL"})
DECIMAL_FORMAT = "%.2f"


def decimal_columns(data: Mapping[str, Any] | None) -> list[str]:
    """The columns to format with 2 decimals, by the warehouse's type (DECIMAL(10,4) too)."""
    if not data or "error" in data:
        return []
    columns, types = data.get("columns") or [], data.get("types") or []
    return [
        column
        for column, type_name in zip(columns, types, strict=False)
        if str(type_name).upper().split("(")[0] in DECIMAL_TYPES
    ]


def data_rows(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The tool's rows as records for st.dataframe, labelled D1, D2 … as the answer cites them."""
    if not data or "error" in data:
        return []
    columns = list(data.get("columns") or [])
    return [
        {"#": f"D{n}", **dict(zip(columns, row, strict=False))}
        for n, row in enumerate(data.get("rows") or [], start=1)
    ]


def route_text(route: Mapping[str, Any] | None, lang: str | None) -> str:
    """'regulations + data', with the keyword fallback named when the router fell back."""
    route = route or {}
    parts = [
        text(lang, key)
        for key, needed in (
            ("route_regulation", route.get("needs_regulation")),
            ("route_data", route.get("needs_data")),
        )
        if needed
    ]
    label = " + ".join(parts) or text(lang, "none")
    if route.get("method") == "keywords":
        label += f" ({text(lang, 'route_keywords')})"
    return label


def latency_text(latency_ms: int | float | None, lang: str | None) -> str:
    """Seconds with one decimal, decimal comma in German: '9.8 s' / '9,8 s'."""
    if latency_ms is None:
        return "–"
    seconds = f"{latency_ms / 1000:.1f}"
    return f"{seconds.replace('.', ',') if ui_lang(lang) == 'de' else seconds} s"


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True)
class AppConfig:
    """The app's settings, from app/app.yaml's env (resource values via valueFrom).

    Locally nothing is set and the library defaults apply (compliance_dev, gpt-oss, the dev
    warehouse). Tracing is off unless EUCC_TRACING=1: the app's service principal has no
    permission on an MLflow experiment.
    """

    catalog: str
    schema: str
    index: str
    warehouse_id: str
    llm_endpoint: str
    embedding_endpoint: str  # The index embeds the queries with it; shown on the page.
    tracing: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AppConfig":
        catalog = env.get("EUCC_CATALOG") or rag.DEFAULT_CATALOG
        schema = env.get("EUCC_SCHEMA") or rag.DEFAULT_SCHEMA
        return cls(
            catalog=catalog,
            schema=schema,
            index=env.get("EUCC_INDEX") or full_name(catalog, schema, INDEX_TABLE),
            warehouse_id=env.get(sql_tool.WAREHOUSE_ENV) or sql_tool.SQL_WAREHOUSE_ID,
            llm_endpoint=env.get("EUCC_LLM_ENDPOINT") or rag.MODEL,
            embedding_endpoint=env.get("EUCC_EMBEDDING_ENDPOINT") or EMBEDDING_MODEL,
            tracing=env.get("EUCC_TRACING", "0").strip() == "1",
        )
