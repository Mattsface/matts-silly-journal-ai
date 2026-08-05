"""Deterministic Markdown chunking for journal source files."""

from __future__ import annotations

import re
from dataclasses import dataclass

from journal_ai.config import ChunkingConfig
from journal_ai.hashing import hash_text
from journal_ai.models import TextChunk

_HEADING_PATTERN = re.compile(r"^#{1,6}\s")
_TOP_LEVEL_BULLET_PATTERN = re.compile(r"^-\s")
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class _SourceBlock:
    """One structural unit of the original Markdown with source coordinates."""

    start_line: int
    end_line: int
    start_char: int
    end_char: int


def chunk_markdown(text: str, config: ChunkingConfig) -> tuple[TextChunk, ...]:
    """Split Markdown into ordered, deterministic chunks.

  Empty or whitespace-only input produces no chunks. Each non-empty chunk
  stores an exact substring of the source text. Line numbers are one-based
  and inclusive. Overlap is applied only when oversized blocks are split.
  """
    if not text or not text.strip():
        return ()

    line_starts = _line_start_offsets(text)
    blocks = _parse_structural_blocks(text, line_starts)
    sized_blocks = _enforce_max_size(blocks, text, config)
    merged_blocks = _merge_small_blocks(sized_blocks, text, config)
    return _blocks_to_chunks(merged_blocks, text)


def _line_start_offsets(text: str) -> list[int]:
    starts = [0]
    for index, character in enumerate(text):
        if character == "\n" and index + 1 < len(text):
            starts.append(index + 1)
    return starts


def _offset_to_line(offset: int, line_starts: list[int]) -> int:
    line = 1
    for start in line_starts[1:]:
        if start > offset:
            return line
        line += 1
    return line


def _line_range(start_char: int, end_char: int, line_starts: list[int]) -> tuple[int, int]:
    return (
        _offset_to_line(start_char, line_starts),
        _offset_to_line(max(start_char, end_char - 1), line_starts),
    )


def _parse_structural_blocks(text: str, line_starts: list[int]) -> list[_SourceBlock]:
    blocks: list[_SourceBlock] = []
    length = len(text)
    index = 0

    while index < length:
        line_start = _line_start_index(index, text)
        line = text[line_start : _line_end_index(line_start, text)]

        if _HEADING_PATTERN.match(line):
            index = _consume_heading_section(text, line_starts, index, blocks)
            continue

        if _TOP_LEVEL_BULLET_PATTERN.match(line) and not line.startswith((" ", "\t")):
            index = _consume_logseq_block(text, line_starts, index, blocks)
            continue

        index = _consume_paragraph(text, line_starts, index, blocks)

    return blocks


def _line_start_index(index: int, text: str) -> int:
    previous_newline = text.rfind("\n", 0, index)
    return 0 if previous_newline == -1 else previous_newline + 1


def _line_end_index(line_start: int, text: str) -> int:
    next_newline = text.find("\n", line_start)
    return len(text) if next_newline == -1 else next_newline


def _append_block(
    blocks: list[_SourceBlock],
    *,
    text: str,
    line_starts: list[int],
    start_char: int,
    end_char: int,
) -> None:
    if start_char >= end_char:
        return

    start_line, end_line = _line_range(start_char, end_char, line_starts)
    blocks.append(
        _SourceBlock(
            start_line=start_line,
            end_line=end_line,
            start_char=start_char,
            end_char=end_char,
        )
    )


def _consume_heading_section(
    text: str,
    line_starts: list[int],
    index: int,
    blocks: list[_SourceBlock],
) -> int:
    start_char = index
    cursor = index

    while cursor < len(text):
        line_start = cursor
        line_end = _line_end_index(line_start, text)
        line = text[line_start:line_end]

        if cursor != start_char and _HEADING_PATTERN.match(line):
            break

        cursor = line_end + 1 if line_end < len(text) else len(text)

    _append_block(
        blocks,
        text=text,
        line_starts=line_starts,
        start_char=start_char,
        end_char=min(len(text), cursor),
    )
    return min(len(text), cursor)


def _consume_logseq_block(
    text: str,
    line_starts: list[int],
    index: int,
    blocks: list[_SourceBlock],
) -> int:
    start_char = index
    cursor = index

    while cursor < len(text):
        line_start = cursor
        line_end = _line_end_index(line_start, text)
        line = text[line_start:line_end]

        if cursor != start_char:
            if _HEADING_PATTERN.match(line):
                break
            if (
                _TOP_LEVEL_BULLET_PATTERN.match(line)
                and not line.startswith((" ", "\t"))
            ):
                break

        cursor = line_end + 1 if line_end < len(text) else len(text)

    _append_block(
        blocks,
        text=text,
        line_starts=line_starts,
        start_char=start_char,
        end_char=min(len(text), cursor),
    )
    return min(len(text), cursor)


def _consume_paragraph(
    text: str,
    line_starts: list[int],
    index: int,
    blocks: list[_SourceBlock],
) -> int:
    start_char = index
    cursor = index

    while cursor < len(text):
        line_start = cursor
        line_end = _line_end_index(line_start, text)
        line = text[line_start:line_end]

        if cursor != start_char:
            if not line.strip():
                break
            if _HEADING_PATTERN.match(line):
                break
            if (
                _TOP_LEVEL_BULLET_PATTERN.match(line)
                and not line.startswith((" ", "\t"))
            ):
                break

        cursor = line_end + 1 if line_end < len(text) else len(text)

    end_char = cursor
    if end_char < len(text) and text[end_char - 1] == "\n":
        end_char -= 1

    _append_block(
        blocks,
        text=text,
        line_starts=line_starts,
        start_char=start_char,
        end_char=max(start_char, end_char),
    )

    if cursor < len(text) and text[cursor : cursor + 1] == "\n":
        return cursor + 1

    return cursor


def _enforce_max_size(
    blocks: list[_SourceBlock],
    text: str,
    config: ChunkingConfig,
) -> list[_SourceBlock]:
    sized: list[_SourceBlock] = []

    for block in blocks:
        content = text[block.start_char : block.end_char]
        if len(content) <= config.max_characters:
            sized.append(block)
            continue

        sized.extend(_split_oversized_block(block, text, config))

    return sized


def _split_oversized_block(
    block: _SourceBlock,
    text: str,
    config: ChunkingConfig,
) -> list[_SourceBlock]:
    content = text[block.start_char : block.end_char]
    line_starts = _line_start_offsets(text)
    results: list[_SourceBlock] = []

    for start_offset, end_offset in _split_text_ranges(content, config):
        start_char = block.start_char + start_offset
        end_char = block.start_char + end_offset
        start_line, end_line = _line_range(start_char, end_char, line_starts)
        results.append(
            _SourceBlock(
                start_line=start_line,
                end_line=end_line,
                start_char=start_char,
                end_char=end_char,
            )
        )

    return results


def _split_text_ranges(content: str, config: ChunkingConfig) -> list[tuple[int, int]]:
    if len(content) <= config.max_characters:
        return [(0, len(content))]

    line_spans = _line_spans(content)
    if len(line_spans) > 1:
        return _pack_span_ranges(line_spans, content, config)

    sentence_spans = _sentence_spans(content)
    if len(sentence_spans) > 1:
        return _pack_span_ranges(sentence_spans, content, config)

    return _pack_span_ranges([(0, len(content))], content, config)


def _line_spans(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = 0

    for line in content.splitlines(keepends=True):
        end = position + len(line)
        spans.append((position, end))
        position = end

    return spans


def _sentence_spans(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0

    for match in _SENTENCE_BOUNDARY_PATTERN.finditer(content):
        end = match.start() + 1
        spans.append((start, end))
        start = match.end()

    if start < len(content):
        spans.append((start, len(content)))

    if not spans:
        spans.append((0, len(content)))

    return spans


def _pack_span_ranges(
    spans: list[tuple[int, int]],
    content: str,
    config: ChunkingConfig,
) -> list[tuple[int, int]]:
    packed: list[tuple[int, int]] = []
    current_start, current_end = spans[0]

    for next_start, next_end in spans[1:]:
        combined_end = next_end
        combined_len = combined_end - current_start

        if combined_len <= config.max_characters:
            current_end = combined_end
            continue

        packed.extend(_split_span((current_start, current_end), content, config))
        current_start, current_end = next_start, next_end

    packed.extend(_split_span((current_start, current_end), content, config))
    return packed


def _split_span(
    span: tuple[int, int],
    content: str,
    config: ChunkingConfig,
) -> list[tuple[int, int]]:
    start, end = span
    piece = content[start:end]
    if len(piece) <= config.max_characters:
        return [span]

    line_spans = _line_spans(piece)
    if len(line_spans) > 1:
        adjusted = [(start + line_start, start + line_end) for line_start, line_end in line_spans]
        return _pack_span_ranges(adjusted, content, config)

    sentence_spans = _sentence_spans(piece)
    if len(sentence_spans) > 1:
        adjusted = [(start + sentence_start, start + sentence_end) for sentence_start, sentence_end in sentence_spans]
        return _pack_span_ranges(adjusted, content, config)

    return _character_span_ranges(start, end, config)


def _character_span_ranges(
    start: int,
    end: int,
    config: ChunkingConfig,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    step = max(1, config.max_characters - config.overlap_characters)
    offset = start
    limit = end

    while offset < limit:
        piece_end = min(limit, offset + config.max_characters)
        ranges.append((offset, piece_end))
        if piece_end >= limit:
            break
        offset += step

    return ranges


def _merge_small_blocks(
    blocks: list[_SourceBlock],
    text: str,
    config: ChunkingConfig,
) -> list[_SourceBlock]:
    if not blocks:
        return []

    merged: list[_SourceBlock] = []
    current = blocks[0]

    for block in blocks[1:]:
        current_text = text[current.start_char : current.end_char]
        block_text = text[block.start_char : block.end_char]
        separator = _join_separator(text, current.end_char, block.start_char)
        combined_len = len(current_text) + len(separator) + len(block_text)

        should_merge = combined_len <= config.max_characters and (
            len(current_text) < config.target_characters
            or combined_len <= config.target_characters
        )

        if should_merge:
            current = _SourceBlock(
                start_line=current.start_line,
                end_line=block.end_line,
                start_char=current.start_char,
                end_char=block.end_char,
            )
            continue

        merged.append(current)
        current = block

    merged.append(current)
    return merged


def _join_separator(text: str, left_end: int, right_start: int) -> str:
    return text[left_end:right_start]


def _blocks_to_chunks(
    blocks: list[_SourceBlock],
    text: str,
) -> tuple[TextChunk, ...]:
    chunks: list[TextChunk] = []

    for index, block in enumerate(blocks):
        content = text[block.start_char : block.end_char]
        chunks.append(
            TextChunk(
                chunk_index=index,
                content=content,
                content_hash=hash_text(content),
                start_line=block.start_line,
                end_line=block.end_line,
                character_count=len(content),
            )
        )

    return tuple(chunks)
