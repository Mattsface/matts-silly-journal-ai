from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from journal_ai.cli import analyze_document
from journal_ai.config import AppConfig, OllamaConfig
from journal_ai.journal_reader import find_markdown_files


def make_mock_response(data: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(data).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


@patch("journal_ai.journal_reader.is_mountpoint", return_value=True)
@patch("journal_ai.ollama_client.urlopen")
def test_analyze_save_writes_visible_content_without_changing_source(
    mock_urlopen: MagicMock,
    _mock_is_mountpoint: object,
    tmp_path: Path,
    capsys: object,
) -> None:
    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    journals_dir.mkdir(parents=True)

    source_relative = Path("journals") / "entry.md"
    source_path = journal_path / source_relative
    original_content = "Today I focused on recovery."
    source_path.write_text(original_content, encoding="utf-8")

    mock_urlopen.return_value = make_mock_response(
        {
            "model": "test-model",
            "response": "## Main themes\nRecovery and focus.",
            "prompt_eval_count": 12,
            "eval_count": 9,
        }
    )

    config = AppConfig(
        journal_path=journal_path,
        ollama=OllamaConfig(model="test-model"),
    )

    exit_code = analyze_document(
        config=config,
        relative_file=source_relative,
        save=True,
    )

    assert exit_code == 0
    assert source_path.read_text(encoding="utf-8") == original_content

    analyses_dir = journal_path / "generated" / "analyses"
    saved_files = list(analyses_dir.glob("*.md"))
    assert len(saved_files) == 1

    saved = saved_files[0].read_text(encoding="utf-8")
    assert "Source: `journals/entry.md`" in saved
    assert "Model: `test-model`" in saved
    assert "---\n\n## Main themes\nRecovery and focus.\n" in saved

    discovered = find_markdown_files(journal_path)
    assert discovered == [source_path.resolve()]

    captured = capsys.readouterr()
    assert "Recovery and focus." in captured.out
    assert "Saved analysis to:" in captured.out
