from __future__ import annotations

import pytest

from journal_ai.chunking import chunk_markdown
from journal_ai.config import ChunkingConfig, ConfigError
from journal_ai.hashing import hash_text


def default_config(**overrides: int) -> ChunkingConfig:
    values = {
        "target_characters": 1000,
        "max_characters": 1600,
        "minimum_characters": 250,
        "overlap_characters": 0,
    }
    values.update(overrides)
    return ChunkingConfig(**values)


def joined_chunks(text: str, config: ChunkingConfig | None = None) -> str:
    chunks = chunk_markdown(text, config or default_config())
    return "".join(chunk.content for chunk in chunks)


def test_empty_content_returns_no_chunks() -> None:
    assert chunk_markdown("", default_config()) == ()
    assert chunk_markdown("   \n\n  ", default_config()) == ()


def test_short_paragraph_creates_one_chunk() -> None:
    text = "Today was calm."
    chunks = chunk_markdown(text, default_config())

    assert len(chunks) == 1
    assert chunks[0].content == text
    assert chunks[0].chunk_index == 0
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 1
    assert chunks[0].content_hash == hash_text(text)


def test_multiple_paragraphs_combine_when_small() -> None:
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_markdown(text, default_config())

    assert len(chunks) == 1
    assert chunks[0].content == text


def test_heading_stays_with_following_content() -> None:
    text = "## Work\nI handled the rush better today."
    chunks = chunk_markdown(text, default_config())

    assert len(chunks) == 1
    assert chunks[0].content == text
    assert "## Work" in chunks[0].content
    assert "rush" in chunks[0].content


def test_logseq_top_level_bullet_keeps_nested_children() -> None:
    text = (
        "- Work was difficult today.\n"
        "  - I became overwhelmed during the rush.\n"
        "  - I recovered faster than usual.\n"
        "- I went for a walk after work."
    )
    config = default_config(target_characters=60, max_characters=80, minimum_characters=0)
    chunks = chunk_markdown(text, config)

    assert len(chunks) == 2
    assert "overwhelmed" in chunks[0].content
    assert "walk" in chunks[1].content
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1


def test_chunk_order_matches_source_order() -> None:
    text = "Alpha.\n\nBeta.\n\nGamma."
    config = default_config(target_characters=5, max_characters=7, minimum_characters=0)
    chunks = chunk_markdown(text, config)

    assert [chunk.content for chunk in chunks] == ["Alpha.", "Beta.", "Gamma."]


def test_repeated_runs_are_identical() -> None:
    text = "## Sleep\nRested well.\n\n## Work\nBusy day."
    first = chunk_markdown(text, default_config())
    second = chunk_markdown(text, default_config())

    assert first == second


def test_chunk_hashes_are_stable() -> None:
    text = "Stable hashing matters."
    chunk = chunk_markdown(text, default_config())[0]

    assert chunk.content_hash == hash_text(chunk.content)


def test_line_numbers_are_one_based() -> None:
    text = "Line one.\nLine two.\n\nLine four."
    config = default_config(target_characters=12, max_characters=20, minimum_characters=0)
    chunks = chunk_markdown(text, config)

    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2
    assert chunks[1].start_line == 4
    assert chunks[1].end_line == 4


def test_chunks_respect_configured_maximum() -> None:
    text = "word " * 500
    config = default_config(target_characters=200, max_characters=300, minimum_characters=0)
    chunks = chunk_markdown(text, config)

    assert chunks
    assert all(chunk.character_count <= config.max_characters for chunk in chunks)


def test_small_blocks_combine_toward_target() -> None:
    text = "a" * 100 + "\n\n" + "b" * 100 + "\n\n" + "c" * 100
    config = default_config(target_characters=250, max_characters=400, minimum_characters=0)
    chunks = chunk_markdown(text, config)

    assert len(chunks) == 1
    assert len(chunks[0].content) > 250


def test_oversized_paragraph_splits_without_loss() -> None:
    text = "Sentence one. " * 200
    config = default_config(target_characters=100, max_characters=120, minimum_characters=0)
    chunks = chunk_markdown(text, config)

    assert len(chunks) > 1
    assert joined_chunks(text, config) == text
def test_long_unbroken_string_splits_by_character() -> None:
    text = "x" * 500
    config = default_config(
        target_characters=100,
        max_characters=120,
        minimum_characters=0,
        overlap_characters=20,
    )
    chunks = chunk_markdown(text, config)

    assert len(chunks) > 1
    assert all(chunk.character_count <= config.max_characters for chunk in chunks)


def test_invalid_chunk_configuration_is_rejected() -> None:
    with pytest.raises(ConfigError):
        ChunkingConfig(target_characters=2000, max_characters=1600)

    with pytest.raises(ConfigError):
        ChunkingConfig(overlap_characters=1600, max_characters=1600)


def test_no_source_text_is_lost_for_structured_markdown() -> None:
    text = (
        "## Work\n"
        "Handled tickets.\n\n"
        "- Bullet one.\n"
        "  - Nested detail.\n"
        "- Bullet two.\n"
    )
    chunks = chunk_markdown(text, default_config())
    combined = "".join(chunk.content for chunk in chunks)

    assert combined == text


def test_source_text_is_not_reordered() -> None:
    text = "First\n\nSecond\n\nThird"
    config = default_config(target_characters=8, max_characters=12, minimum_characters=0)
    chunks = chunk_markdown(text, config)
    positions = [text.index(chunk.content) for chunk in chunks]

    assert positions == sorted(positions)


def test_character_overlap_is_deterministic() -> None:
    text = "x" * 400
    config = default_config(
        target_characters=80,
        max_characters=100,
        minimum_characters=0,
        overlap_characters=25,
    )
    first = chunk_markdown(text, config)
    second = chunk_markdown(text, config)

    assert first == second
    assert len(first) > 1
    assert first[0].content[-25:] == first[1].content[:25]
