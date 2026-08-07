"""Contract test: status.sh --json output must validate against
specs/002-cloud-provision-deploy/contracts/status-output-v1.schema.json.

Uses a fixture because the live command needs a real AWS account (quickstart
scenarios). The fixture mirrors the exact shape emit_status_json produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

SCHEMA = json.loads(
    Path("specs/002-cloud-provision-deploy/contracts/status-output-v1.schema.json").read_text()
)
FIXTURE = json.loads(Path("tests/fixtures/cloud/status-output-v1.json").read_text())


def _status(**overrides) -> dict:
    data = json.loads(Path("tests/fixtures/cloud/status-output-v1.json").read_text())
    data.update(overrides)
    return data


def test_fixture_validates():
    jsonschema.validate(FIXTURE, SCHEMA)


def test_deprovisioned_environment_null_validates():
    # environment: null is valid (status.sh when nothing is provisioned)
    data = _status()
    data["environment"] = None
    jsonschema.validate(data, SCHEMA)


def test_cost_over_budget_validates():
    data = _status()
    data["cost"]["estimated_usd_month"] = 7.73
    data["cost"]["over_budget"] = True
    jsonschema.validate(data, SCHEMA)


def test_missing_required_field_rejected():
    import pytest

    data = _status()
    del data["retained"]["data_volume_id"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)


def test_bad_environment_state_rejected():
    import pytest

    data = _status()
    data["environment"]["state"] = "sideways"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
