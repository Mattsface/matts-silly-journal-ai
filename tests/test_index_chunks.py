from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from journal_ai.chunking import chunk_markdown
from journal_ai.config import ChunkingConfig, index_database_path
from journal_ai.hashing import hash_text
from journal_ai.index_database import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    IndexDatabaseError,
    UnsupportedIndexSchemaError,
    open_index_database,
)
from journal_ai.index_service import rebuild_index, update_index
from journal_ai.models import IndexedDocument, TextChunk

FIRST_RUN = datetime(2026, 7, 31, 13, 24, 18, tzinfo=UTC)
SECOND_RUN = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC)


def write_entry(journal_path: Path, relative_path: str, content: str) -> Path:
    file_path = journal_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def test_schema_includes_chunks_table(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    with open_index_database(database_path) as database:
        assert database.chunk_count() == 0

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()

    assert "chunks" in tables


def test_schema_version_one_requires_rebuild(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE index_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                content_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                modified_at TEXT NOT NULL,
                first_indexed_at TEXT NOT NULL,
                last_indexed_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
            (SCHEMA_VERSION_KEY, "1"),
        )
        connection.commit()
    finally:
        connection.close()

    with (
        pytest.raises(UnsupportedIndexSchemaError, match="index --rebuild"),
        open_index_database(database_path),
    ):
        pass

    connection = sqlite3.connect(database_path)
    try:
        version = connection.execute(
            "SELECT value FROM index_metadata WHERE key = ?",
            (SCHEMA_VERSION_KEY,),
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()

    assert version == "1"
    assert "chunks" not in tables
    assert SCHEMA_VERSION != 1


def test_new_document_creates_chunks(mounted_journal: Path) -> None:
    content = "## Work\nHandled tickets calmly."
    write_entry(mounted_journal, "journals/entry.md", content)

    result = update_index(journal_path=mounted_journal, now=FIRST_RUN)

    assert result.chunks_created == 1
    assert result.chunks_total == 1

    with open_index_database(index_database_path(mounted_journal)) as database:
        document_ids = database.read_document_ids()
        chunks = database.read_chunks_for_document(document_ids[Path("journals/entry.md")])

    assert len(chunks) == 1
    assert chunks[0].content == content
    assert chunks[0].content_hash == hash_text(content)


def test_unchanged_document_keeps_chunks(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Stable text.")
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    with open_index_database(index_database_path(mounted_journal)) as database:
        before = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )

    second = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert second.chunks_created == 0
    assert second.chunks_removed == 0

    with open_index_database(index_database_path(mounted_journal)) as database:
        after = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )

    assert before == after


def test_changed_document_replaces_chunks(mounted_journal: Path) -> None:
    file_path = write_entry(mounted_journal, "journals/entry.md", "Version one.")
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    file_path.write_text("Version two with more detail.", encoding="utf-8")
    result = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.changed == 1
    assert result.chunks_removed >= 1
    assert result.chunks_created >= 1

    with open_index_database(index_database_path(mounted_journal)) as database:
        chunks = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )

    assert chunks[0].content == "Version two with more detail."


def test_deleted_document_removes_chunks(mounted_journal: Path) -> None:
    file_path = write_entry(mounted_journal, "journals/entry.md", "Temporary.")
    update_index(journal_path=mounted_journal, now=FIRST_RUN)
    file_path.unlink()

    result = update_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.deleted == 1
    assert result.chunks_removed >= 1
    assert result.chunks_total == 0


def test_rebuild_recreates_chunks(mounted_journal: Path) -> None:
    write_entry(mounted_journal, "journals/first.md", "First.")
    write_entry(mounted_journal, "journals/second.md", "Second.")
    update_index(journal_path=mounted_journal, now=FIRST_RUN)

    result = rebuild_index(journal_path=mounted_journal, now=SECOND_RUN)

    assert result.new == 2
    assert result.chunks_total == 2


def test_chunk_indices_are_unique_per_document(mounted_journal: Path) -> None:
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    write_entry(mounted_journal, "journals/entry.md", text)
    config = ChunkingConfig(
        target_characters=10,
        max_characters=20,
        overlap_characters=0,
    )

    update_index(journal_path=mounted_journal, now=FIRST_RUN, chunking=config)

    with open_index_database(index_database_path(mounted_journal)) as database:
        chunks = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )

    indices = [chunk.chunk_index for chunk in chunks]
    assert indices == list(range(len(indices)))


def test_failed_chunk_update_rolls_back(tmp_path: Path) -> None:
    database_path = tmp_path / "index.sqlite"
    document = IndexedDocument(
        relative_path=Path("journals/entry.md"),
        content_hash="hash",
        file_size=4,
        modified_at=FIRST_RUN,
        first_indexed_at=FIRST_RUN,
        last_indexed_at=FIRST_RUN,
    )
    chunk = chunk_markdown("ok", ChunkingConfig())[0]
    duplicate = TextChunk(
        chunk_index=0,
        content=chunk.content,
        content_hash=chunk.content_hash,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        character_count=chunk.character_count,
    )

    with open_index_database(database_path) as database:
        database.apply_changes(
            indexed_at=FIRST_RUN,
            inserted=[document],
            chunks_by_path={Path("journals/entry.md"): (chunk,)},
        )

        with pytest.raises(IndexDatabaseError):
            database.apply_changes(
                indexed_at=SECOND_RUN,
                updated=[document],
                chunks_by_path={
                    Path("journals/entry.md"): (duplicate, duplicate),
                },
            )

        assert database.chunk_count() == 1


def test_changing_max_characters_rechunks_without_content_change(
    mounted_journal: Path,
) -> None:
    content = "Sentence one. " * 80
    file_path = write_entry(mounted_journal, "journals/entry.md", content)
    original_bytes = file_path.read_bytes()
    original_mtime = file_path.stat().st_mtime_ns
    first_config = ChunkingConfig(
        target_characters=1000,
        max_characters=1600,
        overlap_characters=150,
    )
    second_config = ChunkingConfig(
        target_characters=800,
        max_characters=800,
        overlap_characters=150,
    )

    first = update_index(
        journal_path=mounted_journal,
        now=FIRST_RUN,
        chunking=first_config,
    )
    with open_index_database(index_database_path(mounted_journal)) as database:
        before = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )
        stored_signature = database.read_chunking_signature()

    second = update_index(
        journal_path=mounted_journal,
        now=SECOND_RUN,
        chunking=second_config,
    )

    assert first.chunks_total == 1
    assert second.changed == 0
    assert second.unchanged == 1
    assert second.chunks_created > 0
    assert second.chunks_removed == first.chunks_total
    assert second.chunks_total > first.chunks_total

    with open_index_database(index_database_path(mounted_journal)) as database:
        after = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )
        document = database.read_documents()[Path("journals/entry.md")]
        assert database.read_chunking_signature() == second_config.signature()

    assert stored_signature == first_config.signature()
    assert after != before
    assert document.last_indexed_at == FIRST_RUN
    assert file_path.read_bytes() == original_bytes
    assert file_path.stat().st_mtime_ns == original_mtime

    third = update_index(
        journal_path=mounted_journal,
        now=SECOND_RUN,
        chunking=second_config,
    )

    assert third.changed == 0
    assert third.chunks_created == 0
    assert third.chunks_removed == 0
    assert third.chunks_total == second.chunks_total


def test_changing_overlap_rechunks_then_stays_stable(
    mounted_journal: Path,
) -> None:
    content = "x" * 2000
    write_entry(mounted_journal, "journals/entry.md", content)
    first_config = ChunkingConfig(
        target_characters=1000,
        max_characters=1600,
        overlap_characters=150,
    )
    second_config = ChunkingConfig(
        target_characters=1000,
        max_characters=1600,
        overlap_characters=0,
    )

    update_index(
        journal_path=mounted_journal,
        now=FIRST_RUN,
        chunking=first_config,
    )
    with open_index_database(index_database_path(mounted_journal)) as database:
        before = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )

    changed_config = update_index(
        journal_path=mounted_journal,
        now=SECOND_RUN,
        chunking=second_config,
    )

    assert changed_config.changed == 0
    assert changed_config.unchanged == 1
    assert changed_config.chunks_created > 0
    assert changed_config.chunks_removed > 0

    with open_index_database(index_database_path(mounted_journal)) as database:
        after = database.read_chunks_for_document(
            database.read_document_ids()[Path("journals/entry.md")]
        )
        assert database.read_chunking_signature() == second_config.signature()

    assert after != before

    repeat = update_index(
        journal_path=mounted_journal,
        now=SECOND_RUN,
        chunking=second_config,
    )

    assert repeat.chunks_created == 0
    assert repeat.chunks_removed == 0
    assert repeat.has_changes is False


def test_rebuild_stores_current_chunking_signature(
    mounted_journal: Path,
) -> None:
    write_entry(mounted_journal, "journals/entry.md", "Rebuild me.")
    first_config = ChunkingConfig(target_characters=1000, max_characters=1600)
    second_config = ChunkingConfig(target_characters=800, max_characters=800)

    update_index(
        journal_path=mounted_journal,
        now=FIRST_RUN,
        chunking=first_config,
    )
    result = rebuild_index(
        journal_path=mounted_journal,
        now=SECOND_RUN,
        chunking=second_config,
    )

    assert result.new == 1
    assert result.chunks_total >= 1

    with open_index_database(index_database_path(mounted_journal)) as database:
        assert database.read_chunking_signature() == second_config.signature()


def test_failed_configuration_driven_rechunking_rolls_back(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "index.sqlite"
    relative_path = Path("journals/entry.md")
    document = IndexedDocument(
        relative_path=relative_path,
        content_hash="hash",
        file_size=4,
        modified_at=FIRST_RUN,
        first_indexed_at=FIRST_RUN,
        last_indexed_at=FIRST_RUN,
    )
    chunk = chunk_markdown("ok", ChunkingConfig())[0]
    duplicate = TextChunk(
        chunk_index=0,
        content=chunk.content,
        content_hash=chunk.content_hash,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        character_count=chunk.character_count,
    )

    with open_index_database(database_path) as database:
        database.apply_changes(
            indexed_at=FIRST_RUN,
            inserted=[document],
            chunks_by_path={relative_path: (chunk,)},
            chunking_signature="old-signature",
        )

        with pytest.raises(IndexDatabaseError):
            database.apply_changes(
                indexed_at=SECOND_RUN,
                chunks_by_path={relative_path: (duplicate, duplicate)},
                chunking_signature="new-signature",
            )

        assert database.read_chunking_signature() == "old-signature"
        assert database.chunk_count() == 1
        stored = database.read_chunks_for_document(
            database.read_document_ids()[relative_path]
        )
        assert stored[0].content == chunk.content
        assert database.read_last_indexed_at() == FIRST_RUN
