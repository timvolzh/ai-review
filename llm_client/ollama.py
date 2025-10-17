"""Client utilities for interacting with Ollama."""
from __future__ import annotations

from typing import Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class OllamaError(RuntimeError):
    """Raised when Ollama responds with an error."""


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type(requests.RequestException),
)
def ollama_generate(
    prompt: str,
    *,
    base_url: str,
    model: str,
    temperature: float = 0.2,
    system: Optional[str] = None,
) -> str:
    """Request a completion from Ollama.

    Args:
        prompt: Prompt text for the LLM.
        base_url: Base URL of the Ollama service.
        model: Model identifier to use for the request.
        temperature: Sampling temperature.
        system: Optional system prompt.

    Returns:
        The trimmed response text returned by Ollama.

    Raises:
        OllamaError: If the HTTP response indicates an error.
    """

    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system
    resp = requests.post(url, json=payload, timeout=120, verify=False)
    if resp.status_code >= 400:
        raise OllamaError(f"Ollama error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data.get("response", "").strip()
