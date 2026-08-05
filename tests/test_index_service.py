from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from journal_ai.config import index_database_path
from journal_ai.index_database import open_index_database
from journal_ai.index_service import (
    read_index_status,
    read_source_state,
    rebuild_index,
    scan_journal,
    update_index,
)
from journal_ai.journal_reader import (
    JournalNotMountedError,
    JournalReadError,
)
from journal_ai.models import IndexedDocument

FIRST_RUN = datetime(2026, 7, 31, 13, 24, 18, tzinfo=UTC)
SECOND_RUN = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC)


def write_entry(journal_path: Path, relative_path: str, content: str) -> Path:
    file_path = journal_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def read_indexed(journal_path: Path) -> dict[Path, IndexedDocument]:
    with open_index_database(index_database_path(journal_path)) as database:
        return database.read_documents()


def test_new_file_is_indexed(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")

    result = update_index(journal_path=mounted_journal, now=FIRST_RUN)

    assert result.new == 1
    assert result.changed == 0
    assert result.unchanged == 0
    assert result.deleted == 0
    assert result.new_paths == (Path("journals/entry.md"),)
    assert result.has_changes is True

    indexed = read_indexed(mounted_journal)
    document = indexed[Path("journals/entry.md")]
    assert document.content_hash == hashlib.sha256(
        b"Today was calm."
    ).hexdigest()
    assert document.file_size == len("Today was calm.")
    assert document.first_indexed_at == FIRST_RUN
    assert document.last_indexed_at == FIRST_RUN


def test_second_run_reports_the_file_as_unchanged(
    mounted_journal: Path,
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    result = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.new == 0
    assert result.changed == 0
    assert result.unchanged == 1
    assert result.deleted == 0
    assert result.has_changes is False

    document = read_indexed(mounted_journal)[Path("journals/entry.md")]
    assert document.first_indexed_at == FIRST_RUN
    assert document.last_indexed_at == FIRST_RUN


def test_changed_content_is_detected(mounted_journal: Path) -> None:
    file_path = write_entry(
        mounted_journal,
        "journals/entry.md",
        "Today was calm.",
    )
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    file_path.write_text("Today was busy instead.", encoding="utf-8")

    result = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.changed == 1
    assert result.new == 0
    assert result.unchanged == 0
    assert result.changed_paths == (Path("journals/entry.md"),)

    document = read_indexed(mounted_journal)[Path("journals/entry.md")]
    assert document.content_hash == hashlib.sha256(
        b"Today was busy instead."
    ).hexdigest()
    assert document.first_indexed_at == FIRST_RUN
    assert document.last_indexed_at == SECOND_RUN


def test_timestamp_only_change_stays_unchanged(mounted_journal: Path) -> None:
    file_path = write_entry(
        mounted_journal,
        "journals/entry.md",
        "Today was calm.",
    )
    update_index(journal_path=mounted_journal, now=FIRST_RUN)
    original_hash = read_indexed(mounted_journal)[
        Path("journals/entry.md")
    ].content_hash

    new_modified_time = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    os.utime(
        file_path,
        (new_modified_time.timestamp(), new_modified_time.timestamp()),
    )

    result = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.unchanged == 1
    assert result.changed == 0
    assert result.new == 0

    document = read_indexed(mounted_journal)[Path("journals/entry.md")]
    assert document.content_hash == original_hash
    assert document.modified_at == new_modified_time
    assert document.first_indexed_at == FIRST_RUN
    assert document.last_indexed_at == SECOND_RUN


def test_deleted_file_is_removed_from_the_index(mounted_journal: Path) -> None:
    file_path = write_entry(
        mounted_journal,
        "journals/entry.md",
        "Today was calm.",
    )
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    file_path.unlink()

    result = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.deleted == 1
    assert result.deleted_paths == (Path("journals/entry.md"),)
    assert read_indexed(mounted_journal) == {}


def test_mixed_changes_are_counted_correctly(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/keep_one.md", "One.")
    write_entry(mounted_journal, "journals/keep_two.md", "Two.")
    changed_path = write_entry(mounted_journal, "journals/edit.md", "Before.")
    removed_path = write_entry(mounted_journal, "journals/remove.md", "Gone.")

    first_result = update_index(journal_path=mounted_journal, now=FIRST_RUN)
    assert first_result.new == 4

    changed_path.write_text("After.", encoding="utf-8")
    removed_path.unlink()
    write_entry(mounted_journal, "pages/new_page.md", "Fresh.")
    write_entry(mounted_journal, "journals/new_entry.md", "Also fresh.")

    result = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.new == 2
    assert result.changed == 1
    assert result.unchanged == 2
    assert result.deleted == 1
    assert sorted(result.new_paths) == [
        Path("journals/new_entry.md"),
        Path("pages/new_page.md"),
    ]

    assert sorted(read_indexed(mounted_journal)) == [
        Path("journals/edit.md"),
        Path("journals/keep_one.md"),
        Path("journals/keep_two.md"),
        Path("journals/new_entry.md"),
        Path("pages/new_page.md"),
    ]


def test_generated_analyses_are_ignored(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Source entry.")
    write_entry(
        mounted_journal,
        "generated/analyses/20260731T000000Z_entry.md",
        "# Journal analysis\n\nBody\n",
    )

    result = update_index(journal_path=mounted_journal, now=FIRST_RUN)

    assert result.new == 1
    assert list(read_indexed(mounted_journal)) == [Path("journals/entry.md")]


def test_journal_ai_state_files_are_ignored(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Source entry.")
    write_entry(mounted_journal, ".journal-ai/notes.md", "App state notes.")

    result = update_index(journal_path=mounted_journal, now=FIRST_RUN)

    assert result.new == 1
    assert list(read_indexed(mounted_journal)) == [Path("journals/entry.md")]


def test_non_markdown_files_are_ignored(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Source entry.")
    write_entry(mounted_journal, "journals/notes.txt", "Plain text.")
    write_entry(mounted_journal, "assets/data.json", "{}")

    result = update_index(journal_path=mounted_journal, now=FIRST_RUN)

    assert result.new == 1
    assert list(read_indexed(mounted_journal)) == [Path("journals/entry.md")]


def test_file_outside_the_journal_is_rejected(
    mounted_journal: Path,
    tmp_path: Path,
) -> None:
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("Private text.", encoding="utf-8")

    with pytest.raises(JournalReadError, match="outside"):
        read_source_state(outside_file, mounted_journal)


def test_missing_file_is_rejected(mounted_journal: Path) -> None:
    with pytest.raises(JournalReadError, match="does not exist"):
        read_source_state(
            mounted_journal / "journals" / "missing.md",
            mounted_journal,
        )


def test_database_is_stored_inside_the_journal_state_directory(
    mounted_journal: Path,
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")

    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    expected_path = mounted_journal / ".journal-ai" / "index.sqlite"
    assert index_database_path(mounted_journal) == expected_path
    assert expected_path.is_file()


def test_rebuild_recreates_the_index(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/first.md", "First.")
    removed_path = write_entry(mounted_journal, "journals/second.md", "Second.")
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    removed_path.unlink()

    result = rebuild_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.new == 1
    assert result.changed == 0
    assert result.unchanged == 0
    assert result.deleted == 0

    indexed = read_indexed(mounted_journal)
    assert list(indexed) == [Path("journals/first.md")]
    assert indexed[Path("journals/first.md")].first_indexed_at == SECOND_RUN


def test_rebuild_repairs_a_corrupt_database(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")
    database_path = index_database_path(mounted_journal)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.write_bytes(b"this is not a database")

    result = rebuild_index(journal_path=mounted_journal, now=FIRST_RUN)

    assert result.new == 1
    assert list(read_indexed(mounted_journal)) == [Path("journals/entry.md")]


def test_status_reports_document_count_and_update_time(
    mounted_journal: Path,
) -> None:
    write_entry(mounted_journal, "journals/first.md", "First.")
    write_entry(mounted_journal, "journals/second.md", "Second.")
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    status = read_index_status(journal_path=mounted_journal)

    assert status.database_path == index_database_path(mounted_journal)
    assert status.database_exists is True
    assert status.document_count == 2
    assert status.last_indexed_at == FIRST_RUN


def test_status_without_a_database_reports_nothing_indexed(
    mounted_journal: Path,
) -> None:
    status = read_index_status(journal_path=mounted_journal)

    assert status.database_exists is False
    assert status.document_count == 0
    assert status.last_indexed_at is None
    assert not status.database_path.exists()


def test_source_files_are_unchanged_after_indexing(
    mounted_journal: Path,
) -> None:
    contents = {
        "journals/first.md": "First entry.",
        "journals/second.md": "Second entry.",
        "pages/topic.md": "A page.",
    }
    paths = {
        relative_path: write_entry(mounted_journal, relative_path, content)
        for relative_path, content in contents.items()
    }
    before = {
        relative_path: (
            path.read_text(encoding="utf-8"),
            path.stat().st_mtime_ns,
        )
        for relative_path, path in paths.items()
    }

    update_index(journal_path=mounted_journal, now=FIRST_RUN)
    rebuild_index(journal_path=mounted_journal, now=SECOND_RUN)

    for relative_path, path in paths.items():
        assert path.is_file()
        assert (
            path.read_text(encoding="utf-8"),
            path.stat().st_mtime_ns,
        ) == before[relative_path]


def test_unmounted_journal_is_rejected(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()

    with pytest.raises(JournalNotMountedError, match="not mounted"):
        update_index(journal_path=journal_path, now=FIRST_RUN)

    with pytest.raises(JournalNotMountedError, match="not mounted"):
        rebuild_index(journal_path=journal_path, now=FIRST_RUN)

    with pytest.raises(JournalNotMountedError, match="not mounted"):
        read_index_status(journal_path=journal_path)

    assert not (journal_path / ".journal-ai").exists()


def test_rebuild_keeps_the_index_when_the_journal_is_locked(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "journal"
    (journal_path / "journals").mkdir(parents=True)

    with patch("journal_ai.journal_reader.is_mountpoint", return_value=True):
        write_entry(journal_path, "journals/entry.md", "Today was calm.")
        update_index(journal_path=journal_path, now=FIRST_RUN)

    with pytest.raises(JournalNotMountedError):
        rebuild_index(journal_path=journal_path, now=SECOND_RUN)

    assert index_database_path(journal_path).is_file()


def test_scan_journal_never_returns_journal_content(
    mounted_journal: Path,
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "A private secret.")

    states = scan_journal(mounted_journal)

    assert len(states) == 1
    assert "A private secret." not in repr(states[0])


@patch("journal_ai.ollama_client.urlopen")
def test_indexing_never_contacts_ollama(
    mock_urlopen: MagicMock,
    mounted_journal: Path,
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")

    update_index(journal_path=mounted_journal, now=FIRST_RUN)
    rebuild_index(journal_path=mounted_journal, now=SECOND_RUN)
    read_index_status(journal_path=mounted_journal)

    mock_urlopen.assert_not_called()


def test_index_database_path_is_excluded_from_source_discovery() -> None:
    from journal_ai.config import INDEX_STATE_DIR_NAME
    from journal_ai.journal_reader import EXCLUDED_TOP_LEVEL_DIRS

    assert INDEX_STATE_DIR_NAME in EXCLUDED_TOP_LEVEL_DIRS


def test_explicit_database_path_is_used(
    mounted_journal: Path,
    tmp_path: Path,
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Today was calm.")
    database_path = tmp_path / "custom" / "index.sqlite"

    result = update_index(
        journal_path=mounted_journal,
        database_path=database_path,
        now=FIRST_RUN,
    )

    assert result.new == 1
    assert database_path.is_file()
    assert not index_database_path(mounted_journal).exists()
