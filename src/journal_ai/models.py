from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class JournalDocument:
    """A read-only representation of one Markdown journal file."""

    path: Path
    relative_path: Path
    content: str
    modified_at: datetime

    @property
    def character_count(self) -> int:
        return len(self.content)

    @property
    def word_count(self) -> int:
        return len(self.content.split())


@dataclass(frozen=True, slots=True)
class SourceFileState:
    """Filesystem facts about one Markdown file, without its content."""

    relative_path: Path
    content_hash: str
    file_size: int
    modified_at: datetime


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """One inventory record describing a Markdown file in the index."""

    relative_path: Path
    content_hash: str
    file_size: int
    modified_at: datetime
    first_indexed_at: datetime
    last_indexed_at: datetime


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One deterministic chunk of source text before it is stored in SQLite."""

    chunk_index: int
    content: str
    content_hash: str
    start_line: int
    end_line: int
    character_count: int


@dataclass(frozen=True, slots=True)
class DocumentChunk(TextChunk):
    """One chunk row linked to an indexed source document."""

    document_id: int


@dataclass(frozen=True, slots=True)
class IndexResult:
    """How one index run classified the discovered Markdown files.

    The relative paths are kept so later components can process only the
    new and changed documents. They are never accompanied by file content.
    """

    new_paths: tuple[Path, ...] = ()
    changed_paths: tuple[Path, ...] = ()
    unchanged_paths: tuple[Path, ...] = ()
    deleted_paths: tuple[Path, ...] = ()
    chunks_created: int = 0
    chunks_removed: int = 0
    chunks_total: int = 0

    @property
    def new(self) -> int:
        return len(self.new_paths)

    @property
    def changed(self) -> int:
        return len(self.changed_paths)

    @property
    def unchanged(self) -> int:
        return len(self.unchanged_paths)

    @property
    def deleted(self) -> int:
        return len(self.deleted_paths)

    @property
    def has_changes(self) -> bool:
        return bool(self.new_paths or self.changed_paths or self.deleted_paths)


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """Index metadata that can be reported without scanning the journal."""

    database_path: Path
    database_exists: bool
    document_count: int
    chunk_count: int
    last_indexed_at: datetime | None
