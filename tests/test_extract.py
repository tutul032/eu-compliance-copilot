import hashlib
import io
import json
from pathlib import Path

import pytest
import requests
import responses
from responses.matchers import header_matcher

from eucc import extract
from eucc.corpus.manifest import Manifest, ManifestEntry
from eucc.extract import (
    BotChallengeError,
    ExtractError,
    FetchedDocument,
    LockEntry,
    LockError,
    check_lock,
    fetch,
    load_lock,
    main,
    make_lock_entry,
    output_path,
    save_lock,
    validate,
)

CELEX = "32016R0679"
FETCH_URL = f"http://publications.europa.eu/resource/celex/{CELEX}"
XHTML = "application/xhtml+xml;charset=UTF-8"


def _raw_entry(lang: str = "en", articles: int = 3, recitals: int = 2) -> dict:
    return {
        "id": f"gdpr-original-{lang}",
        "reg": "GDPR",
        "celex": CELEX,
        "lang": lang,
        "role": "articles+recitals",
        "version_date": "2016-05-04",
        "revision": None,
        "source_url": (
            f"https://eur-lex.europa.eu/legal-content/{lang.upper()}/TXT/HTML/?uri=CELEX:{CELEX}"
        ),
        "format": "html",
        "expected_units": {"articles": articles, "recitals": recitals, "annexes": 0},
        "sha256": None,
    }


def _entry(lang: str = "en", articles: int = 3, recitals: int = 2) -> ManifestEntry:
    return ManifestEntry.model_validate(_raw_entry(lang, articles, recitals))


def _xhtml(articles: int = 3, recitals: int = 2, word: str = "Article", pad: int = 60_000) -> bytes:
    """A synthetic document with the ELI ids the extractor counts, padded past the size floor."""
    units = "".join(f'<div id="rct_{n}">Recital {n}</div>' for n in range(1, recitals + 1))
    units += "".join(
        f'<div id="art_{n}"><p>{word} {n}</p><div id="art_{n}.tit_1">Title</div></div>'
        for n in range(1, articles + 1)
    )
    return f"<html><body>{units}<!-- {'x' * pad} --></body></html>".encode()


def _document(body: bytes | None = None, content_type: str = XHTML) -> FetchedDocument:
    return FetchedDocument(
        body=_xhtml() if body is None else body, content_type=content_type, final_url=FETCH_URL
    )


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """Record sleeps instead of waiting for them."""
    recorded: list[float] = []
    monkeypatch.setattr(extract.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def lock_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "manifest.lock.json"
    monkeypatch.setattr(extract, "LOCK_PATH", path)
    return path


@pytest.fixture
def manifest(monkeypatch) -> None:
    """Replace the repo manifest with one source, in both languages, of 3 articles / 2 recitals."""
    loaded = Manifest.model_validate({"entries": [_raw_entry("en"), _raw_entry("de")]})
    monkeypatch.setattr(extract, "load_manifest", lambda: loaded)


def _serve(body: bytes, lang: str = "eng") -> None:
    responses.get(
        FETCH_URL,
        body=body,
        content_type=XHTML,
        match=[header_matcher({"Accept-Language": lang})],
    )


# fetch


@responses.activate
def test_fetch_sends_the_negotiation_headers():
    responses.get(FETCH_URL, body=_xhtml(), content_type=XHTML)
    fetch(_entry("de"), requests.Session())
    headers = responses.calls[0].request.headers
    assert headers["Accept"] == "application/xhtml+xml, text/html;q=0.9"
    assert headers["Accept-Language"] == "deu"
    assert headers["User-Agent"].startswith("eucc-extractor/0.1 (+https://github.com/")


@responses.activate
def test_fetch_follows_redirects_and_keeps_the_final_url():
    final = "http://publications.europa.eu/resource/cellar/abc.0006.03/DOC_1"
    responses.get(FETCH_URL, status=303, headers={"Location": final})
    responses.get(final, body=_xhtml(), content_type=XHTML)
    assert fetch(_entry(), requests.Session()).final_url == final


@responses.activate
def test_fetch_retries_503_then_succeeds(sleeps):
    responses.get(FETCH_URL, status=503)
    responses.get(FETCH_URL, body=_xhtml(), content_type=XHTML)
    document = fetch(_entry(), requests.Session())
    assert document.body == _xhtml()
    assert len(responses.calls) == 2
    assert sleeps == [1]


@responses.activate
def test_fetch_retries_429_and_connection_errors(sleeps):
    responses.get(FETCH_URL, status=429)
    responses.get(FETCH_URL, body=requests.ConnectionError("reset"))
    responses.get(FETCH_URL, body=_xhtml(), content_type=XHTML)
    fetch(_entry(), requests.Session())
    assert sleeps == [1, 2]


@responses.activate
def test_fetch_gives_up_after_three_retries(sleeps):
    responses.get(FETCH_URL, status=500)
    with pytest.raises(ExtractError, match="after 3 retries: HTTP 500"):
        fetch(_entry(), requests.Session())
    assert len(responses.calls) == 4
    assert sleeps == [1, 2, 4]


@responses.activate
def test_fetch_202_is_a_bot_challenge_and_not_retried(sleeps):
    responses.get(FETCH_URL, status=202, body=b"")
    with pytest.raises(BotChallengeError, match="bot challenge from the EUR-Lex website, not the"):
        fetch(_entry(), requests.Session())
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
@pytest.mark.parametrize("status", [404, 300])
def test_fetch_other_statuses_fail_without_retry(status):
    responses.get(FETCH_URL, status=status)
    with pytest.raises(ExtractError, match=f"HTTP {status}"):
        fetch(_entry(), requests.Session())
    assert len(responses.calls) == 1


# validate


@pytest.mark.parametrize("content_type", [XHTML, "text/html; charset=utf-8"])
def test_validate_passes(content_type):
    report = validate(_entry(), _document(content_type=content_type))
    assert report.ok, report.errors
    assert (report.articles, report.recitals) == (3, 2)
    assert report.bytes == len(_xhtml())


def test_validate_passes_for_german():
    assert validate(_entry("de"), _document(_xhtml(word="Artikel"))).ok


def test_validate_rejects_other_content_types():
    report = validate(_entry(), _document(content_type="application/pdf"))
    assert any("content type" in e for e in report.errors)


def test_validate_rejects_small_documents():
    report = validate(_entry(), _document(_xhtml(pad=10)))
    assert any("size is" in e for e in report.errors)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (_xhtml(articles=2), "found 2 articles, expected 3"),
        (_xhtml(articles=4), "found 4 articles, expected 3"),
        (_xhtml(recitals=1), "found 1 recitals, expected 2"),
    ],
)
def test_validate_requires_exact_counts(body, message):
    assert message in validate(_entry(), _document(body)).errors


def test_validate_skips_a_unit_type_expected_as_zero():
    # An original act used for recitals contains articles but expects none.
    assert validate(_entry(articles=0), _document()).ok


def test_validate_counts_unique_ids_including_inserted_articles():
    extra = b'<div id="art_1">repeat</div><div id="art_4a">inserted</div><div id="rct_1">r</div>'
    report = validate(_entry(articles=4), _document(_xhtml() + extra))
    assert (report.articles, report.recitals) == (4, 2)
    assert report.ok


@pytest.mark.parametrize(("lang", "word"), [("de", "Article"), ("en", "Artikel")])
def test_validate_checks_language(lang, word):
    report = validate(_entry(lang), _document(_xhtml(word=word)))
    assert any("language marker" in e for e in report.errors)


# output path


def test_output_path_layout(tmp_path):
    assert output_path(tmp_path, _entry("de")) == (tmp_path / CELEX / "de" / "2016-05-04.xhtml")


# lock


def test_lock_entry_fields():
    document = _document()
    locked = make_lock_entry(_entry(), document)
    assert locked.id == "gdpr-original-en"
    assert locked.fetch_url == FETCH_URL
    assert locked.final_url == FETCH_URL
    assert locked.sha256 == hashlib.sha256(document.body).hexdigest()
    assert locked.bytes == len(document.body)
    assert locked.fetched_at.endswith("+00:00")


def test_lock_round_trip(tmp_path):
    path = tmp_path / "lock.json"
    locked = make_lock_entry(_entry(), _document())
    save_lock(path, {locked.id: locked})
    assert load_lock(path) == {locked.id: locked}


def test_check_lock_accepts_a_match():
    locked = make_lock_entry(_entry(), _document())
    check_lock({locked.id: locked}, locked)


def test_check_lock_rejects_a_changed_sha256():
    locked = make_lock_entry(_entry(), _document())
    changed = LockEntry(**{**locked.__dict__, "sha256": "0" * 64})
    with pytest.raises(LockError, match="source changed on EUR-Lex/Cellar"):
        check_lock({locked.id: locked}, changed)


def test_check_lock_rejects_a_missing_entry():
    with pytest.raises(LockError, match="no entry"):
        check_lock({}, make_lock_entry(_entry(), _document()))


# CLI


@responses.activate
def test_main_writes_files_lock_and_summary(tmp_path, lock_path, manifest, sleeps, capsys):
    en, de = _xhtml(), _xhtml(word="Artikel")
    _serve(en, "eng")
    _serve(de, "deu")
    assert main(["--out", str(tmp_path / "raw"), "--write-lock"]) == 0

    assert (tmp_path / "raw" / CELEX / "en" / "2016-05-04.xhtml").read_bytes() == en
    assert (tmp_path / "raw" / CELEX / "de" / "2016-05-04.xhtml").read_bytes() == de
    entries = json.loads(lock_path.read_text(encoding="utf-8"))["entries"]
    assert [e["id"] for e in entries] == ["gdpr-original-de", "gdpr-original-en"]
    assert set(entries[0]) == {"id", "fetch_url", "final_url", "sha256", "bytes", "fetched_at"}
    assert sleeps == [2]  # between the two requests, not after the last

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["id", "status", "bytes", "articles", "recitals"]
    assert lines[1].split() == ["gdpr-original-en", "ok", str(len(en)), "3", "2"]


@responses.activate
def test_main_passes_when_lock_matches(tmp_path, lock_path, manifest):
    _serve(_xhtml())
    args = ["--out", str(tmp_path), "--only", "gdpr-original-en"]
    assert main([*args, "--write-lock"]) == 0
    assert main(args) == 0


@responses.activate
def test_main_fails_on_lock_mismatch_and_keeps_the_old_file(tmp_path, lock_path, manifest, capsys):
    old, new = _xhtml(), _xhtml() + b"<!-- amended -->"
    _serve(old)
    args = ["--out", str(tmp_path), "--only", "gdpr-original-en"]
    assert main([*args, "--write-lock"]) == 0

    responses.reset()
    _serve(new)
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "source changed on EUR-Lex/Cellar" in captured.err
    assert "lock-mismatch" in captured.out
    assert (tmp_path / CELEX / "en" / "2016-05-04.xhtml").read_bytes() == old


@responses.activate
def test_main_fails_on_missing_lock_entry(tmp_path, lock_path, manifest, capsys):
    _serve(_xhtml())
    assert main(["--out", str(tmp_path), "--only", "gdpr-original-en"]) == 1
    assert "no entry" in capsys.readouterr().err
    assert not (tmp_path / CELEX).exists()


@responses.activate
def test_main_write_lock_with_only_keeps_other_entries(tmp_path, lock_path, manifest):
    other = make_lock_entry(_entry("de"), _document())
    save_lock(lock_path, {other.id: other})
    _serve(_xhtml())
    assert main(["--out", str(tmp_path), "--only", "gdpr-original-en", "--write-lock"]) == 0
    assert set(load_lock(lock_path)) == {"gdpr-original-de", "gdpr-original-en"}


@responses.activate
def test_main_does_not_lock_or_write_an_invalid_document(tmp_path, lock_path, manifest, capsys):
    _serve(_xhtml(articles=2))
    args = ["--out", str(tmp_path), "--only", "gdpr-original-en", "--write-lock"]
    assert main(args) == 1
    assert not lock_path.exists()
    assert not (tmp_path / CELEX).exists()
    assert "found 2 articles, expected 3" in capsys.readouterr().err


@responses.activate
def test_main_continues_after_a_failed_entry(tmp_path, lock_path, manifest, capsys):
    responses.get(FETCH_URL, status=202, match=[header_matcher({"Accept-Language": "eng"})])
    _serve(_xhtml(word="Artikel"), "deu")
    assert main(["--out", str(tmp_path), "--write-lock"]) == 1
    captured = capsys.readouterr()
    assert "bot challenge" in captured.err
    assert "fetch-failed" in captured.out
    assert set(load_lock(lock_path)) == {"gdpr-original-de"}


def test_main_rejects_unknown_only(tmp_path, lock_path, manifest):
    with pytest.raises(SystemExit) as exc:
        main(["--out", str(tmp_path), "--only", "nope-en"])
    assert exc.value.code == 2


class _FakeFiles:
    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}
        self.overwrite: list[bool] = []

    def upload(self, path: str, contents: io.BufferedIOBase, *, overwrite: bool) -> None:
        self.uploads[path] = contents.read()
        self.overwrite.append(overwrite)


class _FakeClient:
    def __init__(self) -> None:
        self.files = _FakeFiles()


@responses.activate
def test_main_uploads_with_the_same_layout(tmp_path, lock_path, manifest, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(extract, "WorkspaceClient", lambda: client)
    _serve(_xhtml())
    volume = "/Volumes/workspace/compliance_dev/raw/"
    args = ["--out", str(tmp_path), "--only", "gdpr-original-en", "--write-lock"]
    assert main([*args, "--upload-to", volume]) == 0
    remote = f"/Volumes/workspace/compliance_dev/raw/{CELEX}/en/2016-05-04.xhtml"
    assert client.files.uploads == {remote: _xhtml()}
    assert client.files.overwrite == [True]
