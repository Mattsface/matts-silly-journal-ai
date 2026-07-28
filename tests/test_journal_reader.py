from pathlib import Path

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
