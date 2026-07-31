from __future__ import annotations

from pathlib import Path

import pytest

from journal_ai.config import (
    DEFAULT_NUM_PREDICT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_THINK,
    DEFAULT_TIMEOUT_SECONDS,
    AppConfig,
    ConfigError,
    apply_overrides,
    default_journal_path,
    load_config,
)


def write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def assert_defaults(config: AppConfig) -> None:
    assert config.journal_path == default_journal_path()
    assert config.ollama.url == DEFAULT_OLLAMA_URL
    assert config.ollama.model == DEFAULT_OLLAMA_MODEL
    assert config.ollama.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert config.ollama.num_predict == DEFAULT_NUM_PREDICT
    assert config.ollama.think is DEFAULT_THINK


def test_missing_config_file_uses_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")

    assert_defaults(config)


def test_empty_config_file_uses_defaults(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, ""))

    assert_defaults(config)


def test_partial_config_merges_with_defaults(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
        [ollama]
        model = "custom-model"
        """,
    )

    config = load_config(config_path)

    assert config.ollama.model == "custom-model"
    assert config.journal_path == default_journal_path()
    assert config.ollama.url == DEFAULT_OLLAMA_URL
    assert config.ollama.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert config.ollama.num_predict == DEFAULT_NUM_PREDICT
    assert config.ollama.think is DEFAULT_THINK


def test_full_config_is_loaded(tmp_path: Path) -> None:
    journal_path = tmp_path / "journal"
    config_path = write_config(
        tmp_path,
        f"""
        journal_path = "{journal_path}"

        [ollama]
        url = "http://192.168.1.42:11434"
        model = "custom-model"
        timeout_seconds = 120
        num_predict = 42
        think = true
        """,
    )

    config = load_config(config_path)

    assert config.journal_path == journal_path
    assert config.ollama.url == "http://192.168.1.42:11434"
    assert config.ollama.model == "custom-model"
    assert config.ollama.timeout_seconds == 120.0
    assert config.ollama.num_predict == 42
    assert config.ollama.think is True


def test_journal_path_tilde_is_expanded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    config_path = write_config(tmp_path, 'journal_path = "~/journal"\n')

    config = load_config(config_path)

    assert config.journal_path == fake_home / "journal"
    assert "~" not in str(config.journal_path)


def test_invalid_toml_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, "journal_path = \n")

    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(config_path)


def test_wrong_value_type_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, "journal_path = 42\n")

    with pytest.raises(ConfigError, match="journal_path must be"):
        load_config(config_path)


def test_wrong_ollama_value_type_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
        [ollama]
        think = "yes"
        """,
    )

    with pytest.raises(ConfigError, match="ollama.think must be true or false"):
        load_config(config_path)


def test_non_table_ollama_section_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, 'ollama = "http://localhost"\n')

    with pytest.raises(ConfigError, match="ollama must be a table"):
        load_config(config_path)


@pytest.mark.parametrize("timeout", ["0", "-1", "-0.5"])
def test_non_positive_timeout_is_rejected(
    tmp_path: Path,
    timeout: str,
) -> None:
    config_path = write_config(
        tmp_path,
        f"""
        [ollama]
        timeout_seconds = {timeout}
        """,
    )

    with pytest.raises(ConfigError, match="timeout_seconds must be greater"):
        load_config(config_path)


@pytest.mark.parametrize("num_predict", ["0", "-10"])
def test_non_positive_num_predict_is_rejected(
    tmp_path: Path,
    num_predict: str,
) -> None:
    config_path = write_config(
        tmp_path,
        f"""
        [ollama]
        num_predict = {num_predict}
        """,
    )

    with pytest.raises(ConfigError, match="num_predict must be greater"):
        load_config(config_path)


def test_fractional_num_predict_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
        [ollama]
        num_predict = 12.5
        """,
    )

    with pytest.raises(ConfigError, match="num_predict must be an integer"):
        load_config(config_path)


def test_empty_model_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
        [ollama]
        model = "   "
        """,
    )

    with pytest.raises(ConfigError, match="ollama.model must be"):
        load_config(config_path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, 'jurnal_path = "/tmp/journal"\n')

    with pytest.raises(ConfigError, match="unknown key\\(s\\): jurnal_path"):
        load_config(config_path)


def test_unknown_ollama_key_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        """
        [ollama]
        temperature = 0.5
        """,
    )

    with pytest.raises(ConfigError, match="unknown key\\(s\\): ollama.temp"):
        load_config(config_path)


def test_unreadable_config_file_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.mkdir()

    with pytest.raises(ConfigError, match="Could not read configuration file"):
        load_config(config_path)


def test_apply_overrides_without_values_keeps_config(tmp_path: Path) -> None:
    config = AppConfig(journal_path=tmp_path / "journal")

    assert apply_overrides(config) == config


def test_apply_overrides_replaces_selected_values(tmp_path: Path) -> None:
    config = AppConfig(journal_path=tmp_path / "journal")

    updated = apply_overrides(
        config,
        journal_path=tmp_path / "other",
        ollama_url="http://192.168.1.42:11434",
        model="override-model",
        timeout_seconds=30.0,
        num_predict=25,
        think=True,
    )

    assert updated.journal_path == tmp_path / "other"
    assert updated.ollama.url == "http://192.168.1.42:11434"
    assert updated.ollama.model == "override-model"
    assert updated.ollama.timeout_seconds == 30.0
    assert updated.ollama.num_predict == 25
    assert updated.ollama.think is True
    assert config.ollama.model == DEFAULT_OLLAMA_MODEL


def test_apply_overrides_expands_journal_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    updated = apply_overrides(AppConfig(), journal_path=Path("~/journal"))

    assert updated.journal_path == fake_home / "journal"


@pytest.mark.parametrize("timeout", [0.0, -5.0])
def test_apply_overrides_rejects_non_positive_timeout(timeout: float) -> None:
    with pytest.raises(ConfigError, match="--timeout-seconds must be greater"):
        apply_overrides(AppConfig(), timeout_seconds=timeout)


@pytest.mark.parametrize("num_predict", [0, -3])
def test_apply_overrides_rejects_non_positive_num_predict(
    num_predict: int,
) -> None:
    with pytest.raises(ConfigError, match="--num-predict must be greater"):
        apply_overrides(AppConfig(), num_predict=num_predict)


def test_apply_overrides_rejects_empty_model() -> None:
    with pytest.raises(ConfigError, match="--model must be"):
        apply_overrides(AppConfig(), model="  ")
