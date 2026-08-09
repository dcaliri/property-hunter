"""Minimal OpenAI-compatible chat client for optional LLM features (US4).

Fail-open by design: callers treat any exception/timeout as "no result" and
continue the pipeline (FR-022). Transport is injectable for tests.
"""

from __future__ import annotations

import logging

import httpx

from property_hunter.config import LLMConfig

logger = logging.getLogger("property_hunter.llm")

DEFAULT_SYSTEM = (
    "Eres un asistente de análisis inmobiliario. Responde únicamente con el "
    "formato solicitado, en español."
)


class LLMError(Exception):
    """Raised for any failure talking to the LLM endpoint."""


def complete(llm: LLMConfig, messages: list[dict], transport=None,
             timeout_seconds: float | None = None, json_mode: bool = False) -> str:
    """POST to the chat completions endpoint and return the message content.

    ``json_mode`` requests structured JSON output via ``response_format`` (an
    OpenAI convention honored by OpenAI, Ollama, vLLM, LM Studio, etc.). Callers
    whose prompt asks for JSON should opt in so unparseable responses vanish.
    """
    url = llm.base_url.rstrip("/") + "/chat/completions"
    timeout = timeout_seconds if timeout_seconds is not None else llm.timeout_seconds
    if transport is None:
        transport = httpx.Client(timeout=timeout)
    payload: dict = {"model": llm.model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = transport.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {llm.api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise LLMError(str(exc)) from exc
