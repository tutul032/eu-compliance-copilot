"""Parse: turn one EUR-Lex document into provisions (articles, recitals, annexes).

Two markup variants, chosen by CELEX: consolidated texts (0...) use ELI classes and carry
amendment markers, original OJ acts (3...) use oj- classes. Numbers come from the markup, never
from counting. What the real files require, beyond the obvious:

- Later subparagraphs are siblings of the numbered paragraph, not inside it, so a numbered block
  owns everything up to the next one.
- A quoted number (‘5. / „(5)) belongs to the act being amended (AI Act Art. 105-109): it stays
  in the text and does not start a paragraph of this article.
- A block marker stays in force until the next one, also across paragraphs and articles.
- The files are XHTML with self-closed tags, which an HTML parser nests wrongly: parse as XML.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PageElement, PreformattedString

from eucc.corpus.manifest import ManifestEntry

UnitType = Literal["article", "recital", "annex"]

ROLE_UNITS: dict[str, frozenset[str]] = {
    "articles": frozenset({"article", "annex"}),
    "recitals": frozenset({"recital"}),
    "articles+recitals": frozenset({"article", "annex", "recital"}),
    "amendments+recitals": frozenset({"article", "annex", "recital"}),
}

ARTICLE_ID = re.compile(r"^art_(\d+[a-z]*)$")
RECITAL_ID = re.compile(r"^rct_(\d+)$")
ANNEX_ID = re.compile(r"^anx_([IVXLC]+)$")
# The element id prefix of each unit in the EUR-Lex HTML, so a citation links to the provision.
ANCHOR_PREFIX: dict[str, str] = {"article": "art", "recital": "rct", "annex": "anx"}
OJ_PARAGRAPH_ID = re.compile(r"^\d{3}\.\d{3}$")
# "1." in EN, "(1)" in DE. No leading quote on purpose: see the module docstring.
PARAGRAPH_NUMBER = re.compile(r"^(?:(\d+[a-z]*)\.|\((\d+[a-z]*)\))(?:\s+|$)")
RECITAL_NUMBER = re.compile(r"^\(\d+\)\s*")

MARKER = re.compile(r"[►▼▲]([A-Z]\d*)?")
MARKERS_BEFORE_PUNCTUATION = re.compile(r"\s*(?:(?:[►▼▲](?:[A-Z]\d*)?|◄)\s*)+(?=[,;:.)])")
BASE_ACT = "B"  # The act itself, not an amendment.
PLACEHOLDER = re.compile(r"[_—–]{3,}")
# A block holding nothing but an optional point label and the deletion line.
DELETED_BLOCK = re.compile(r"^(?:\(?\w{1,4}[.)]?)?\s*[_—–]{3,}$")
FOOTNOTE_REF = ""
FOOTNOTE_ANCHOR_ID = re.compile(r"^(?:src\.|ntc)")

BLOCK_TAGS = frozenset({"p", "div", "table", "tbody", "tr", "td", "br", "hr"})
SKIPPED_CLASSES = frozenset({"footnote", "oj-note"})
SUPERSCRIPT_CLASSES = frozenset({"superscript", "oj-super"})
HEADING_CLASSES = frozenset({"title-article-norm", "oj-ti-art", "eli-title"})
ANNEX_TITLE_CLASSES = frozenset({"title-annex-1", "title-annex-2"})


@dataclass(frozen=True)
class Provision:
    provision_id: str
    reg: str
    celex: str
    lang: str
    version_date: str
    revision: str | None
    unit_type: UnitType
    article: str
    paragraph: str | None
    heading: str | None
    text: str
    amended_by: list[str]
    source_url: str


def anchored_url(url: str, unit_type: str, number: str | None) -> str:
    """The document URL with the provision's anchor: #art_33, #art_4a, #rct_85, #anx_III.

    Any fragment already on the URL is replaced, so applying it twice changes nothing. An
    unknown unit type or a missing number gives the bare document URL.
    """
    base = url.split("#", 1)[0]
    prefix = ANCHOR_PREFIX.get(unit_type)
    return f"{base}#{prefix}_{number}" if prefix and number else base


def clean_text(raw: str) -> str:
    """Strip markers, footnote references and soft hyphens; collapse whitespace."""
    # Markers sit in spans with spaces of their own: "Verstoßes ◄ , wenn".
    text = MARKERS_BEFORE_PUNCTUATION.sub("", raw)
    text = MARKER.sub("", text)
    text = re.sub(rf"\s*\(?{FOOTNOTE_REF}\)?", "", text)
    text = re.sub(r"\s*◄", "", text)
    # OJ amending acts put the punctuation after a quotation in a block of its own: "’</p><p>;".
    text = re.sub(r"(?<=[’”“])\s+(?=[,;:.])", "", text)
    return " ".join(text.replace("­", "").split())


def _classes(tag: Tag) -> set[str]:
    return set(tag.get("class") or [])


def _marker_of(tag: Tag) -> str | None:
    found = MARKER.search(tag.get_text())
    return found.group(1) if found else None


def _is_marker_block(tag: Tag) -> bool:
    return "modref" in _classes(tag)


def _is_deletion_block(tag: Tag) -> bool:
    return _is_marker_block(tag) and PLACEHOLDER.search(tag.get_text()) is not None


def _is_deleted_block(tag: Tag) -> bool:
    if tag.name not in {"p", "div", "tr"}:
        return False
    return DELETED_BLOCK.match(" ".join(MARKER.sub("", tag.get_text()).split())) is not None


class _Renderer:
    """Walks markup in document order, collecting clean text and the markers that apply to it."""

    def __init__(self, active: str | None) -> None:
        self.active = active
        self.parts: list[str] = []
        self.markers: list[str] = []

    def _note(self, marker: str | None) -> None:
        if marker and marker != BASE_ACT and marker not in self.markers:
            self.markers.append(marker)

    def feed(self, node: PageElement) -> None:
        if isinstance(node, PreformattedString):  # Comments, doctype.
            return
        if isinstance(node, NavigableString):
            self._feed_string(str(node))
        elif isinstance(node, Tag):
            self._feed_tag(node)

    def _feed_string(self, string: str) -> None:
        if MARKER.sub("", string).replace("◄", "").strip():
            self._note(self.active)
        for found in MARKER.finditer(string):
            self._note(found.group(1))
        self.parts.append(string)

    def _feed_tag(self, tag: Tag) -> None:
        classes = _classes(tag)
        if classes & SKIPPED_CLASSES:
            return
        if _is_marker_block(tag):
            self.active = _marker_of(tag) or self.active
            if _is_deletion_block(tag):
                self._note(self.active)
            return
        if _is_deleted_block(tag):
            self._note(_marker_of(tag) or self.active)
            return
        if tag.name == "a" and FOOTNOTE_ANCHOR_ID.match(str(tag.get("id", ""))):
            self.parts.append(FOOTNOTE_REF)
            return
        if classes & SUPERSCRIPT_CLASSES:  # An exponent: 10^25, not 1025.
            self.parts.append("^")
        is_block = tag.name in BLOCK_TAGS
        if is_block:
            self.parts.append(" ")
        for child in tag.children:
            self.feed(child)
        if is_block:
            self.parts.append(" ")

    def text(self) -> str:
        return clean_text("".join(self.parts))


def _render(elements: list[Tag], active: str | None) -> tuple[str, list[str]]:
    renderer = _Renderer(active)
    for element in elements:
        renderer.feed(element)
    return renderer.text(), renderer.markers


def _active_markers(soup: BeautifulSoup) -> dict[int, str | None]:
    """The block marker in force on reaching each tag: one pass, not a search per unit."""
    active: str | None = None
    by_tag: dict[int, str | None] = {}
    for tag in soup.find_all(True):
        by_tag[id(tag)] = active
        if _is_marker_block(tag):
            active = _marker_of(tag) or active
    return by_tag


def _number_in(text: str) -> str | None:
    found = PARAGRAPH_NUMBER.match(clean_text(text))
    return (found.group(1) or found.group(2)) if found else None


def _consolidated_number(child: Tag) -> str | None:
    if child.name != "div" or "norm" not in _classes(child):
        return None
    span = child.find("span", class_="no-parag", recursive=False)
    return _number_in(span.get_text()) if span else None


def _oj_number(child: Tag) -> str | None:
    if child.name != "div" or not OJ_PARAGRAPH_ID.match(str(child.get("id", ""))):
        return None
    return _number_in(child.get_text())


def _group_paragraphs(
    article: Tag, number_of: Callable[[Tag], str | None]
) -> list[tuple[str | None, list[Tag]]]:
    """Each numbered block with the subparagraphs and lists that follow it."""
    groups: list[tuple[str | None, list[Tag]]] = []
    for child in article.find_all(recursive=False):
        if _classes(child) & HEADING_CLASSES:
            continue
        number = number_of(child)
        if number:
            groups.append((number, [child]))
        elif not groups:
            groups.append((None, [child]))
        elif _is_deletion_block(child) and groups[-1][0]:
            # Stands in for a deleted paragraph of its own; the previous one was not amended.
            continue
        else:
            groups[-1][1].append(child)
    return groups


def _heading_of(tag: Tag, css_class: str) -> str | None:
    found = tag.find(class_=css_class)
    return (clean_text(found.get_text()) or None) if found else None


def _annex_heading(children: list[Tag]) -> str | None:
    """The title under "ANNEX X": title-annex-2, or (Annex X) a second title-annex-1."""
    titles = [c for c in children if _classes(c) & ANNEX_TITLE_CLASSES]
    return (clean_text(titles[1].get_text()) or None) if len(titles) > 1 else None


class _EntryParser:
    def __init__(self, entry: ManifestEntry, soup: BeautifulSoup) -> None:
        self.entry = entry
        self.soup = soup
        self.active = _active_markers(soup)
        self.consolidated = entry.celex.startswith("0")

    def _provision(
        self,
        unit_type: UnitType,
        article: str,
        paragraph: str | None,
        heading: str | None,
        text: str,
        amended_by: list[str],
    ) -> Provision:
        entry = self.entry
        version_date = entry.version_date.isoformat()
        provision_id = (
            f"{entry.reg}-{entry.lang}-{unit_type}-{article}-{paragraph or 0}-{version_date}"
        ).lower()
        return Provision(
            provision_id=provision_id,
            reg=entry.reg,
            celex=entry.celex,
            lang=entry.lang,
            version_date=version_date,
            revision=entry.revision,
            unit_type=unit_type,
            article=article,
            paragraph=paragraph,
            heading=heading,
            text=text,
            amended_by=amended_by,
            source_url=anchored_url(entry.source_url, unit_type, article),
        )

    def articles(self) -> list[Provision]:
        number_of = _consolidated_number if self.consolidated else _oj_number
        title_class = "stitle-article-norm" if self.consolidated else "oj-sti-art"
        provisions = []
        for tag in self.soup.find_all("div", class_="eli-subdivision", id=ARTICLE_ID):
            article = ARTICLE_ID.match(tag["id"]).group(1)
            heading = _heading_of(tag, title_class)
            for number, elements in _group_paragraphs(tag, number_of):
                text, markers = _render(elements, self.active.get(id(elements[0])))
                if number:
                    text = PARAGRAPH_NUMBER.sub("", text, count=1)
                if text:  # Empty once a deleted provision is dropped.
                    provisions.append(
                        self._provision("article", article, number, heading, text, markers)
                    )
        return provisions

    def recitals(self) -> list[Provision]:
        provisions = []
        for tag in self.soup.find_all("div", id=RECITAL_ID):
            text, markers = _render([tag], self.active.get(id(tag)))
            text = RECITAL_NUMBER.sub("", text, count=1)
            if text:
                number = RECITAL_ID.match(tag["id"]).group(1)
                provisions.append(self._provision("recital", number, None, None, text, markers))
        return provisions

    def annexes(self) -> list[Provision]:
        provisions = []
        for tag in self.soup.find_all("div", id=ANNEX_ID):
            children = tag.find_all(recursive=False)
            body = [c for c in children if not _classes(c) & ANNEX_TITLE_CLASSES]
            text, markers = _render(body, self.active.get(id(tag)))
            if text:
                number = ANNEX_ID.match(tag["id"]).group(1)
                heading = _annex_heading(children)
                provisions.append(self._provision("annex", number, None, heading, text, markers))
        return provisions


def parse_entry(entry: ManifestEntry, document: bytes | str) -> list[Provision]:
    """The provisions this entry is used for: the manifest role decides the unit types."""
    # lxml-xml keeps class as one string unless told otherwise.
    soup = BeautifulSoup(document, "xml", multi_valued_attributes={"*": ["class"]})
    parser = _EntryParser(entry, soup)
    units = ROLE_UNITS[entry.role]
    provisions: list[Provision] = []
    if "article" in units:
        provisions += parser.articles()
    if "recital" in units:
        provisions += parser.recitals()
    if "annex" in units:
        provisions += parser.annexes()
    return provisions
