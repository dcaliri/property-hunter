"""Feature engineering for the valuation model (US2).

One-hot encoding for barrio and property_type plus numeric fields. Nulls are
coerced to 0.0 so the model matrix is dense; barrio/type vocabularies are
fixed from the training data and must be reused verbatim at predict time.
"""

from __future__ import annotations

from datetime import datetime, timezone

NUMERIC_COLUMNS = ("beds", "baths", "covered_area_m2", "total_area_m2", "age_days")


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


def feature_names(barrio_values, type_values) -> list[str]:
    barrios = sorted(barrio_values)
    types = sorted(t for t in type_values if t)
    return (list(NUMERIC_COLUMNS)
            + [f"barrio::{b}" for b in barrios]
            + [f"type::{t}" for t in types])


def feature_vector(row, barrio_values, type_values) -> list[float]:
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
    return vec
