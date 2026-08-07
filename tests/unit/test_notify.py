"""Tests for the email notify stage (US4)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from property_hunter.config import Settings
from property_hunter.db import Repository, connect
from property_hunter.detect import run_detect
from property_hunter.analyze import run_analyze
from property_hunter.models import DetectionRecord, ListingRecord, PredictionRecord, PriceObservation, Signal
from property_hunter.notify.email import build_digest, run_notify, SMTPError
from property_hunter.util import utcnow

_counter = [100_000]


def _observed_at(days_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_settings(db_path: Path) -> Settings:
    s = Settings.from_env()
    s.db_path = db_path
    s.baselines.min_observations_per_zone = 1
    s.smtp.recipient = "me@example.com"
    s.smtp.sender = "prop-hunter@example.com"
    s.notify.max_attempts = 3
    s.notify.retry_backoff_base_seconds = 0.001
    return s


def _repo(db_path: Path) -> Repository:
    return Repository(connect(db_path))


def _seed_detection(repo: Repository, db_path: Path, *, barrio: str = "Palermo",
                    price_cents: int = 8_500_000) -> int:
    _counter[0] += 1
    sid = _counter[0]
    observed_at = _observed_at()
    run_id = repo.create_run()
    lid = repo.upsert_listing(ListingRecord(
        source="inmoup", source_listing_id=sid,
        source_url=f"https://inmoup.com.ar/inmuebles/{sid}",
        operation="sale", property_type="departamento",
        street_address="Av. Siempre Viva 742", barrio=barrio, region="Comuna 14",
        beds=2, covered_area_m2=50.0, price_cents=price_cents, observed_at=observed_at,
    ))
    repo.insert_observation(PriceObservation(
        run_id=run_id, listing_id=lid, price_cents=price_cents, observed_at=observed_at,
    ))
    repo.finish_run(run_id, "ok", observed_at)
    run_analyze(_make_settings(db_path), repo)

    pred_run = repo.create_run(trigger="predict")
    repo.insert_prediction(PredictionRecord(
        listing_id=lid, model_version_id=None, run_id=pred_run,
        predicted_price_cents=10_000_000, is_fallback=True, predicted_at=observed_at,
    ))
    repo.finish_run(pred_run, "ok", observed_at)

    det_run = repo.create_run(trigger="detect")
    repo.insert_detection(DetectionRecord(
        listing_id=lid, run_id=det_run,
        prediction_id=repo.prediction_for(lid)["id"],
        signals=[Signal(type="undervaluation", threshold=0.10,
                        observed=price_cents, expected=10_000_000, satisfied=True,
                        is_fallback=True)],
        score=0.33, status="active",
        first_seen_at=observed_at, last_seen_at=observed_at, created_at=observed_at,
    ))
    repo.finish_run(det_run, "ok", observed_at)
    repo.conn.commit()
    return lid


class StubTransport:
    def __init__(self, failures: int = 0, error: Exception | None = None):
        self.failures = failures
        self.error = error
        self.calls: list[dict] = []

    def send(self, *, sender, recipient, subject, html, text):
        self.calls.append(dict(sender=sender, recipient=recipient, subject=subject, html=html, text=text))
        if self.failures > 0:
            self.failures -= 1
            raise SMTPError("temporary smtp failure")
        if self.error is not None:
            raise self.error


def _seed_one(db_path: Path) -> Repository:
    repo = _repo(db_path)
    _seed_detection(repo, db_path)
    return repo


def test_digest_build(db_path: Path):
    repo = _seed_one(db_path)
    det = repo.unnotified_detections()[0]

    subject, html, text = build_digest(_make_settings(db_path), repo, [det])

    assert "oportunidad" in subject
    assert "Av. Siempre Viva 742" in text
    assert "$" in text
    assert "85,000" in text
    assert "Subvaluado" in text
    assert "100,000" in text
    assert "inmoup.com.ar" in text
    assert "Av. Siempre Viva 742" in html


def test_dedupe(db_path: Path):
    repo = _seed_one(db_path)
    settings = _make_settings(db_path)
    transport = StubTransport()

    first = run_notify(settings, repo, transport=transport)
    second = run_notify(settings, repo, transport=transport)

    assert first["sent"] == 1
    assert len(transport.calls) == 1
    assert second["sent"] == 0
    assert second["skipped"] == 1
    assert len(transport.calls) == 1


def test_retry_then_success(db_path: Path):
    repo = _seed_one(db_path)
    settings = _make_settings(db_path)
    transport = StubTransport(failures=2)

    counts = run_notify(settings, repo, transport=transport)

    assert counts["attempts"] == 3
    assert counts["sent"] == 1
    assert counts["failed"] == 0
    row = repo.conn.execute("SELECT * FROM notifications").fetchone()
    assert row["status"] == "sent"
    assert row["attempt_count"] == 3


def test_retry_exhausted(db_path: Path):
    repo = _seed_one(db_path)
    settings = _make_settings(db_path)
    transport = StubTransport(error=SMTPError("server down"))

    counts = run_notify(settings, repo, transport=transport)

    assert counts["attempts"] == 3
    assert counts["failed"] == 1
    row = repo.conn.execute("SELECT * FROM notifications").fetchone()
    assert row["status"] == "failed"
    assert row["attempt_count"] == 3
    assert "server down" in row["last_error"]
