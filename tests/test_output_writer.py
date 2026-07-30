from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from journal_ai.output_writer import OutputWriteError, save_analysis


def test_save_analysis_writes_visible_content(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()
    source_relative_path = Path("journals") / "2026_07_28.md"
    generated_at = datetime(2026, 7, 30, 16, 0, 0, tzinfo=UTC)

    output_path = save_analysis(
        journal_path=journal_path,
        source_relative_path=source_relative_path,
        model="qwen3.5:4b",
        analysis_text="## Main themes\nFocus and recovery.",
        generated_at=generated_at,
    )

    saved = output_path.read_text(encoding="utf-8")

    assert output_path.parent == journal_path / "generated" / "analyses"
    assert "Source: `journals/2026_07_28.md`" in saved
    assert "Model: `qwen3.5:4b`" in saved
    assert "Generated: `2026-07-30T16:00:00+00:00`" in saved
    assert "---\n\n## Main themes\nFocus and recovery.\n" in saved


def test_save_analysis_rejects_empty_text(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journal_path.mkdir()

    with pytest.raises(OutputWriteError, match="empty or whitespace-only"):
        save_analysis(
            journal_path=journal_path,
            source_relative_path=Path("journals") / "entry.md",
            model="test-model",
            analysis_text="   \n",
        )


def test_save_analysis_leaves_source_unchanged(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    journals_dir.mkdir(parents=True)

    source_path = journals_dir / "entry.md"
    original_content = "Original journal entry."
    source_path.write_text(original_content, encoding="utf-8")

    save_analysis(
        journal_path=journal_path,
        source_relative_path=Path("journals") / "entry.md",
        model="test-model",
        analysis_text="Visible analysis body.",
        generated_at=datetime(2026, 7, 30, 16, 0, 0, tzinfo=UTC),
    )

    assert source_path.read_text(encoding="utf-8") == original_content


@patch("journal_ai.journal_reader.is_mountpoint", return_value=True)
def test_saved_analysis_excluded_from_source_discovery(
    _mock_is_mountpoint: object,
    tmp_path: Path,
) -> None:
    from journal_ai.journal_reader import find_markdown_files

    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    journals_dir.mkdir(parents=True)

    source_path = journals_dir / "entry.md"
    source_path.write_text("Source entry.", encoding="utf-8")

    save_analysis(
        journal_path=journal_path,
        source_relative_path=Path("journals") / "entry.md",
        model="test-model",
        analysis_text="Visible analysis body.",
        generated_at=datetime(2026, 7, 30, 16, 0, 0, tzinfo=UTC),
    )

    discovered = find_markdown_files(journal_path)

    assert discovered == [source_path.resolve()]
