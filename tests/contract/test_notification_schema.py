"""Contract test: NotificationRecord must validate against contracts/notification-v1.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from property_hunter.models import NotificationRecord

SCHEMA = json.loads(Path("specs/001-opportunity-hunter/contracts/notification-v1.schema.json").read_text())


def _record(**overrides) -> NotificationRecord:
    base = dict(
        detection_id=5,
        run_id=6,
        channel="email",
        recipient="me@example.com",
        status="sent",
        attempt_count=2,
        last_error=None,
        sent_at="2026-08-06T12:00:00+00:00",
        created_at="2026-08-06T12:00:00+00:00",
        digest_narrative="Hoy detectamos 2 oportunidades en Palermo.",
        llm_enriched=True,
    )
    base.update(overrides)
    return NotificationRecord(**base)


def test_notification_record_validates():
    jsonschema.validate(_record().model_dump(), SCHEMA)


def test_plain_digest_validates():
    jsonschema.validate(
        _record(digest_narrative=None, llm_enriched=False, status="pending").model_dump(),
        SCHEMA,
    )


def test_failed_notification_validates():
    jsonschema.validate(
        _record(status="failed", last_error="SMTP connection refused", sent_at=None).model_dump(),
        SCHEMA,
    )
