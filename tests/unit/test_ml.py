"""Tests for the valuation model train/predict stages (US2)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from property_hunter.config import Settings
from property_hunter.db import Repository, connect
from property_hunter.models import ListingRecord, PriceObservation
from property_hunter.ml.train import run_train
from property_hunter.ml.predict import run_predict
from property_hunter.analyze import run_analyze


def _observed_at(days_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_settings(db_path: Path, min_train_samples: int = 200,
                   min_obs: int = 1) -> Settings:
    s = Settings.from_env()
    s.db_path = db_path
    s.ml.min_train_samples = min_train_samples
    s.baselines.min_observations_per_zone = min_obs
    return s


def _repo(db_path: Path) -> Repository:
    return Repository(connect(db_path))


def _seed_sale_batch(repo: Repository, listings: list[tuple[int, str, int, int, int]]) -> None:
    """listings: (source_listing_id, barrio, beds, covered_m2, price_cents)."""
    observed_at = _observed_at()
    run_id = repo.create_run()
    for sid, barrio, beds, covered, price in listings:
        lid = repo.upsert_listing(ListingRecord(
            source="inmoup", source_listing_id=sid,
            source_url=f"https://inmoup.com.ar/inmuebles/{sid}",
            operation="sale", property_type="departamento",
            barrio=barrio, region="Comuna 14", beds=beds, baths=beds - 1 if beds > 1 else 1,
            covered_area_m2=float(covered), price_cents=price, observed_at=observed_at,
        ))
        repo.insert_observation(PriceObservation(
            run_id=run_id, listing_id=lid, price_cents=price, observed_at=observed_at,
        ))
    repo.finish_run(run_id, "ok", observed_at)
    repo.conn.commit()


def _synthetic_listings(n: int = 60, seed: int = 7) -> list[tuple[int, str, int, int, int]]:
    rng = random.Random(seed)
    barrios = ["Palermo", "Almagro", "Belgrano", "Caballito", "Recoleta"]
    out: list[tuple[int, str, int, int, int]] = []
    for i in range(n):
        barrio = barrios[i % len(barrios)]
        beds = rng.choice([1, 2, 3, 4])
        covered = rng.choice([35, 45, 55, 70, 85, 100])
        base = 4_000_000 + {"Palermo": 2_000_000, "Recoleta": 2_400_000,
                            "Belgrano": 1_200_000, "Caballito": 0,
                            "Almagro": 300_000}[barrio]
        price = int(base + covered * 45_000 + beds * 900_000 * (1 + rng.uniform(-0.02, 0.02)))
        out.append((20_000 + i, barrio, beds, covered, price))
    return out


def test_train_quality(db_path: Path):
    repo = _repo(db_path)
    _seed_sale_batch(repo, _synthetic_listings())
    settings = _make_settings(db_path, min_train_samples=30)

    counts = run_train(settings, repo)

    assert counts["samples"] == 60
    assert counts["skipped"] == 0
    mv = repo.current_model_version()
    assert mv is not None
    assert mv["r2_score"] is not None
    assert mv["r2_score"] > 0.8
    assert mv["mae_cents"] is not None

    counts = run_predict(settings, repo)
    assert counts["predicted"] == 60
    assert counts["fallback"] == 0
    assert counts["model_id"] == mv["id"]

    for sid, _barrio, _beds, _covered, price in _synthetic_listings()[:20]:
        row = repo.conn.execute(
            "SELECT l.id FROM listings l WHERE l.source_listing_id=?", (sid,)
        ).fetchone()
        pred = repo.prediction_for(row["id"])
        assert pred is not None
        assert pred["is_fallback"] == 0
        assert pred["model_version_id"] == mv["id"]
        assert abs(pred["predicted_price_cents"] - price) / price < 0.20


def test_fallback(db_path: Path):
    repo = _repo(db_path)
    _seed_sale_batch(repo, _synthetic_listings(5))
    settings = _make_settings(db_path, min_train_samples=1000)

    run_analyze(settings, repo)
    counts = run_train(settings, repo)
    assert counts["skipped"] == 1
    assert repo.current_model_version() is None

    counts = run_predict(settings, repo)
    assert counts["fallback"] == 5

    first = repo.active_listings("sale")[0]
    pred = repo.prediction_for(first["id"])
    assert pred is not None
    assert pred["is_fallback"] == 1
    assert pred["model_version_id"] is None
    assert pred["predicted_price_cents"] > 0


def test_retrain_supersede(db_path: Path):
    repo = _repo(db_path)
    _seed_sale_batch(repo, _synthetic_listings(40, seed=1))
    settings = _make_settings(db_path, min_train_samples=30)

    run_train(settings, repo)
    first_mv = repo.current_model_version()

    _seed_sale_batch(repo, _synthetic_listings(40, seed=2))
    run_train(settings, repo)
    second_mv = repo.current_model_version()

    assert first_mv is not None and second_mv is not None
    assert second_mv["id"] != first_mv["id"]
    stale = repo.conn.execute(
        "SELECT is_current FROM model_versions WHERE id=?", (first_mv["id"],)
    ).fetchone()
    assert stale["is_current"] == 0
    assert second_mv["is_current"] == 1

    run_predict(settings, repo)
    for lid in [r["id"] for r in repo.active_listings("sale")][:5]:
        pred = repo.prediction_for(lid)
        assert pred["model_version_id"] == second_mv["id"]
