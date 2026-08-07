"""Contract test: DetectionRecord must validate against contracts/detection-v1.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from property_hunter.models import DetectionRecord, Signal

SCHEMA = json.loads(Path("specs/001-opportunity-hunter/contracts/detection-v1.schema.json").read_text())


def _record(**overrides) -> DetectionRecord:
    base = dict(
        listing_id=11,
        run_id=4,
        baseline_id=2,
        prediction_id=9,
        signals=[
            Signal(
                type="undervaluation",
                threshold=0.10,
                observed=8_500_000,
                expected=10_000_000,
                satisfied=True,
                model_version_id=1,
                is_fallback=False,
            ),
            Signal(
                type="price_drop",
                threshold=0.05,
                observed=0.07,
                expected=0.05,
                satisfied=True,
            ),
        ],
        score=0.67,
        status="active",
        first_seen_at="2026-08-06T12:00:00+00:00",
        last_seen_at="2026-08-06T12:00:00+00:00",
        created_at="2026-08-06T12:00:00+00:00",
    )
    base.update(overrides)
    return DetectionRecord(**base)


def test_detection_record_validates():
    jsonschema.validate(_record().model_dump(), SCHEMA)


def test_detection_without_prediction_validates():
    jsonschema.validate(
        _record(prediction_id=None, signals=[_record().signals[1]]).model_dump(),
        SCHEMA,
    )


def test_fallback_signal_validates():
    jsonschema.validate(_record(signals=[_record().signals[0]]).model_dump(), SCHEMA)


def test_invalid_signal_type_rejected():
    import pytest

    data = _record().model_dump()
    data["signals"][0]["type"] = "bargain"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
