"""Valuation model training + persistence (US2).

Trains a HistGradientBoostingRegressor on log1p prices with a held-out split,
records quality metrics, and persists a pickled bundle (model + feature
vocabulary) as a versioned model_versions row. Below ML_MIN_TRAIN_SAMPLES the
stage is skipped and predictions fall back to zone median price-per-m2.
"""

from __future__ import annotations

import logging
import pickle
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.ml.features import feature_names, feature_vector
from property_hunter.models import ModelVersionRecord
from property_hunter.util import utcnow, window_bounds

logger = logging.getLogger("property_hunter.ml.train")


def _usable(rows: list) -> list:
    """Sale listings with a positive price and at least one area signal."""
    return [r for r in rows
            if r["price_cents"] > 0
            and (r["covered_area_m2"] or r["total_area_m2"])]


def run_train(settings: Settings, repo: Repository, run_id: int | None = None) -> dict:
    created = run_id is None
    run_id = run_id or repo.create_run(trigger="train")

    rows = _usable(repo.active_listings_with_price("sale"))
    counts = {"samples": len(rows), "skipped": 0, "model_version_id": None}

    if len(rows) < settings.ml.min_train_samples:
        counts["skipped"] = 1
        logger.info("train skipped: insufficient samples", extra={
            "ctx_run_id": run_id, "ctx_samples": len(rows),
            "ctx_min_train_samples": settings.ml.min_train_samples})
        if created:
            repo.finish_run(run_id, "skipped", utcnow())
        repo.conn.commit()
        return counts

    barrios = sorted({r["barrio"] for r in rows if r["barrio"]})
    types = sorted({r["property_type"] for r in rows if r["property_type"]})
    names = feature_names(barrios, types)

    X = np.array([feature_vector(r, barrios, types) for r in rows], dtype=float)
    y = np.log1p(np.array([r["price_cents"] for r in rows], dtype=float))

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=settings.ml.test_split, random_state=settings.ml.random_state)

    model = HistGradientBoostingRegressor(
        max_iter=150, learning_rate=0.08, min_samples_leaf=4,
        random_state=settings.ml.random_state)
    model.fit(X_train, y_train)

    y_pred_log = model.predict(X_test)
    if len(y_test) >= 2:
        r2 = float(r2_score(y_test, y_pred_log))
    else:
        r2 = None
    mae_cents = int(mean_absolute_error(np.expm1(y_test), np.expm1(y_pred_log)))

    trained_at = utcnow()
    window_start, window_end = window_bounds(settings.baselines.window_days)
    bundle: dict[str, Any] = {
        "model": model,
        "barrios": barrios,
        "types": types,
        "feature_names": names,
    }
    mv_id = repo.insert_model_version(ModelVersionRecord(
        run_id=run_id,
        trained_at=trained_at,
        training_window_start=window_start,
        training_window_end=window_end,
        training_count=len(rows),
        r2_score=r2,
        mae_cents=mae_cents,
        blob=pickle.dumps(bundle),
        notes=f"sale; features={len(names)}; r2={r2 if r2 is None else round(r2, 3)}",
    ))
    counts["model_version_id"] = mv_id

    if created:
        repo.finish_run(run_id, "ok", trained_at)
    repo.conn.commit()
    logger.info("train complete", extra={
        "ctx_run_id": run_id, "ctx_samples": len(rows),
        "ctx_r2": r2 if r2 is None else round(r2, 3),
        "ctx_mae_cents": mae_cents})
    return counts
