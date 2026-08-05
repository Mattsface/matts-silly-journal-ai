from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_LOCATION = "~/.config/journal-ai/config.toml"
DEFAULT_JOURNAL_LOCATION = "~/journal"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_NUM_PREDICT = 300
DEFAULT_THINK = False

DEFAULT_CHUNK_TARGET_CHARACTERS = 1000
DEFAULT_CHUNK_MAX_CHARACTERS = 1600
DEFAULT_CHUNK_MINIMUM_CHARACTERS = 250
DEFAULT_CHUNK_OVERLAP_CHARACTERS = 150

INDEX_STATE_DIR_NAME = ".journal-ai"
INDEX_DATABASE_FILENAME = "index.sqlite"

TOP_LEVEL_KEYS = frozenset({"journal_path", "ollama", "chunking"})
OLLAMA_KEYS = frozenset(
    {"url", "model", "timeout_seconds", "num_predict", "think"}
)
CHUNKING_KEYS = frozenset(
    {
        "target_characters",
        "max_characters",
        "minimum_characters",
        "overlap_characters",
    }
)


class ConfigError(Exception):
    """Raised when the configuration file is unreadable or invalid."""


def default_config_path() -> Path:
    """Return the default configuration file location."""
    return Path(DEFAULT_CONFIG_LOCATION).expanduser()


def default_journal_path() -> Path:
    """Return the default mounted journal location."""
    return Path(DEFAULT_JOURNAL_LOCATION).expanduser()


def index_database_path(journal_path: Path) -> Path:
    """Derive the index database location from the mounted journal path.

    The index is disposable application state that describes exactly one
    journal, so it is derived from journal_path instead of being a separate
    configuration setting. Keeping it under INDEX_STATE_DIR_NAME also keeps
    it inside the encrypted mount and outside source discovery.
    """
    return (
        journal_path.expanduser()
        / INDEX_STATE_DIR_NAME
        / INDEX_DATABASE_FILENAME
    )


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Settings for deterministic Markdown chunking during indexing."""

    target_characters: int = DEFAULT_CHUNK_TARGET_CHARACTERS
    max_characters: int = DEFAULT_CHUNK_MAX_CHARACTERS
    minimum_characters: int = DEFAULT_CHUNK_MINIMUM_CHARACTERS
    overlap_characters: int = DEFAULT_CHUNK_OVERLAP_CHARACTERS

    def __post_init__(self) -> None:
        if self.target_characters <= 0:
            raise ConfigError("chunking.target_characters must be greater than zero")
        if self.max_characters <= 0:
            raise ConfigError("chunking.max_characters must be greater than zero")
        if self.minimum_characters < 0:
            raise ConfigError(
                "chunking.minimum_characters must be zero or greater"
            )
        if self.overlap_characters < 0:
            raise ConfigError("chunking.overlap_characters must be zero or greater")
        if self.target_characters > self.max_characters:
            raise ConfigError(
                "chunking.target_characters must not exceed chunking.max_characters"
            )
        if self.minimum_characters > self.max_characters:
            raise ConfigError(
                "chunking.minimum_characters must not exceed chunking.max_characters"
            )
        if self.overlap_characters >= self.max_characters:
            raise ConfigError(
                "chunking.overlap_characters must be smaller than "
                "chunking.max_characters"
            )


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    """Settings for one Ollama server and its generation requests."""

    url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_OLLAMA_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    num_predict: int = DEFAULT_NUM_PREDICT
    think: bool = DEFAULT_THINK


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Resolved application settings."""

    journal_path: Path = field(default_factory=default_journal_path)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration from TOML, falling back to built-in defaults."""
    path = (config_path or default_config_path()).expanduser()

    if not path.exists():
        return AppConfig()

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(
            f"Could not read configuration file: {path}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"Configuration file is not valid UTF-8: {path}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"Invalid TOML in configuration file {path}: {exc}"
        ) from exc

    return config_from_mapping(data, source=path)


def config_from_mapping(
    data: Mapping[str, Any],
    *,
    source: Path | None = None,
) -> AppConfig:
    """Merge parsed TOML values with the built-in defaults."""
    _reject_unknown_keys(data, allowed=TOP_LEVEL_KEYS, source=source)

    journal_path = default_journal_path()
    if "journal_path" in data:
        journal_path = _as_path(
            data["journal_path"],
            key="journal_path",
            source=source,
        )

    ollama_data = data.get("ollama", {})
    if not isinstance(ollama_data, Mapping):
        raise _config_error("ollama must be a table", source)

    _reject_unknown_keys(
        ollama_data,
        allowed=OLLAMA_KEYS,
        source=source,
        section="ollama",
    )

    defaults = OllamaConfig()
    ollama = OllamaConfig(
        url=_optional_str(ollama_data, "url", defaults.url, source),
        model=_optional_str(ollama_data, "model", defaults.model, source),
        timeout_seconds=(
            _as_positive_float(
                ollama_data["timeout_seconds"],
                key="ollama.timeout_seconds",
                source=source,
            )
            if "timeout_seconds" in ollama_data
            else defaults.timeout_seconds
        ),
        num_predict=(
            _as_positive_int(
                ollama_data["num_predict"],
                key="ollama.num_predict",
                source=source,
            )
            if "num_predict" in ollama_data
            else defaults.num_predict
        ),
        think=(
            _as_bool(ollama_data["think"], key="ollama.think", source=source)
            if "think" in ollama_data
            else defaults.think
        ),
    )

    chunking_data = data.get("chunking", {})
    if not isinstance(chunking_data, Mapping):
        raise _config_error("chunking must be a table", source)

    _reject_unknown_keys(
        chunking_data,
        allowed=CHUNKING_KEYS,
        source=source,
        section="chunking",
    )

    chunking_defaults = ChunkingConfig()
    chunking = ChunkingConfig(
        target_characters=(
            _as_positive_int(
                chunking_data["target_characters"],
                key="chunking.target_characters",
                source=source,
            )
            if "target_characters" in chunking_data
            else chunking_defaults.target_characters
        ),
        max_characters=(
            _as_positive_int(
                chunking_data["max_characters"],
                key="chunking.max_characters",
                source=source,
            )
            if "max_characters" in chunking_data
            else chunking_defaults.max_characters
        ),
        minimum_characters=(
            _as_non_negative_int(
                chunking_data["minimum_characters"],
                key="chunking.minimum_characters",
                source=source,
            )
            if "minimum_characters" in chunking_data
            else chunking_defaults.minimum_characters
        ),
        overlap_characters=(
            _as_non_negative_int(
                chunking_data["overlap_characters"],
                key="chunking.overlap_characters",
                source=source,
            )
            if "overlap_characters" in chunking_data
            else chunking_defaults.overlap_characters
        ),
    )

    return AppConfig(journal_path=journal_path, ollama=ollama, chunking=chunking)


def apply_overrides(
    config: AppConfig,
    *,
    journal_path: Path | None = None,
    ollama_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
    num_predict: int | None = None,
    think: bool | None = None,
) -> AppConfig:
    """Return a copy of config with explicitly supplied values applied."""
    ollama = OllamaConfig(
        url=(
            config.ollama.url
            if ollama_url is None
            else _as_str(ollama_url, key="--ollama-url", source=None)
        ),
        model=(
            config.ollama.model
            if model is None
            else _as_str(model, key="--model", source=None)
        ),
        timeout_seconds=(
            config.ollama.timeout_seconds
            if timeout_seconds is None
            else _as_positive_float(
                timeout_seconds,
                key="--timeout-seconds",
                source=None,
            )
        ),
        num_predict=(
            config.ollama.num_predict
            if num_predict is None
            else _as_positive_int(
                num_predict,
                key="--num-predict",
                source=None,
            )
        ),
        think=config.ollama.think if think is None else think,
    )

    return AppConfig(
        journal_path=(
            config.journal_path
            if journal_path is None
            else Path(journal_path).expanduser()
        ),
        ollama=ollama,
        chunking=config.chunking,
    )


def _config_error(message: str, source: Path | None) -> ConfigError:
    if source is None:
        return ConfigError(f"Invalid configuration: {message}")

    return ConfigError(f"Invalid configuration in {source}: {message}")


def _reject_unknown_keys(
    data: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    source: Path | None,
    section: str | None = None,
) -> None:
    unknown = sorted(set(data) - allowed)
    if not unknown:
        return

    prefix = f"{section}." if section else ""
    named = ", ".join(f"{prefix}{key}" for key in unknown)
    raise _config_error(f"unknown key(s): {named}", source)


def _optional_str(
    data: Mapping[str, Any],
    key: str,
    fallback: str,
    source: Path | None,
) -> str:
    if key not in data:
        return fallback

    return _as_str(data[key], key=f"ollama.{key}", source=source)


def _as_str(value: object, *, key: str, source: Path | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _config_error(f"{key} must be a non-empty string", source)

    return value


def _as_path(value: object, *, key: str, source: Path | None) -> Path:
    return Path(_as_str(value, key=key, source=source)).expanduser()


def _as_positive_float(
    value: object,
    *,
    key: str,
    source: Path | None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _config_error(f"{key} must be a number", source)

    number = float(value)
    if number <= 0:
        raise _config_error(f"{key} must be greater than zero", source)

    return number


def _as_positive_int(value: object, *, key: str, source: Path | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _config_error(f"{key} must be an integer", source)

    if value <= 0:
        raise _config_error(f"{key} must be greater than zero", source)

    return value


def _as_non_negative_int(value: object, *, key: str, source: Path | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _config_error(f"{key} must be an integer", source)

    if value < 0:
        raise _config_error(f"{key} must be zero or greater", source)

    return value


def _as_bool(value: object, *, key: str, source: Path | None) -> bool:
    if not isinstance(value, bool):
        raise _config_error(f"{key} must be true or false", source)

    return value
