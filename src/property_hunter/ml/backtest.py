"""Temporal backtest of the valuation model (US2).

Trains the same regressor used in production on sale listings observed before a
cutoff date and evaluates it against listings observed after that date. This
simulates "train on what we knew, predict what arrives next", which the random
held-out split in train.py cannot measure, and it includes a naive zone
median-per-m2 baseline so improvements are comparable.

This stage is read-only: it never persists a model version or predictions.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict

import numpy as np

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.ml.features import feature_names, feature_vector
from property_hunter.ml.train import _usable, build_model

logger = logging.getLogger("property_hunter.ml.backtest")


def _zone_median_ppm2(rows: list) -> dict[tuple[str, str], float]:
    """Zone median price-per-m2 computed from a training split only."""
    zones: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        area = r["covered_area_m2"] or r["total_area_m2"]
        if area and r["price_cents"] > 0:
            zones[(r["region"], r["barrio"])].append(r["price_cents"] / area)
    return {z: float(statistics.median(v)) for z, v in zones.items()}


def _split(rows: list, cutoff: str | None, test_split: float) -> tuple[list, list, str]:
    """Temporal split by first-seen date; returns (train, test, cutoff)."""
    ordered = sorted(rows, key=lambda r: r["listed_at"] or "")
    if cutoff:
        train = [r for r in ordered if (r["listed_at"] or "") < cutoff]
        test = [r for r in ordered if (r["listed_at"] or "") >= cutoff]
        return train, test, cutoff
    n_test = max(1, int(round(len(ordered) * test_split)))
    split_at = ordered[-n_test]["listed_at"]
    return ordered[:-n_test], ordered[-n_test:], split_at


def run_backtest(settings: Settings, repo: Repository, cutoff: str | None = None,
                 min_train_samples: int | None = None,
                 use_llm_features: bool = True) -> dict:
    """Backtest the valuation model on a temporal train/test split.

    ``use_llm_features`` toggles the LLM-derived feature block, enabling a
    clean before/after comparison of description-based features.
    """
    min_samples = min_train_samples or settings.ml.min_train_samples
    rows = _usable(repo.sale_listings_history("sale"))

    result: dict = {"samples": len(rows), "skipped": 0}
    if len(rows) < min_samples:
        result["skipped"] = 1
        logger.info("backtest skipped: insufficient samples",
                    extra={"ctx_samples": len(rows), "ctx_min_train_samples": min_samples})
        return result

    train, test, split_at = _split(rows, cutoff, settings.ml.test_split)
    result["train"] = len(train)
    result["test"] = len(test)
    result["cutoff"] = split_at
    result["test_split"] = settings.ml.test_split
    result["use_llm_features"] = use_llm_features

    barrios = sorted({r["barrio"] for r in train if r["barrio"]})
    types = sorted({r["property_type"] for r in train if r["property_type"]})
    names = feature_names(barrios, types, include_llm_features=use_llm_features)

    X_train = np.array([feature_vector(r, barrios, types, include_llm_features=use_llm_features)
                        for r in train], dtype=float)
    y_train = np.log1p(np.array([r["price_cents"] for r in train], dtype=float))
    model = build_model(settings)
    model.fit(X_train, y_train)

    test_rows = []
    for r in test:
        vec = np.array([feature_vector(r, barrios, types, include_llm_features=use_llm_features)],
                       dtype=float)
        pred = float(np.expm1(model.predict(vec)[0]))
        if pred > 0:
            test_rows.append((r, pred, float(r["price_cents"])))

    preds = [p for _, p, _ in test_rows]
    actuals = [a for _, _, a in test_rows]
    errors = [abs(p - a) / a for p, a in zip(preds, actuals)]

    r2 = None
    if len(actuals) >= 2:
        denom = sum((a - np.mean(actuals)) ** 2 for a in actuals)
        if denom > 0:
            r2 = float(1 - sum((p - a) ** 2 for p, a in zip(preds, actuals)) / denom)

    result["metrics"] = {
        "mape": float(np.mean(errors)),
        "mdape": float(statistics.median(errors)),
        "mae_cents": int(np.mean([abs(p - a) for p, a in zip(preds, actuals)])),
        "rmse_cents": int(float(np.sqrt(np.mean([(p - a) ** 2 for p, a in zip(preds, actuals)])))),
        "r2": r2,
    }

    fallback_preds: list[float] = []
    fallback_actuals: list[float] = []
    beats = 0
    zone_ppm2 = _zone_median_ppm2(train)
    for r, pred, actual in test_rows:
        area = r["covered_area_m2"] or r["total_area_m2"]
        ppm2 = zone_ppm2.get((r["region"], r["barrio"]))
        if area and ppm2:
            fp = ppm2 * area
            fallback_preds.append(fp)
            fallback_actuals.append(actual)
            if abs(pred - actual) < abs(fp - actual):
                beats += 1
    if fallback_preds:
        f_errors = [abs(p - a) / a for p, a in zip(fallback_preds, fallback_actuals)]
        result["fallback"] = {
            "mape": float(np.mean(f_errors)),
            "mdape": float(statistics.median(f_errors)),
            "rows": len(fallback_preds),
            "model_beats_fallback_frac": beats / len(fallback_preds),
        }

    per_zone: dict[str, list[float]] = defaultdict(list)
    for r, pred, actual in test_rows:
        per_zone[r["barrio"] or "(unknown)"].append(abs(pred - actual) / actual)
    result["per_zone"] = sorted(
        ({"barrio": b, "count": len(e), "mape": float(np.mean(e))} for b, e in per_zone.items()),
        key=lambda z: z["mape"], reverse=True)

    logger.info("backtest complete",
                extra={"ctx_samples": len(rows), "ctx_train": len(train), "ctx_test": len(test),
                       "ctx_mape": round(result["metrics"]["mape"], 4)})
    return result


def format_report(result: dict) -> str:
    """Human-readable rendering of the backtest result dict."""
    if result.get("skipped"):
        return (f"Backtest skipped: {result['samples']} samples < min_train_samples "
                "(raise ML_MIN_TRAIN_SAMPLES or check collected data).")
    lines = [
        f"Backtest: sale valuation, temporal split (cutoff={result['cutoff']})",
        f"  samples={result['samples']} train={result['train']} test={result['test']}",
    ]
    m = result["metrics"]
    lines.append(
        f"  Model     MAPE={m['mape']:.1%} MdAPE={m['mdape']:.1%} "
        f"MAE=${m['mae_cents']:,} RMSE=${m['rmse_cents']:,} "
        f"R2={m['r2']:.3f}" if m["r2"] is not None else
        f"  Model     MAPE={m['mape']:.1%} MdAPE={m['mdape']:.1%} "
        f"MAE=${m['mae_cents']:,} RMSE=${m['rmse_cents']:,} R2=n/a")
    if "fallback" in result:
        fb = result["fallback"]
        lines.append(
            f"  Fallback  MAPE={fb['mape']:.1%} MdAPE={fb['mdape']:.1%} "
            f"(n={fb['rows']}) model_beats_fallback={fb['model_beats_fallback_frac']:.0%}")
    lines.append("  Per-zone MAPE:")
    for z in result["per_zone"]:
        lines.append(f"    {z['barrio']:<24} n={z['count']:<4} MAPE={z['mape']:.1%}")
    return "\n".join(lines)
