"""Structured description extraction for the valuation model (US2/US4).

One request per sale listing description extracts a small validated JSON feature
set (condition, floor, expensas, amenities, orientation) that is fed into the
ML feature table. Any failure is fail-open: the listing is skipped and the
pipeline continues (FR-022). Values are coerced against fixed vocabularies so
the model never sees free text.
"""

from __future__ import annotations

import json
import logging
import re

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.llm.client import complete
from property_hunter.util import utcnow

logger = logging.getLogger("property_hunter.llm.features")

CONDITIONS = ("a_estrenar", "nuevo", "renovado", "buen_estado", "regular", "a_refaccionar")
ORIENTATIONS = ("N", "S", "E", "O", "NE", "NO", "SE", "SO")
AMENITIES = ("has_parking", "has_pool", "has_gym", "has_terrace", "has_balcony", "has_security")

_CONDITION_ALIASES = {
    "a estrenar": "a_estrenar", "estrenar": "a_estrenar", "estreno": "a_estrenar",
    "nuevo": "nuevo", "nueva": "nuevo", "0km": "nuevo",
    "renovado": "renovado", "renovada": "renovado", "reciclado": "renovado", "remodelado": "renovado",
    "buen estado": "buen_estado", "muy buen estado": "buen_estado", "excelente estado": "buen_estado",
    "regular": "regular", "a mejorar": "regular", "usado": "regular",
    "a refaccionar": "a_refaccionar", "refaccionar": "a_refaccionar", "a reciclar": "a_refaccionar",
    "en obra": "a_refaccionar",
}

_ORIENTATION_ALIASES = {
    "norte": "N", "n": "N", "nor": "N",
    "sur": "S", "s": "S",
    "este": "E", "e": "E",
    "oeste": "O", "o": "O",
    "noreste": "NE", "ne": "NE",
    "noroeste": "NO", "no": "NO",
    "sudeste": "SE", "sureste": "SE", "se": "SE",
    "sudoeste": "SO", "suroeste": "SO", "so": "SO",
}

_SYSTEM = (
    "Eres un tasador inmobiliario argentino. A partir de la descripción de un "
    "aviso, extrae sus características. Devuelve EXCLUSIVAMENTE un objeto JSON "
    "con estas claves y valores permitidos:\n"
    '- "condition": una de "' + '", "'.join(CONDITIONS) + '" (desconocido no es válido, elige la más próxima)\n'
    '- "floor": número entero >= 0 del piso ("PB" o planta baja = 0), o null si no se indica\n'
    '- "expensas": número entero en USD o ARS al mes, o null si no se indica\n'
    '- "orientation": una de "' + '", "'.join(ORIENTATIONS) + '" o null\n'
    '- "has_parking", "has_pool", "has_gym", "has_terrace", "has_balcony", "has_security": true/false (cochera = has_parking, piscina = has_pool, gimnasio = has_gym, sum/terraza = has_terrace, balcón = has_balcony, seguridad/portero = has_security)\n'
    "No agregues claves ni valores fuera de esta lista. Si no hay información para un campo, usa null."
)
_USER_EXAMPLE = (
    "{\"condition\":\"buen_estado\",\"floor\":3,\"expensas\":120000,\"orientation\":\"N\","
    "\"has_parking\":true,\"has_pool\":false,\"has_gym\":true,\"has_terrace\":false,"
    "\"has_balcony\":true,\"has_security\":true}"
)
_USER = (
    "Devuelve solo el JSON, por ejemplo: " + _USER_EXAMPLE
    + "\n\nDescripción:\n{desc}"
)


def _user_message(description: str) -> str:
    return _USER.replace("{desc}", description[:4000])


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"si", "sí", "true", "1", "yes", "s"}
    return False


def _coerce_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if int(value) >= 0 else None
    if isinstance(value, str):
        text = value.strip().lower().replace(".", "").replace(",", "")
        if text in {"pb", "planta baja"}:
            return 0
        match = re.search(r"\d+", text)
        return int(match.group()) if match else None
    return None


def _coerce_condition(value) -> str:
    if isinstance(value, str):
        key = value.strip().lower().replace("_", " ")
        if key in _CONDITION_ALIASES:
            return _CONDITION_ALIASES[key]
        if key.replace(" ", "_") in CONDITIONS:
            return key.replace(" ", "_")
    return "unknown"


def _coerce_orientation(value) -> str | None:
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _ORIENTATION_ALIASES:
            return _ORIENTATION_ALIASES[key]
    return None


def parse_features(raw: str) -> dict:
    """Parse and validate the LLM JSON response into a canonical feature dict."""
    text = raw.strip() if raw else ""
    if text:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    data: dict = {}
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                data = parsed
        except (ValueError, TypeError):
            logger.warning("llm.features unparseable JSON; using unknowns")
    features = {
        "condition": _coerce_condition(data.get("condition")),
        "floor": _coerce_int(data.get("floor")),
        "expensas": _coerce_int(data.get("expensas")),
        "orientation": _coerce_orientation(data.get("orientation")),
    }
    for amenity in AMENITIES:
        features[amenity] = _coerce_bool(data.get(amenity))
    return features


def extract_listing_features(settings: Settings, repo: Repository, transport=None,
                             limit: int | None = None) -> dict:
    """Extract structured features for sale listings missing them (fail-open)."""
    counts = {"enriched": 0, "failed": 0, "requests": 0, "skipped": 0}
    if not settings.llm.enabled:
        counts["skipped"] = 1
        logger.info("llm.skipped: LLM not configured")
        return counts

    rows = list(repo.conn.execute(
        """SELECT id, description FROM listings
           WHERE operation='sale' AND description IS NOT NULL AND TRIM(description) <> ''
             AND llm_features_updated_at IS NULL
           ORDER BY id"""
    ))
    if limit is not None:
        rows = rows[:limit]
    for row in rows:
        counts["requests"] += 1
        try:
            raw = complete(
                settings.llm,
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user", "content": _user_message(row["description"])}],
                transport=transport,
                json_mode=True,
            )
            features = parse_features(raw)
            repo.conn.execute(
                "UPDATE listings SET llm_features=?, llm_features_updated_at=? WHERE id=?",
                (json.dumps(features), utcnow(), row["id"]),
            )
            counts["enriched"] += 1
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            logger.warning("llm.features failed (fail-open)", extra={
                "ctx_listing_id": row["id"], "ctx_error": str(exc)})
        if counts["requests"] % 25 == 0:
            repo.conn.commit()
    repo.conn.commit()
    return counts
