"""Extractor: download the corpus from Cellar, validate it and pin it in a lock file.

    python -m eucc.extract --out data/raw [--write-lock] [--upload-to PATH] [--only ID]

Downloads come from Cellar, not the EUR-Lex website, which answers scripts with a bot challenge
(docs/spikes/s1-2-eurlex-format.md). A document is written to --out and uploaded only once it has
passed validation and the lock check, so a changed source never replaces a verified local copy.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError

from eucc.corpus.manifest import DEFAULT_PATH, ManifestEntry, load_manifest

LOCK_PATH = DEFAULT_PATH.with_name("manifest.lock.json")

ACCEPT = "application/xhtml+xml, text/html;q=0.9"
USER_AGENT = "eucc-extractor/0.1 (+https://github.com/tutul032/eu-compliance-copilot)"
TIMEOUT_SECONDS = 60
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1
PAUSE_SECONDS = 2

ALLOWED_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
MIN_BYTES = 50_000
ARTICLE_ID = re.compile(r'id="art_(\d+[a-z]?)"')
RECITAL_ID = re.compile(r'id="rct_(\d+)"')
LANGUAGE_MARKERS = {"en": "Article", "de": "Artikel"}


class ExtractError(Exception):
    """A document could not be downloaded."""


class BotChallengeError(ExtractError):
    """The response came from the EUR-Lex website's bot protection."""


class LockError(ExtractError):
    """A document does not match the lock file."""


@dataclass(frozen=True)
class FetchedDocument:
    body: bytes
    content_type: str
    final_url: str


@dataclass(frozen=True)
class ValidationReport:
    bytes: int
    articles: int
    recitals: int
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class LockEntry:
    id: str
    fetch_url: str
    final_url: str
    sha256: str
    bytes: int
    fetched_at: str


@dataclass(frozen=True)
class SummaryRow:
    id: str
    status: str
    bytes: int | None = None
    articles: int | None = None
    recitals: int | None = None


def fetch(entry: ManifestEntry, session: requests.Session) -> FetchedDocument:
    """Download one document, retrying on 429, 5xx and connection errors."""
    headers = {
        "Accept": ACCEPT,
        "Accept-Language": entry.fetch_lang,
        "User-Agent": USER_AGENT,
    }
    failure = ""
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            time.sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
        try:
            response = session.get(
                entry.fetch_url, headers=headers, timeout=TIMEOUT_SECONDS, allow_redirects=True
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            failure = str(exc)
            continue
        except requests.RequestException as exc:
            raise ExtractError(f"{entry.id}: {exc}") from exc

        status = response.status_code
        if status == 429 or status >= 500:
            failure = f"HTTP {status}"
            continue
        if status == 202:
            raise BotChallengeError(
                f"{entry.id}: HTTP 202 from {response.url}: this is a bot challenge from the "
                "EUR-Lex website, not the Cellar endpoint. Check that fetch_url points to Cellar."
            )
        if status != 200:
            raise ExtractError(f"{entry.id}: HTTP {status} from {response.url}")
        return FetchedDocument(
            body=response.content,
            content_type=response.headers.get("Content-Type", ""),
            final_url=response.url,
        )
    raise ExtractError(f"{entry.id}: giving up after {MAX_RETRIES} retries: {failure}")


def validate(entry: ManifestEntry, content: FetchedDocument) -> ValidationReport:
    """Check media type, size, unit counts and language; annexes are not counted here."""
    errors: list[str] = []
    text = content.body.decode("utf-8", errors="replace")

    media_type = content.content_type.split(";")[0].strip().lower()
    if media_type not in ALLOWED_MEDIA_TYPES:
        errors.append(f"content type is {content.content_type!r}, expected XHTML or HTML")

    size = len(content.body)
    if size <= MIN_BYTES:
        errors.append(f"size is {size} bytes, expected more than {MIN_BYTES}")

    articles = len(set(ARTICLE_ID.findall(text)))
    recitals = len(set(RECITAL_ID.findall(text)))
    expected = entry.expected_units
    # 0 means "not used from this document", not "absent": original acts used for recitals do
    # contain articles.
    for unit, found, wanted in (
        ("articles", articles, expected.articles),
        ("recitals", recitals, expected.recitals),
    ):
        if wanted and found != wanted:
            errors.append(f"found {found} {unit}, expected {wanted}")

    marker = LANGUAGE_MARKERS[entry.lang]
    if marker not in text:
        errors.append(f"language marker {marker!r} not found, expected a {entry.lang} text")

    return ValidationReport(bytes=size, articles=articles, recitals=recitals, errors=errors)


def relative_path(entry: ManifestEntry) -> PurePosixPath:
    """The layout shared by the local output and the volume: <celex>/<lang>/<version_date>.xhtml."""
    return PurePosixPath(entry.celex, entry.lang, f"{entry.version_date.isoformat()}.xhtml")


def output_path(out: Path, entry: ManifestEntry) -> Path:
    return out / relative_path(entry)


def make_lock_entry(entry: ManifestEntry, content: FetchedDocument) -> LockEntry:
    return LockEntry(
        id=entry.id,
        fetch_url=entry.fetch_url,
        final_url=content.final_url,
        sha256=hashlib.sha256(content.body).hexdigest(),
        bytes=len(content.body),
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def load_lock(path: Path) -> dict[str, LockEntry]:
    """Read the lock file; a file that doesn't exist yet is an empty lock."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: LockEntry(**item) for item in data["entries"]}


def save_lock(path: Path, lock: dict[str, LockEntry]) -> None:
    data = {"entries": [asdict(lock[entry_id]) for entry_id in sorted(lock)]}
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def check_lock(lock: dict[str, LockEntry], current: LockEntry) -> None:
    """Raise LockError if the document isn't in the lock or its hash differs."""
    locked = lock.get(current.id)
    if locked is None:
        raise LockError(
            f"{current.id}: no entry in {LOCK_PATH.name}; run with --write-lock to add it"
        )
    if locked.sha256 != current.sha256:
        raise LockError(
            f"{current.id}: the source changed on EUR-Lex/Cellar: sha256 is {current.sha256}, "
            f"locked {locked.sha256} (fetched {locked.fetched_at}). Review the change, then run "
            "with --write-lock to accept it."
        )


def upload_file(client: WorkspaceClient, local: Path, volume: str, entry: ManifestEntry) -> None:
    remote = f"{volume.rstrip('/')}/{relative_path(entry)}"
    with local.open("rb") as f:
        client.files.upload(remote, f, overwrite=True)


def process_entry(
    entry: ManifestEntry,
    session: requests.Session,
    out: Path,
    lock: dict[str, LockEntry],
    write_lock: bool,
    client: WorkspaceClient | None,
    upload_to: str | None,
) -> SummaryRow:
    """Run one document through fetch, validate, lock, write and upload; updates lock in place."""

    def row(status: str) -> SummaryRow:
        return SummaryRow(entry.id, status, report.bytes, report.articles, report.recitals)

    try:
        content = fetch(entry, session)
    except ExtractError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return SummaryRow(entry.id, "fetch-failed")

    report = validate(entry, content)
    if not report.ok:
        for error in report.errors:
            print(f"ERROR {entry.id}: {error}", file=sys.stderr)
        return row("invalid")

    current = make_lock_entry(entry, content)
    if write_lock:
        lock[entry.id] = current
    else:
        try:
            check_lock(lock, current)
        except LockError as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return row("lock-mismatch")

    path = output_path(out, entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.body)

    if client is not None and upload_to is not None:
        try:
            upload_file(client, path, upload_to, entry)
        except (DatabricksError, requests.RequestException) as exc:
            print(f"ERROR {entry.id}: upload failed: {exc}", file=sys.stderr)
            return row("upload-failed")

    return row("ok")


def format_summary(rows: list[SummaryRow]) -> str:
    def cell(value: int | None) -> str:
        return "-" if value is None else str(value)

    table = [("id", "status", "bytes", "articles", "recitals")]
    table += [(r.id, r.status, cell(r.bytes), cell(r.articles), cell(r.recitals)) for r in rows]
    widths = [max(len(line[i]) for line in table) for i in range(5)]
    return "\n".join(
        f"{line[0]:<{widths[0]}}  {line[1]:<{widths[1]}}  {line[2]:>{widths[2]}}  "
        f"{line[3]:>{widths[3]}}  {line[4]:>{widths[4]}}"
        for line in table
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and verify the corpus from Cellar.")
    parser.add_argument("--out", type=Path, required=True, help="local output directory")
    parser.add_argument("--write-lock", action="store_true", help="write or update the lock file")
    parser.add_argument("--upload-to", metavar="PATH", help="Unity Catalog volume path")
    parser.add_argument("--only", metavar="ID", help="process a single manifest entry")
    args = parser.parse_args(argv)

    entries = load_manifest().entries
    if args.only:
        entries = [e for e in entries if e.id == args.only]
        if not entries:
            parser.error(f"--only: no manifest entry with id {args.only}")

    lock = load_lock(LOCK_PATH)
    # Built up front so that missing credentials fail before anything is downloaded.
    client = WorkspaceClient() if args.upload_to else None

    rows: list[SummaryRow] = []
    with requests.Session() as session:
        for i, entry in enumerate(entries):
            if i:
                time.sleep(PAUSE_SECONDS)
            rows.append(
                process_entry(
                    entry, session, args.out, lock, args.write_lock, client, args.upload_to
                )
            )

    if args.write_lock and lock:
        save_lock(LOCK_PATH, lock)

    print(format_summary(rows))
    return 0 if all(r.status == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
