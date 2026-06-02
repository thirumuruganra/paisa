from __future__ import annotations

from collections.abc import Sequence
import os

import httpx


_OLLAMA_CHAT_PATH = "/api/chat"
_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_BASE_URL_ENV_VAR = "OLLAMA_BASE_URL"


def _normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise ValueError("Ollama base URL cannot be empty.")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    if value.endswith(_OLLAMA_CHAT_PATH):
        value = value[: -len(_OLLAMA_CHAT_PATH)]
    return value.rstrip("/")


def _configured_base_url(explicit_base_url: str | None) -> str | None:
    if explicit_base_url is not None:
        return explicit_base_url
    return os.environ.get(_BASE_URL_ENV_VAR)


def _candidate_base_urls(explicit_base_url: str | None) -> tuple[str, ...]:
    configured_base_url = _configured_base_url(explicit_base_url)
    if configured_base_url is not None:
        return (_normalize_base_url(configured_base_url),)
    return (_DEFAULT_OLLAMA_BASE_URL,)


def _unavailable_message(base_urls: Sequence[str]) -> str:
    primary_base_url = base_urls[0]
    return (
        f"Ollama unavailable at {primary_base_url}. "
        "Start Ollama locally or set OLLAMA_BASE_URL in your environment or .env file and retry."
    )


class OllamaUnavailableError(RuntimeError):
    pass


class OllamaRequestError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str = "llama3.2:3b",
        timeout: float = 120.0,
    ) -> None:
        self.base_urls = _candidate_base_urls(base_url)
        self.base_url = self.base_urls[0]
        self.model = model
        self.timeout = timeout

    async def chat(self, messages: Sequence[dict[str, str]], *, model: str | None = None) -> str:
        payload = {
            "model": model or self.model,
            "messages": list(messages),
            "stream": False,
        }

        last_request_error: httpx.RequestError | None = None
        try:
            for base_url in self.base_urls:
                try:
                    async with httpx.AsyncClient(base_url=base_url, timeout=self.timeout) as client:
                        response = await client.post(_OLLAMA_CHAT_PATH, json=payload)
                        response.raise_for_status()
                    self.base_url = base_url
                    break
                except httpx.TimeoutException as exc:
                    raise OllamaRequestError(
                        f"Ollama request timed out after {self.timeout:.0f}s at {base_url}. "
                        "Model may still be loading; retry or use a smaller model."
                    ) from exc
                except httpx.RequestError as exc:
                    last_request_error = exc
            else:
                raise OllamaUnavailableError(_unavailable_message(self.base_urls)) from last_request_error
        except httpx.HTTPStatusError as exc:
            raise OllamaRequestError(f"Ollama request failed: HTTP {exc.response.status_code}") from exc

        data = response.json()
        message = data.get("message") or {}
        content = str(message.get("content", "")).strip()
        if not content:
            raise OllamaRequestError("Ollama returned an empty response.")
        return content