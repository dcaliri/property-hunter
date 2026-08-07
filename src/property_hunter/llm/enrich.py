"""Description enrichment for listings (US4).

One request per newly-added description; tags are validated against a fixed
vocabulary and persisted on the listing. Any failure is fail-open: the
description is left un-tagged and the pipeline continues (FR-022).
"""

from __future__ import annotations

import json
import logging
import re

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.llm.client import complete
from property_hunter.util import utcnow

logger = logging.getLogger("property_hunter.llm.enrich")

TAG_VOCABULARY = frozenset({
    "a estrenar", "aire acondicionado", "amenities", "balcón", "balcon",
    "calefacción", "cochera", "cocina equipada", "gimnasio", "lavadero",
    "living comedor", "parrilla", "piscina", "portero", "renovado",
    "seguridad 24hs", "sum", "terraza",
})

_SYSTEM = (
    "Eres un agente inmobiliario. A partir de la descripción de un aviso, "
    "devuelve exclusivamente una lista JSON de etiquetas, eligiendo únicamente "
    "entre estas opciones: " + ", ".join(sorted(TAG_VOCABULARY))
)
_USER = (
    "Devuelve solo el JSON, por ejemplo [\"balcón\", \"parrilla\"]. Si ninguna "
    "etiqueta aplica, devuelve [].\n\nDescripción:\n{desc}"
)


def parse_tags(raw: str) -> list[str]:
    """Extract and validate tags from an LLM response against the vocabulary."""
    if not raw:
        return []
    text = raw.strip()
    try:
        values = json.loads(text)
        if not isinstance(values, list):
            values = re.split(r"[,\n;]+", text)
    except (ValueError, TypeError):
        values = re.split(r"[,\n;]+", text)
    tags = []
    for value in values:
        tag = str(value).strip().strip("[]\"' ").lower()
        if tag in TAG_VOCABULARY and tag not in tags:
            tags.append(tag)
    return tags


def enrich_descriptions(settings: Settings, repo: Repository, transport=None) -> dict:
    """Enrich new listing descriptions with amenity/condition tags."""
    counts = {"enriched": 0, "failed": 0, "requests": 0, "skipped": 0}
    if not settings.llm.enabled:
        counts["skipped"] = 1
        logger.info("llm.skipped: LLM not configured")
        return counts

    rows = list(repo.conn.execute(
        """SELECT id, description FROM listings
           WHERE is_active=1 AND description IS NOT NULL AND TRIM(description) <> ''
             AND llm_tags_updated_at IS NULL"""
    ))
    for row in rows:
        counts["requests"] += 1
        try:
            raw = complete(
                settings.llm,
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user", "content": _USER.format(desc=row["description"][:4000])}],
                transport=transport,
            )
            tags = parse_tags(raw)
            repo.conn.execute(
                "UPDATE listings SET llm_amenity_tags=?, llm_tags_updated_at=? WHERE id=?",
                (json.dumps(tags) if tags else None, utcnow(), row["id"]),
            )
            counts["enriched"] += 1
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            logger.warning("llm.enrich failed (fail-open)", extra={
                "ctx_listing_id": row["id"], "ctx_error": str(exc)})
    repo.conn.commit()
    return counts
