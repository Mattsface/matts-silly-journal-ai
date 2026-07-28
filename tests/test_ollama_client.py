from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from journal_ai.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)


def make_mock_response(data: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(data).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


@patch("journal_ai.ollama_client.urlopen")
def test_generate_returns_response(mock_urlopen: MagicMock) -> None:
    mock_urlopen.return_value = make_mock_response(
        {
            "model": "test-model",
            "response": "A useful response.",
            "prompt_eval_count": 20,
            "eval_count": 8,
        }
    )

    client = OllamaClient()
    response = client.generate(
        model="test-model",
        prompt="Analyze this.",
    )

    assert response.text == "A useful response."
    assert response.model == "test-model"
    assert response.prompt_tokens == 20
    assert response.response_tokens == 8


@patch("journal_ai.ollama_client.urlopen")
def test_connection_error_is_wrapped(
    mock_urlopen: MagicMock,
) -> None:
    mock_urlopen.side_effect = URLError("Connection refused")

    client = OllamaClient()

    with pytest.raises(
        OllamaConnectionError,
        match="Could not connect",
    ):
        client.generate(
            model="test-model",
            prompt="Analyze this.",
        )


@patch("journal_ai.ollama_client.urlopen")
def test_missing_response_text_is_rejected(
    mock_urlopen: MagicMock,
) -> None:
    mock_urlopen.return_value = make_mock_response(
        {"model": "test-model"}
    )

    client = OllamaClient()

    with pytest.raises(
        OllamaResponseError,
        match="generated text",
    ):
        client.generate(
            model="test-model",
            prompt="Analyze this.",
        )
