"""Local corpus build: parse and chunk the downloaded documents, check them, write JSONL.

    python -m eucc.build_corpus --raw data/raw --out data/build

Writes silver_provisions.jsonl and gold_chunks.jsonl, only once every entry passes the contract.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from eucc.chunk import Chunk, chunk_provision
from eucc.contract import ContractError, check_corpus, check_entry, distinct_units
from eucc.corpus.manifest import DEFAULT_PATH, Manifest, ManifestEntry, load_manifest
from eucc.extract import output_path
from eucc.parse import Provision, parse_entry

SILVER_FILE = "silver_provisions.jsonl"
GOLD_FILE = "gold_chunks.jsonl"
TABLE_HEADERS = ("entry", "articles", "paragraphs", "recitals", "annexes", "chunks", "max words")


@dataclass(frozen=True)
class EntryBuild:
    entry: ManifestEntry
    provisions: list[Provision]
    chunks: list[Chunk]


def build_entry(entry: ManifestEntry, raw: Path) -> EntryBuild:
    path = output_path(raw, entry)
    if not path.is_file():
        raise ContractError(f"{entry.id}: source file not found: {path}")
    provisions = parse_entry(entry, path.read_bytes())
    chunks = [chunk for provision in provisions for chunk in chunk_provision(provision)]
    check_entry(entry, provisions, chunks)
    return EntryBuild(entry, provisions, chunks)


def build_corpus(manifest: Manifest, raw: Path) -> list[EntryBuild]:
    builds = [build_entry(entry, raw) for entry in manifest.entries]
    check_corpus({build.entry.id: build.chunks for build in builds})
    return builds


def write_jsonl(path: Path, rows: list[Provision] | list[Chunk]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def table_rows(builds: list[EntryBuild]) -> list[tuple[str, ...]]:
    """Max words counts a chunk's text without its header: the measure the 400 limit applies to."""
    rows = []
    for build in builds:
        provisions = build.provisions
        rows.append(
            (
                build.entry.id,
                str(distinct_units(provisions, "article")),
                str(sum(1 for p in provisions if p.paragraph is not None)),
                str(distinct_units(provisions, "recital")),
                str(distinct_units(provisions, "annex")),
                str(len(build.chunks)),
                str(max((len(c.text.split()) for c in build.chunks), default=0)),
            )
        )
    return rows


def format_table(rows: list[tuple[str, ...]]) -> str:
    table = [TABLE_HEADERS, *rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(TABLE_HEADERS))]
    lines = []
    for row in table:
        cells = [row[0].ljust(widths[0])]
        cells += [cell.rjust(width) for cell, width in zip(row[1:], widths[1:], strict=True)]
        lines.append("  ".join(cells))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, required=True, help="downloaded documents")
    parser.add_argument("--out", type=Path, required=True, help="where the JSONL files go")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)

    try:
        builds = build_corpus(load_manifest(args.manifest), args.raw)
    except ContractError as error:
        print(f"contract failed: {error}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out / SILVER_FILE, [p for b in builds for p in b.provisions])
    write_jsonl(args.out / GOLD_FILE, [c for b in builds for c in b.chunks])
    print(format_table(table_rows(builds)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
