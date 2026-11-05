import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import FIXTURES, make_entry

from eucc.build_corpus import GOLD_FILE, SILVER_FILE, main
from eucc.extract import output_path

SILVER_FIELDS = {
    "provision_id",
    "reg",
    "celex",
    "lang",
    "version_date",
    "revision",
    "unit_type",
    "article",
    "paragraph",
    "heading",
    "text",
    "amended_by",
    "source_url",
}


def _setup(tmp_path: Path, articles: int = 3) -> list[str]:
    """A raw folder and manifest holding the GDPR excerpts, in both languages."""
    entries = []
    for lang in ("en", "de"):
        entry = make_entry(
            "consolidated_gdpr",
            lang,
            id=f"gdpr-consolidated-{lang}",
            expected_units={"articles": articles, "recitals": 0, "annexes": 0},
        )
        path = output_path(tmp_path / "raw", entry)
        path.parent.mkdir(parents=True)
        shutil.copy(FIXTURES / f"consolidated_gdpr_{lang}.xhtml", path)
        entries.append(entry.model_dump(mode="json"))
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return [
        "--raw",
        str(tmp_path / "raw"),
        "--out",
        str(tmp_path / "build"),
        "--manifest",
        str(tmp_path / "manifest.yaml"),
    ]


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_writes_silver_and_gold(tmp_path):
    assert main(_setup(tmp_path)) == 0
    silver = _rows(tmp_path / "build" / SILVER_FILE)
    gold = _rows(tmp_path / "build" / GOLD_FILE)
    assert all(set(row) == SILVER_FIELDS for row in silver)
    assert {row["lang"] for row in silver} == {"en", "de"}
    assert {row["provision_id"] for row in silver} == {row["provision_id"] for row in gold}
    assert all(row["chunk_text"].endswith(row["text"]) for row in gold)


def test_jsonl_keeps_umlauts_readable(tmp_path):
    main(_setup(tmp_path))
    assert "für" in (tmp_path / "build" / SILVER_FILE).read_text(encoding="utf-8")


def test_prints_a_table_row_per_entry(tmp_path, capsys):
    main(_setup(tmp_path))
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "entry",
        "articles",
        "paragraphs",
        "recitals",
        "annexes",
        "chunks",
        "max",
        "words",
    ]
    assert [line.split()[0] for line in lines[1:]] == [
        "gdpr-consolidated-en",
        "gdpr-consolidated-de",
    ]
    assert lines[1].split()[1:5] == ["3", "15", "0", "0"]


def test_contract_failure_names_the_entry_and_writes_nothing(tmp_path, capsys):
    assert main(_setup(tmp_path, articles=4)) == 1
    assert "gdpr-consolidated-en: expected 4 articles, found 3" in capsys.readouterr().err
    assert not (tmp_path / "build").exists()


def test_missing_source_names_the_entry(tmp_path, capsys):
    args = _setup(tmp_path)
    shutil.rmtree(tmp_path / "raw")
    assert main(args) == 1
    assert "gdpr-consolidated-en: source file not found" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--raw", "--out"])
def test_paths_are_required(flag, tmp_path):
    args = _setup(tmp_path)
    position = args.index(flag)
    del args[position : position + 2]
    with pytest.raises(SystemExit):
        main(args)
