"""Chunk: one chunk per provision, with a citation header; long texts are split with overlap."""

from dataclasses import dataclass

from eucc.parse import Provision

MAX_WORDS = 400
OVERLAP_WORDS = 50

REG_NAMES: dict[str, dict[str, str]] = {
    "en": {"GDPR": "GDPR", "AIA": "AI Act", "DORA": "DORA", "OMNIBUS_AI": "AI Omnibus"},
    "de": {"GDPR": "DSGVO", "AIA": "KI-VO", "DORA": "DORA", "OMNIBUS_AI": "KI-Omnibus"},
}
RECITAL_LABEL = {"en": "Recital", "de": "Erwägungsgrund"}
ANNEX_LABEL = {"en": "Annex", "de": "Anhang"}


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    provision_id: str
    part: int
    reg: str
    celex: str
    lang: str
    version_date: str
    revision: str | None
    unit_type: str
    article: str
    paragraph: str | None
    heading: str | None
    text: str
    chunk_text: str
    amended_by: list[str]
    source_url: str


def header(provision: Provision) -> str:
    """The citation in the entry's language: GDPR Art. 33(1) — <title>, DSGVO Art. 33 Abs. 1 ..."""
    lang = provision.lang
    name = REG_NAMES[lang][provision.reg]
    if provision.unit_type == "recital":
        citation = f"{name} {RECITAL_LABEL[lang]} {provision.article}"
    elif provision.unit_type == "annex":
        citation = f"{name} {ANNEX_LABEL[lang]} {provision.article}"
    else:
        citation = f"{name} Art. {provision.article}"
        if provision.paragraph:
            paragraph = provision.paragraph
            citation += f" Abs. {paragraph}" if lang == "de" else f"({paragraph})"
    return f"{citation} — {provision.heading}" if provision.heading else citation


def split_words(text: str, max_words: int = MAX_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    """Parts of at most max_words, each repeating the last overlap words of the one before."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    step = max_words - overlap
    parts = []
    start = 0
    while True:
        parts.append(" ".join(words[start : start + max_words]))
        if start + max_words >= len(words):
            return parts
        start += step


def chunk_provision(provision: Provision) -> list[Chunk]:
    """The first part keeps the provision's id; later parts add -p2, -p3 ..."""
    head = header(provision)
    chunks = []
    for number, part in enumerate(split_words(provision.text), start=1):
        suffix = f"-p{number}" if number > 1 else ""
        chunks.append(
            Chunk(
                chunk_id=f"{provision.provision_id}{suffix}",
                provision_id=provision.provision_id,
                part=number,
                reg=provision.reg,
                celex=provision.celex,
                lang=provision.lang,
                version_date=provision.version_date,
                revision=provision.revision,
                unit_type=provision.unit_type,
                article=provision.article,
                paragraph=provision.paragraph,
                heading=provision.heading,
                text=part,
                chunk_text=f"{head}\n{part}",
                amended_by=provision.amended_by,
                source_url=provision.source_url,
            )
        )
    return chunks
