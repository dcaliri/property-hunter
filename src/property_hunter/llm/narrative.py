"""Digest narrative generation (US4).

One summary request per notify pass; the narrative opens the digest in plain
Spanish. Empty/error responses fall back to the templated opening (FR-022).
"""

from __future__ import annotations

import logging

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.llm.client import complete

logger = logging.getLogger("property_hunter.llm.narrative")

_SYSTEM = "Eres un asistente inmobiliario que escribe breves resúmenes en español."
_USER = (
    "Escribe un resumen de 2-3 frases, en español, de estas oportunidades "
    "detectadas hoy. Sé concreto y conciso.\n\n{summary}"
)


def build_narrative(settings: Settings, repo: Repository, detections: list,
                    transport=None) -> str:
    """Return a Spanish digest opening, or \"\" when LLM unavailable/failed."""
    if not settings.llm.enabled or not detections:
        return ""
    summary = "; ".join(
        f"detection {d['id']} listing {d['listing_id']} score {d['score']}"
        for d in detections
    )[:3000]
    try:
        raw = complete(
            settings.llm,
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": _USER.format(summary=summary)}],
            transport=transport,
        )
        text = (raw or "").strip()
        return text[:1000]
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm.narrative failed (fail-open)", extra={"ctx_error": str(exc)})
        return ""
