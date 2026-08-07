"""Contract test: ListingRecord must validate against contracts/listing-v1.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from property_hunter.models import ListingRecord

SCHEMA = json.loads(Path("specs/001-opportunity-hunter/contracts/listing-v1.schema.json").read_text())


def _record(**overrides) -> ListingRecord:
    base = dict(
        source="inmoup",
        source_listing_id=326,
        source_url="https://inmoup.com.ar/agency/inmuebles/326/ficha/x",
        operation="sale",
        property_type="departamento",
        barrio="Palermo",
        region="Capital Federal",
        beds=1,
        baths=1,
        covered_area_m2=60.0,
        total_area_m2=60.0,
        agency_name="AGENCY",
        description="Desc",
        date_posted="2026-01-01",
        price_cents=15860000,
        currency="USD",
        observed_at="2026-08-06T12:00:00+00:00",
    )
    base.update(overrides)
    return ListingRecord(**base)


def test_listing_record_validates():
    jsonschema.validate(_record().model_dump(), SCHEMA)


def test_listing_record_with_llm_tags_validates():
    jsonschema.validate(_record(llm_amenity_tags=["balcón", "parrilla"]).model_dump(), SCHEMA)


def test_invalid_operation_rejected():
    import pytest

    data = _record().model_dump()
    data["operation"] = "for-sale"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
