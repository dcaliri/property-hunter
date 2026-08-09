"""Value estimates for active sale listings (US2).

Model-backed estimates when a current model version exists; otherwise fallback
to zone median price-per-m2 x covered area. Predictions are versioned and
recorded with is_fallback so consumers can distinguish confidence (FR-021).
"""

from __future__ import annotations

import json
import logging
import pickle

import numpy as np

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.ml.features import feature_vector
from property_hunter.models import PredictionRecord
from property_hunter.util import utcnow

logger = logging.getLogger("property_hunter.ml.predict")


def _load_bundle(mv) -> dict | None:
    try:
        return pickle.loads(bytes(mv["blob"]))
    except Exception:
        logger.warning("could not load model bundle; using fallback",
                       extra={"ctx_model_version_id": mv["id"]})
        return None


def _fallback_estimate(repo: Repository, row) -> int | None:
    """zone median price-per-m2 x area, or None when no baseline exists."""
    zone_id = repo.zone_for(row["region"], row["barrio"])
    if zone_id is None:
        return None
    bl = repo.baseline_for(zone_id, "sale")
    if bl is None or not bl["median_price_per_m2_cents"]:
        return None
    area = row["covered_area_m2"] or row["total_area_m2"]
    if not area:
        return None
    return int(round(bl["median_price_per_m2_cents"] * area))


def run_predict(settings: Settings, repo: Repository, run_id: int | None = None) -> dict:
    created = run_id is None
    run_id = run_id or repo.create_run(trigger="predict")
    predicted_at = utcnow()

    mv = repo.current_model_version()
    bundle = _load_bundle(mv) if mv else None
    model = bundle["model"] if bundle else None

    counts = {"predicted": 0, "fallback": 0, "model_id": mv["id"] if mv else None}
    feature_importances = None
    if model is not None and hasattr(model, "feature_importances_"):
        feature_importances = dict(zip(bundle["feature_names"],
                                       [float(v) for v in model.feature_importances_]))

    for row in repo.active_listings_with_price("sale"):
        estimate = None
        is_fallback = True
        if model is not None:
            vec = np.array([feature_vector(row, bundle["barrios"], bundle["types"])],
                           dtype=float)[:, :len(bundle["feature_names"])]
            try:
                estimate = int(round(float(np.expm1(model.predict(vec)[0]))))
                is_fallback = False
            except Exception:
                logger.warning("prediction failed for listing; falling back",
                               extra={"ctx_listing_id": row["id"]})
        if estimate is None or estimate <= 0:
            estimate = _fallback_estimate(repo, row)
        if estimate is None or estimate <= 0:
            continue
        repo.insert_prediction(PredictionRecord(
            listing_id=row["id"],
            model_version_id=mv["id"] if not is_fallback else None,
            run_id=run_id,
            predicted_price_cents=estimate,
            is_fallback=is_fallback,
            feature_importances=feature_importances,
            predicted_at=predicted_at,
        ))
        counts["predicted"] += 1
        if is_fallback:
            counts["fallback"] += 1

    if created:
        repo.finish_run(run_id, "ok", predicted_at)
    repo.conn.commit()
    logger.info("predict complete", extra={
        "ctx_run_id": run_id, "ctx_predicted": counts["predicted"],
        "ctx_fallback": counts["fallback"], "ctx_model_id": counts["model_id"]})
    return counts
