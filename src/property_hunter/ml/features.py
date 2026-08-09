"""Feature engineering for the valuation model (US2).

One-hot encoding for barrio, property_type, condition and orientation plus
numeric fields. Nulls are coerced to 0.0 so the model matrix is dense;
barrio/type vocabularies are fixed from the training data and must be reused
verbatim at predict time. LLM-derived features (condition, floor, expensas,
amenities) come from ``llm_features`` JSON and are appended last so older
persisted bundles can still be served by truncating (see ml.predict).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

NUMERIC_COLUMNS = ("beds", "baths", "covered_area_m2", "total_area_m2", "age_days")

CONDITION_VALUES = ("a_estrenar", "nuevo", "renovado", "buen_estado", "regular", "a_refaccionar")
ORIENTATION_VALUES = ("N", "S", "E", "O", "NE", "NO", "SE", "SO")
AMENITY_VALUES = ("has_parking", "has_pool", "has_gym", "has_terrace", "has_balcony", "has_security")
LLM_NUMERIC = ("floor", "expensas")


def _age_days(row) -> float:
    date_posted = row["date_posted"]
    if not date_posted:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(date_posted))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def _llm_features(row) -> dict:
    raw = row["llm_features"]
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _llm_names() -> list[str]:
    return ([f"condition::{c}" for c in CONDITION_VALUES]
            + [f"orientation::{o}" for o in ORIENTATION_VALUES]
            + list(LLM_NUMERIC)
            + list(AMENITY_VALUES))


def _llm_vector(row) -> list[float]:
    features = _llm_features(row)
    vec: list[float] = []
    condition = features.get("condition")
    vec.extend(1.0 if condition == c else 0.0 for c in CONDITION_VALUES)
    orientation = features.get("orientation")
    vec.extend(1.0 if orientation == o else 0.0 for o in ORIENTATION_VALUES)
    for col in LLM_NUMERIC:
        value = features.get(col)
        vec.append(float(value) if isinstance(value, (int, float)) else 0.0)
    for amenity in AMENITY_VALUES:
        vec.append(1.0 if features.get(amenity) else 0.0)
    return vec


def feature_names(barrio_values, type_values, include_llm_features: bool = True) -> list[str]:
    barrios = sorted(barrio_values)
    types = sorted(t for t in type_values if t)
    names = (list(NUMERIC_COLUMNS)
             + [f"barrio::{b}" for b in barrios]
             + [f"type::{t}" for t in types])
    if include_llm_features:
        names += _llm_names()
    return names


def feature_vector(row, barrio_values, type_values, include_llm_features: bool = True) -> list[float]:
    vec: list[float] = []
    for col in NUMERIC_COLUMNS:
        value = _age_days(row) if col == "age_days" else row[col]
        vec.append(float(value) if value is not None else 0.0)
    barrios = sorted(barrio_values)
    types = sorted(t for t in type_values if t)
    barrio = row["barrio"]
    ptype = row["property_type"]
    vec.extend(1.0 if barrio == b else 0.0 for b in barrios)
    vec.extend(1.0 if ptype == t else 0.0 for t in types)
    if include_llm_features:
        vec += _llm_vector(row)
    return vec
