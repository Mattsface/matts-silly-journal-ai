from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(Exception):
    """Base exception for Ollama communication errors."""


class OllamaConnectionError(OllamaError):
    """Raised when the local Ollama service cannot be reached."""


class OllamaResponseError(OllamaError):
    """Raised when Ollama returns an invalid or unsuccessful response."""


@dataclass(frozen=True, slots=True)
class OllamaResponse:
    """A completed response from the Ollama generate endpoint."""

    text: str
    model: str
    prompt_tokens: int | None = None
    response_tokens: int | None = None


class OllamaClient:
    """Minimal client for Ollama's local HTTP API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 900.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None = None,
    ) -> OllamaResponse:
        """Generate one non-streaming response from Ollama."""
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "num_predict": 300,
            },
        }

        if system is not None:
            payload["system"] = system

        request = Request(
            url=f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                response_data = response.read()
        except HTTPError as exc:
            error_message = self._read_http_error(exc)
            raise OllamaResponseError(
                f"Ollama returned HTTP {exc.code}: {error_message}"
            ) from exc
        except URLError as exc:
            raise OllamaConnectionError(
                f"Could not connect to Ollama at {self.base_url}"
            ) from exc
        except TimeoutError as exc:
            raise OllamaConnectionError(
                f"Ollama request timed out after {self.timeout_seconds} seconds"
            ) from exc

        try:
            parsed = json.loads(response_data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OllamaResponseError(
                "Ollama returned invalid JSON"
            ) from exc

        if not isinstance(parsed, dict):
            raise OllamaResponseError(
                "Ollama returned an unexpected response"
            )

        error = parsed.get("error")
        if isinstance(error, str):
            raise OllamaResponseError(f"Ollama error: {error}")

        text = parsed.get("response")
        returned_model = parsed.get("model")

        if not isinstance(text, str):
            raise OllamaResponseError(
                "Ollama response did not contain generated text"
            )

        stripped_text = text.strip()
        if not stripped_text:
            raise OllamaResponseError(
                "Ollama returned an empty or whitespace-only response"
            )

        if not isinstance(returned_model, str):
            returned_model = model

        return OllamaResponse(
            text=stripped_text,
            model=returned_model,
            prompt_tokens=self._optional_int(
                parsed.get("prompt_eval_count")
            ),
            response_tokens=self._optional_int(
                parsed.get("eval_count")
            ),
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return value if isinstance(value, int) else None

    @staticmethod
    def _read_http_error(exc: HTTPError) -> str:
        try:
            response_data = exc.read()
            parsed = json.loads(response_data)

            if isinstance(parsed, dict):
                error = parsed.get("error")
                if isinstance(error, str):
                    return error
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

        return exc.reason or "Unknown error"
