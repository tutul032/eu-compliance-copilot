"""Data contract between parsing and indexing: raises an error naming the entry that broke it."""

from collections import Counter
from collections.abc import Iterable

from eucc.chunk import Chunk
from eucc.corpus.manifest import ManifestEntry
from eucc.parse import Provision


class ContractError(Exception):
    """Parsed data does not match what the manifest promises."""


def distinct_units(provisions: Iterable[Provision], unit_type: str) -> int:
    """Units, not rows: an article with five paragraphs counts once."""
    return len({p.article for p in provisions if p.unit_type == unit_type})


def check_entry(entry: ManifestEntry, provisions: list[Provision], chunks: list[Chunk]) -> None:
    """Articles and recitals match expected_units, nothing is empty, no chunk_id repeats."""
    expected = entry.expected_units
    for unit_type, wanted in (("article", expected.articles), ("recital", expected.recitals)):
        found = distinct_units(provisions, unit_type)
        if found != wanted:
            raise ContractError(f"{entry.id}: expected {wanted} {unit_type}s, found {found}")

    empty = [p.provision_id for p in provisions if not p.text.strip()]
    empty += [c.chunk_id for c in chunks if not c.text.strip()]
    if empty:
        raise ContractError(f"{entry.id}: empty text in {', '.join(sorted(set(empty)))}")

    counts = Counter(c.chunk_id for c in chunks)
    duplicates = sorted(chunk_id for chunk_id, count in counts.items() if count > 1)
    if duplicates:
        raise ContractError(f"{entry.id}: duplicate chunk_ids: {', '.join(duplicates)}")


def check_corpus(chunks_by_entry: dict[str, list[Chunk]]) -> None:
    """chunk_ids are also unique across entries; the error names the entries that collide."""
    owners: dict[str, list[str]] = {}
    for entry_id, chunks in chunks_by_entry.items():
        for chunk in chunks:
            owners.setdefault(chunk.chunk_id, []).append(entry_id)
    collisions = sorted((cid, ids) for cid, ids in owners.items() if len(ids) > 1)
    if collisions:
        chunk_id, entry_ids = collisions[0]
        raise ContractError(
            f"{', '.join(entry_ids)}: duplicate chunk_id {chunk_id} "
            f"({len(collisions)} collisions in total)"
        )
