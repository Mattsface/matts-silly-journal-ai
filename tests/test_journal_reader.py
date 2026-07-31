from pathlib import Path
from unittest.mock import patch

import pytest

from journal_ai.journal_reader import (
    JournalError,
    JournalNotMountedError,
    JournalReadError,
    find_markdown_files,
    read_markdown_file,
)


def test_missing_directory_raises_error(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing"

    with pytest.raises(JournalError, match="does not exist"):
        find_markdown_files(missing_path)


def test_regular_directory_is_rejected(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()

    with pytest.raises(JournalNotMountedError, match="not mounted"):
        find_markdown_files(journal_path)


def test_read_markdown_file(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()

    file_path = journal_path / "entry.md"
    file_path.write_text(
        "Today was a good day.",
        encoding="utf-8",
    )

    document = read_markdown_file(file_path, journal_path)

    assert document.path == file_path.resolve()
    assert document.relative_path == Path("entry.md")
    assert document.content == "Today was a good day."
    assert document.word_count == 5
    assert document.character_count == 21


def test_file_outside_journal_is_rejected(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()

    outside_file = tmp_path / "outside.md"
    outside_file.write_text("Private text", encoding="utf-8")

    with pytest.raises(JournalReadError, match="outside"):
        read_markdown_file(outside_file, journal_path)


@patch("journal_ai.journal_reader.is_mountpoint", return_value=True)
def test_generated_analyses_are_excluded_from_discovery(
    _mock_is_mountpoint: object,
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    generated_dir = journal_path / "generated" / "analyses"
    app_state_dir = journal_path / ".journal-ai"
    journals_dir.mkdir(parents=True)
    generated_dir.mkdir(parents=True)
    app_state_dir.mkdir(parents=True)

    source_path = journals_dir / "entry.md"
    source_path.write_text("Source entry.", encoding="utf-8")
    (generated_dir / "analysis.md").write_text(
        "# Journal analysis\n\nBody\n",
        encoding="utf-8",
    )
    (app_state_dir / "notes.md").write_text(
        "App state notes.",
        encoding="utf-8",
    )

    discovered = find_markdown_files(journal_path)

    assert discovered == [source_path.resolve()]
