from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.request import Request

import pytest

from journal_ai.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)


def make_client() -> OllamaClient:
    return OllamaClient(
        base_url="http://localhost:11434",
        timeout_seconds=900.0,
    )


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


@patch("journal_ai.ollama_client.urlopen")
def test_generate_payload_uses_supplied_settings(
    mock_urlopen: MagicMock,
) -> None:
    mock_urlopen.return_value = make_mock_response(
        {
            "model": "test-model",
            "response": "A useful response.",
        }
    )

    client = make_client()
    client.generate(
        model="test-model",
        prompt="Analyze this.",
        num_predict=42,
        think=True,
    )

    payload = request_payload(mock_urlopen)

    assert payload["stream"] is False
    assert payload["think"] is True
    assert payload["options"] == {"num_predict": 42}


@patch("journal_ai.ollama_client.urlopen")
def test_generate_payload_can_disable_thinking(
    mock_urlopen: MagicMock,
) -> None:
    mock_urlopen.return_value = make_mock_response(
        {
            "model": "test-model",
            "response": "A useful response.",
        }
    )

    client = make_client()
    client.generate(
        model="test-model",
        prompt="Analyze this.",
        num_predict=300,
        think=False,
    )

    payload = request_payload(mock_urlopen)

    assert payload["think"] is False
    assert payload["options"] == {"num_predict": 300}


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

    client = make_client()
    response = client.generate(
        model="test-model",
        prompt="Analyze this.",
        num_predict=300,
        think=False,
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

    client = make_client()

    with pytest.raises(
        OllamaConnectionError,
        match="Could not connect",
    ):
        client.generate(
            model="test-model",
            prompt="Analyze this.",
            num_predict=300,
            think=False,
        )


@patch("journal_ai.ollama_client.urlopen")
def test_missing_response_text_is_rejected(
    mock_urlopen: MagicMock,
) -> None:
    mock_urlopen.return_value = make_mock_response(
        {"model": "test-model"}
    )

    client = make_client()

    with pytest.raises(
        OllamaResponseError,
        match="generated text",
    ):
        client.generate(
            model="test-model",
            prompt="Analyze this.",
            num_predict=300,
            think=False,
        )


@patch("journal_ai.ollama_client.urlopen")
def test_empty_response_is_rejected(
    mock_urlopen: MagicMock,
) -> None:
    mock_urlopen.return_value = make_mock_response(
        {
            "model": "test-model",
            "response": "",
        }
    )

    client = make_client()

    with pytest.raises(
        OllamaResponseError,
        match="empty or whitespace-only",
    ):
        client.generate(
            model="test-model",
            prompt="Analyze this.",
            num_predict=300,
            think=False,
        )


@patch("journal_ai.ollama_client.urlopen")
def test_whitespace_only_response_is_rejected(
    mock_urlopen: MagicMock,
) -> None:
    mock_urlopen.return_value = make_mock_response(
        {
            "model": "test-model",
            "response": "   \n\t  ",
        }
    )

    client = make_client()

    with pytest.raises(
        OllamaResponseError,
        match="empty or whitespace-only",
    ):
        client.generate(
            model="test-model",
            prompt="Analyze this.",
            num_predict=300,
            think=False,
        )
