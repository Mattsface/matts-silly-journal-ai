from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from journal_ai.chunking import chunk_markdown
from journal_ai.config import ChunkingConfig, index_database_path
from journal_ai.hashing import hash_file
from journal_ai.index_database import (
    delete_index_database,
    open_index_database,
)
from journal_ai.journal_reader import (
    JournalReadError,
    ensure_journal_mounted,
    find_markdown_files,
    read_markdown_file,
)
from journal_ai.models import (
    IndexedDocument,
    IndexResult,
    IndexStatus,
    SourceFileState,
    TextChunk,
)


@dataclass(frozen=True, slots=True)
class _IndexPlan:
    """The database work required to make the index match the filesystem."""

    inserted: tuple[IndexedDocument, ...]
    changed: tuple[IndexedDocument, ...]
    refreshed: tuple[IndexedDocument, ...]
    deleted: tuple[Path, ...]
    unchanged: tuple[Path, ...]


def read_source_state(file_path: Path, journal_path: Path) -> SourceFileState:
    """Collect index metadata for one Markdown file inside the journal.

    Only the file's hash and filesystem metadata are collected. The content
    itself is never returned or stored.
    """
    resolved_file_path = file_path.expanduser().resolve()
    resolved_journal_path = journal_path.expanduser().resolve()

    try:
        relative_path = resolved_file_path.relative_to(resolved_journal_path)
    except ValueError as exc:
        raise JournalReadError(
            f"File is outside the journal directory: {resolved_file_path}"
        ) from exc

    if not resolved_file_path.is_file():
        raise JournalReadError(
            f"Journal file does not exist: {resolved_file_path}"
        )

    try:
        stat_result = resolved_file_path.stat()
    except OSError as exc:
        raise JournalReadError(
            f"Could not read journal file: {resolved_file_path}"
        ) from exc

    return SourceFileState(
        relative_path=relative_path,
        content_hash=hash_file(resolved_file_path),
        file_size=stat_result.st_size,
        modified_at=datetime.fromtimestamp(stat_result.st_mtime, tz=UTC),
    )


def scan_journal(journal_path: Path) -> tuple[SourceFileState, ...]:
    """Collect index metadata for every discoverable source Markdown file."""
    resolved_journal_path = journal_path.expanduser().resolve()

    return tuple(
        read_source_state(file_path, resolved_journal_path)
        for file_path in find_markdown_files(resolved_journal_path)
    )


def update_index(
    *,
    journal_path: Path,
    database_path: Path | None = None,
    now: datetime | None = None,
    chunking: ChunkingConfig | None = None,
) -> IndexResult:
    """Scan the journal and bring the SQLite index up to date."""
    resolved_journal_path = journal_path.expanduser().resolve()
    target_path = database_path or index_database_path(resolved_journal_path)
    indexed_at = _as_utc(now)
    chunking_config = chunking or ChunkingConfig()

    sources = scan_journal(resolved_journal_path)

    with open_index_database(target_path) as database:
        plan = _plan_changes(
            sources,
            database.read_documents(),
            indexed_at=indexed_at,
        )
        current_signature = chunking_config.signature()
        paths_to_chunk = {
            document.relative_path for document in plan.inserted + plan.changed
        }
        if database.read_chunking_signature() != current_signature:
            paths_to_chunk.update(plan.unchanged)

        chunks_by_path = _chunks_for_documents(
            resolved_journal_path,
            paths_to_chunk,
            chunking_config,
        )

        chunk_counts = database.apply_changes(
            indexed_at=indexed_at,
            inserted=plan.inserted,
            updated=plan.changed + plan.refreshed,
            deleted=plan.deleted,
            chunks_by_path=chunks_by_path,
            chunking_signature=current_signature,
        )

        total_chunks = database.chunk_count()

    return IndexResult(
        new_paths=tuple(
            document.relative_path for document in plan.inserted
        ),
        changed_paths=tuple(
            document.relative_path for document in plan.changed
        ),
        unchanged_paths=plan.unchanged,
        deleted_paths=plan.deleted,
        chunks_created=chunk_counts.created,
        chunks_removed=chunk_counts.removed,
        chunks_total=total_chunks,
    )


def rebuild_index(
    *,
    journal_path: Path,
    database_path: Path | None = None,
    now: datetime | None = None,
    chunking: ChunkingConfig | None = None,
) -> IndexResult:
    """Delete the existing index and build a new one from source files."""
    resolved_journal_path = ensure_journal_mounted(journal_path)
    target_path = database_path or index_database_path(resolved_journal_path)

    delete_index_database(target_path)

    return update_index(
        journal_path=resolved_journal_path,
        database_path=target_path,
        now=now,
        chunking=chunking,
    )


def read_index_status(
    *,
    journal_path: Path,
    database_path: Path | None = None,
) -> IndexStatus:
    """Report index metadata without scanning or touching source files."""
    resolved_journal_path = ensure_journal_mounted(journal_path)
    target_path = database_path or index_database_path(resolved_journal_path)

    if not target_path.exists():
        return IndexStatus(
            database_path=target_path,
            database_exists=False,
            document_count=0,
            chunk_count=0,
            last_indexed_at=None,
        )

    with open_index_database(target_path) as database:
        return IndexStatus(
            database_path=target_path,
            database_exists=True,
            document_count=database.document_count(),
            chunk_count=database.chunk_count(),
            last_indexed_at=database.read_last_indexed_at(),
        )


def _chunks_for_documents(
    journal_path: Path,
    relative_paths: Iterable[Path],
    chunking: ChunkingConfig,
) -> dict[Path, tuple[TextChunk, ...]]:
    """Read and chunk the given documents, leaving others unread."""
    chunks_by_path: dict[Path, tuple[TextChunk, ...]] = {}

    for relative_path in sorted(relative_paths):
        file_path = journal_path / relative_path
        document = read_markdown_file(file_path, journal_path)
        chunks_by_path[relative_path] = chunk_markdown(
            document.content,
            chunking,
        )

    return chunks_by_path


def _plan_changes(
    sources: Iterable[SourceFileState],
    indexed: Mapping[Path, IndexedDocument],
    *,
    indexed_at: datetime,
) -> _IndexPlan:
    """Classify scanned files against the records already in the index."""
    inserted: list[IndexedDocument] = []
    changed: list[IndexedDocument] = []
    refreshed: list[IndexedDocument] = []
    unchanged: list[Path] = []
    seen: set[Path] = set()

    for source in sources:
        seen.add(source.relative_path)
        record = indexed.get(source.relative_path)

        if record is None:
            inserted.append(
                IndexedDocument(
                    relative_path=source.relative_path,
                    content_hash=source.content_hash,
                    file_size=source.file_size,
                    modified_at=source.modified_at,
                    first_indexed_at=indexed_at,
                    last_indexed_at=indexed_at,
                )
            )
            continue

        updated_record = IndexedDocument(
            relative_path=record.relative_path,
            content_hash=source.content_hash,
            file_size=source.file_size,
            modified_at=source.modified_at,
            first_indexed_at=record.first_indexed_at,
            last_indexed_at=indexed_at,
        )

        # The content hash alone decides whether a file changed, so a new
        # modification time or size never counts as a content change.
        if record.content_hash != source.content_hash:
            changed.append(updated_record)
            continue

        unchanged.append(source.relative_path)

        if _filesystem_metadata_drifted(record, source):
            refreshed.append(updated_record)

    deleted = tuple(
        relative_path
        for relative_path in sorted(indexed)
        if relative_path not in seen
    )

    return _IndexPlan(
        inserted=tuple(inserted),
        changed=tuple(changed),
        refreshed=tuple(refreshed),
        deleted=deleted,
        unchanged=tuple(unchanged),
    )


def _filesystem_metadata_drifted(
    record: IndexedDocument,
    source: SourceFileState,
) -> bool:
    return (
        record.file_size != source.file_size
        or record.modified_at != source.modified_at
    )


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
