from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request

import pytest

from journal_ai.cli import build_parser, main, resolve_config
from journal_ai.config import (
    DEFAULT_NUM_PREDICT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    AppConfig,
    ConfigError,
    default_journal_path,
)

FULL_CONFIG = """
journal_path = "/tmp/configured-journal"

[ollama]
url = "http://configured:11434"
model = "configured-model"
timeout_seconds = 111
num_predict = 11
think = false
"""


def write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def resolve(*argv: str) -> AppConfig:
    return resolve_config(build_parser().parse_args(argv))


def make_mock_response(data: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(data).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def request_payload(mock_urlopen: MagicMock) -> dict[str, object]:
    request = mock_urlopen.call_args.args[0]
    assert isinstance(request, Request)
    assert request.data is not None
    parsed = json.loads(request.data.decode("utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_defaults_are_used_without_a_config_file() -> None:
    config = resolve("list")

    assert config.journal_path == default_journal_path()
    assert config.ollama.url == DEFAULT_OLLAMA_URL
    assert config.ollama.model == DEFAULT_OLLAMA_MODEL
    assert config.ollama.num_predict == DEFAULT_NUM_PREDICT
    assert config.ollama.think is False


def test_config_file_values_are_used(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve("--config", str(config_path), "list")

    assert config.journal_path == Path("/tmp/configured-journal")
    assert config.ollama.url == "http://configured:11434"
    assert config.ollama.model == "configured-model"
    assert config.ollama.timeout_seconds == 111.0
    assert config.ollama.num_predict == 11


def test_cli_journal_path_overrides_config(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve(
        "--config",
        str(config_path),
        "--journal-path",
        "/tmp/cli-journal",
        "list",
    )

    assert config.journal_path == Path("/tmp/cli-journal")


def test_cli_ollama_url_overrides_config(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve(
        "--config",
        str(config_path),
        "analyze",
        "journals/entry.md",
        "--ollama-url",
        "http://cli:11434",
    )

    assert config.ollama.url == "http://cli:11434"
    assert config.ollama.model == "configured-model"


def test_cli_model_overrides_config(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve(
        "--config",
        str(config_path),
        "analyze",
        "journals/entry.md",
        "--model",
        "cli-model",
    )

    assert config.ollama.model == "cli-model"


def test_cli_timeout_overrides_config(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve(
        "--config",
        str(config_path),
        "analyze",
        "journals/entry.md",
        "--timeout-seconds",
        "5.5",
    )

    assert config.ollama.timeout_seconds == 5.5


def test_cli_num_predict_overrides_config(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve(
        "--config",
        str(config_path),
        "analyze",
        "journals/entry.md",
        "--num-predict",
        "77",
    )

    assert config.ollama.num_predict == 77


def test_think_overrides_configured_false(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve(
        "--config",
        str(config_path),
        "analyze",
        "journals/entry.md",
        "--think",
    )

    assert config.ollama.think is True


def test_no_think_overrides_configured_true(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
        [ollama]
        think = true
        """,
    )

    config = resolve(
        "--config",
        str(config_path),
        "analyze",
        "journals/entry.md",
        "--no-think",
    )

    assert config.ollama.think is False


def test_settings_are_accepted_before_the_subcommand(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, FULL_CONFIG)

    config = resolve(
        "--config",
        str(config_path),
        "--model",
        "cli-model",
        "analyze",
        "journals/entry.md",
    )

    assert config.ollama.model == "cli-model"


def test_think_and_no_think_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["analyze", "journals/entry.md", "--think", "--no-think"]
        )


def test_invalid_config_file_reports_an_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = write_config(tmp_path, "journal_path = \n")

    exit_code = main(["--config", str(config_path), "list"])

    assert exit_code == 1
    assert "Invalid TOML" in capsys.readouterr().out


def test_cli_rejects_non_positive_num_predict(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--num-predict must be greater"):
        resolve("analyze", "journals/entry.md", "--num-predict", "0")


@patch("journal_ai.journal_reader.is_mountpoint", return_value=True)
def test_list_command_still_works(
    _mock_is_mountpoint: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    journals_dir.mkdir(parents=True)
    (journals_dir / "entry.md").write_text(
        "Today was calm.",
        encoding="utf-8",
    )

    exit_code = main(["--journal-path", str(journal_path), "list"])

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Loaded 1 Markdown document(s):" in captured
    assert "journals/entry.md" in captured


@patch("journal_ai.journal_reader.is_mountpoint", return_value=True)
def test_list_command_uses_configured_journal_path(
    _mock_is_mountpoint: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    journals_dir.mkdir(parents=True)
    (journals_dir / "entry.md").write_text("Calm day.", encoding="utf-8")

    config_path = write_config(
        tmp_path,
        f'journal_path = "{journal_path}"\n',
    )

    exit_code = main(["--config", str(config_path), "list"])

    assert exit_code == 0
    assert "journals/entry.md" in capsys.readouterr().out


@patch("journal_ai.ollama_client.urlopen")
def test_analyze_payload_uses_resolved_settings(
    mock_urlopen: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    journals_dir.mkdir(parents=True)
    (journals_dir / "entry.md").write_text(
        "Today I rested.",
        encoding="utf-8",
    )

    config_path = write_config(
        tmp_path,
        f"""
        journal_path = "{journal_path}"

        [ollama]
        model = "configured-model"
        num_predict = 25
        think = true
        """,
    )

    mock_urlopen.return_value = make_mock_response(
        {
            "model": "configured-model",
            "response": "## Main themes\nRest.",
        }
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "analyze",
            "journals/entry.md",
        ]
    )

    assert exit_code == 0

    payload = request_payload(mock_urlopen)
    assert payload["model"] == "configured-model"
    assert payload["think"] is True
    assert payload["options"] == {"num_predict": 25}
    assert payload["stream"] is False
    assert "Rest." in capsys.readouterr().out


@patch("journal_ai.ollama_client.urlopen")
def test_analyze_payload_prefers_cli_overrides(
    mock_urlopen: MagicMock,
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "journal"
    journals_dir = journal_path / "journals"
    journals_dir.mkdir(parents=True)
    (journals_dir / "entry.md").write_text(
        "Today I rested.",
        encoding="utf-8",
    )

    config_path = write_config(
        tmp_path,
        f"""
        journal_path = "{journal_path}"

        [ollama]
        model = "configured-model"
        num_predict = 25
        think = true
        """,
    )

    mock_urlopen.return_value = make_mock_response(
        {
            "model": "cli-model",
            "response": "## Main themes\nRest.",
        }
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "analyze",
            "journals/entry.md",
            "--model",
            "cli-model",
            "--num-predict",
            "9",
            "--no-think",
        ]
    )

    assert exit_code == 0

    payload = request_payload(mock_urlopen)
    assert payload["model"] == "cli-model"
    assert payload["think"] is False
    assert payload["options"] == {"num_predict": 9}
