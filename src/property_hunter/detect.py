"""Opportunity detection rules (US3).

Evaluates configurable signals for each active sale listing against current
zone baselines, the latest value estimate (model or fallback) and recent price
history. Insufficient-data zones are skipped. Each detection is explainable:
signals carry observed/expected values and the threshold that fired (FR-021).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.models import DetectionRecord, Signal
from property_hunter.util import utcnow

logger = logging.getLogger("property_hunter.detect")


def _recent_drop(repo: Repository, listing_id: int, lookback_start: str) -> float | None:
    """Largest price-drop fraction within [lookback_start, now), else None."""
    largest: float | None = None
    for row in repo.price_history_for(listing_id):
        if row["observed_at"] < lookback_start:
            break
        old, new = row["old_price_cents"], row["new_price_cents"]
        if old and new and old > 0 and new < old:
            drop = (old - new) / old
            largest = drop if largest is None else max(largest, drop)
    return largest


def run_detect(settings: Settings, repo: Repository, run_id: int | None = None) -> dict:
    created = run_id is None
    run_id = run_id or repo.create_run(trigger="detect")
    now = utcnow()
    rules = settings.rules
    lookback_start = (
        datetime.now(timezone.utc) - timedelta(days=rules.price_drop_lookback_days)
    ).isoformat()

    counts = {"detections": 0, "evaluated": 0}
    rule_count = 3

    for row in repo.active_listings_with_price("sale"):
        zone_id = repo.zone_for(row["region"], row["barrio"])
        if zone_id is None:
            continue
        sale_bl = repo.baseline_for(zone_id, "sale")
        if sale_bl is None or not sale_bl["is_sufficient"]:
            continue
        rent_bl = repo.baseline_for(zone_id, "rent")
        pred = repo.prediction_for(row["id"])
        counts["evaluated"] += 1

        signals: list[Signal] = []

        if rules.undervaluation_enabled and pred is not None:
            expected = pred["predicted_price_cents"]
            observed = row["price_cents"]
            discount = (expected - observed) / expected if expected > 0 else 0.0
            signals.append(Signal(
                type="undervaluation",
                threshold=rules.undervaluation_threshold,
                observed=observed,
                expected=expected,
                satisfied=discount >= rules.undervaluation_threshold,
                model_version_id=pred["model_version_id"],
                is_fallback=bool(pred["is_fallback"]),
            ))

        if rules.yield_enabled and rent_bl is not None and rent_bl["median_price_per_m2_cents"] and row["covered_area_m2"]:
            annual_rent = rent_bl["median_price_per_m2_cents"] * row["covered_area_m2"] * 12
            yield_fraction = annual_rent / row["price_cents"] if row["price_cents"] else 0.0
            signals.append(Signal(
                type="yield",
                threshold=rules.yield_threshold,
                observed=yield_fraction,
                expected=rules.yield_threshold,
                satisfied=yield_fraction >= rules.yield_threshold,
            ))

        if rules.price_drop_enabled:
            drop = _recent_drop(repo, row["id"], lookback_start)
            if drop is not None:
                signals.append(Signal(
                    type="price_drop",
                    threshold=rules.price_drop_threshold,
                    observed=drop,
                    expected=rules.price_drop_threshold,
                    satisfied=drop >= rules.price_drop_threshold,
                ))

        satisfied = [s for s in signals if s.satisfied]
        if not satisfied:
            continue

        repo.insert_detection(DetectionRecord(
            listing_id=row["id"],
            run_id=run_id,
            baseline_id=sale_bl["id"],
            prediction_id=pred["id"] if pred is not None else None,
            signals=signals,
            score=len(satisfied) / rule_count,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
        ))
        repo.supersede_detections(run_id)
        counts["detections"] += 1

    if created:
        repo.finish_run(run_id, "ok", now)
    repo.conn.commit()
    logger.info("detect complete", extra={
        "ctx_run_id": run_id, "ctx_detections": counts["detections"],
        "ctx_evaluated": counts["evaluated"]})
    return counts
