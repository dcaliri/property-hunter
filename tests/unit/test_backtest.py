"""Tests for the temporal valuation backtest (US2)."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from property_hunter.config import Settings
from property_hunter.db import Repository, connect
from property_hunter.models import ListingRecord, PriceObservation
from property_hunter.ml.backtest import format_report, run_backtest


def _observed_at(days_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_settings(db_path: Path, min_train_samples: int = 200) -> Settings:
    s = Settings.from_env()
    s.db_path = db_path
    s.ml.min_train_samples = min_train_samples
    return s


def _repo(db_path: Path) -> Repository:
    return Repository(connect(db_path))


def _seed_sale_history(repo: Repository, listings: list[tuple[int, str, int, int, int]],
                       features: dict[int, dict] | None = None) -> None:
    """Seed sale listings with first-seen dates spread over past days.

    listings: (source_listing_id, barrio, beds, covered_m2, price_cents).
    Listing i is first seen ``n - i`` days ago, so the final 20% are the most
    recent and form the natural test split. ``features`` maps source_listing_id
    to an llm_features dict (stored as JSON).
    """
    features = features or {}
    n = len(listings)
    for i, (sid, barrio, beds, covered, price) in enumerate(listings):
        observed_at = _observed_at(n - i)
        run_id = repo.create_run()
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
        if sid in features:
            repo.conn.execute(
                "UPDATE listings SET llm_features=? WHERE id=?",
                (json.dumps(features[sid]), lid),
            )
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


def test_backtest_temporal_split(db_path: Path):
    repo = _repo(db_path)
    _seed_sale_history(repo, _synthetic_listings())
    settings = _make_settings(db_path, min_train_samples=30)

    result = run_backtest(settings, repo)

    assert result["samples"] == 60
    assert result["skipped"] == 0
    assert result["train"] == 48
    assert result["test"] == 12

    metrics = result["metrics"]
    assert metrics["mape"] < 0.25
    assert metrics["mdape"] < 0.25
    assert metrics["mae_cents"] > 0
    assert metrics["rmse_cents"] > 0
    assert metrics["r2"] is not None and metrics["r2"] > 0.5

    assert "fallback" in result
    assert result["fallback"]["model_beats_fallback_frac"] > 0.5
    assert any(z["barrio"] == "Palermo" for z in result["per_zone"])


def test_backtest_explicit_cutoff(db_path: Path):
    repo = _repo(db_path)
    _seed_sale_history(repo, _synthetic_listings(40))
    settings = _make_settings(db_path, min_train_samples=10)
    cutoff = _observed_at(20.5)

    result = run_backtest(settings, repo, cutoff=cutoff)

    assert result["skipped"] == 0
    assert result["cutoff"] == cutoff
    assert result["train"] == 20
    assert result["test"] == 20
    assert result["metrics"]["mape"] < 0.25


def test_split_prefers_date_posted_over_listed_at():
    from property_hunter.ml.backtest import _split

    rows = [
        {"id": 1, "date_posted": "2026-02-01", "listed_at": "2026-01-01"},
        {"id": 2, "date_posted": "2026-01-01", "listed_at": "2026-02-01"},
    ]
    train, test, _ = _split(rows, "2026-01-15", 0.5)

    assert [r["id"] for r in train] == [2]
    assert [r["id"] for r in test] == [1]


def test_split_cutoff_reproduces_default_split_on_ties():
    from property_hunter.ml.backtest import _split

    rows = [
        {"id": 1, "date_posted": "2026-01-01", "listed_at": "x"},
        {"id": 2, "date_posted": "2026-01-01", "listed_at": "x"},
        {"id": 3, "date_posted": "2026-02-01", "listed_at": "x"},
        {"id": 4, "date_posted": "2026-02-01", "listed_at": "x"},
        {"id": 5, "date_posted": "2026-03-01", "listed_at": "x"},
    ]
    train, test, cutoff = _split(rows, None, 0.4)

    assert [r["id"] for r in train] == [1, 2]
    assert [r["id"] for r in test] == [3, 4, 5]
    train2, test2, _ = _split(rows, cutoff, 0.4)
    assert [r["id"] for r in train2] == [1, 2]
    assert [r["id"] for r in test2] == [3, 4, 5]


def test_backtest_insufficient_samples(db_path: Path):
    repo = _repo(db_path)
    _seed_sale_history(repo, _synthetic_listings(5, seed=1))
    settings = _make_settings(db_path, min_train_samples=1000)

    result = run_backtest(settings, repo)

    assert result["samples"] == 5
    assert result["skipped"] == 1
    assert "metrics" not in result
    assert "Backtest skipped" in format_report(result)


def test_backtest_report_renders(db_path: Path):
    repo = _repo(db_path)
    _seed_sale_history(repo, _synthetic_listings(40, seed=2))
    settings = _make_settings(db_path, min_train_samples=10)

    result = run_backtest(settings, repo)
    report = format_report(result)

    assert "Backtest: sale valuation" in report
    assert "MAPE=" in report
    assert "Per-zone MAPE:" in report


def test_backtest_llm_features_help(db_path: Path):
    """The LLM condition signal should reduce MAPE vs the same data without it."""
    listings: list[tuple[int, str, int, int, int]] = []
    features: dict[int, dict] = {}
    rng = random.Random(3)
    barrios = ["Palermo", "Almagro", "Belgrano", "Caballito", "Recoleta"]
    for i in range(60):
        barrio = barrios[i % len(barrios)]
        beds = rng.choice([1, 2, 3, 4])
        covered = rng.choice([35, 45, 55, 70, 85, 100])
        base = 4_000_000 + {"Palermo": 2_000_000, "Recoleta": 2_400_000,
                            "Belgrano": 1_200_000, "Caballito": 0,
                            "Almagro": 300_000}[barrio]
        need_work = i % 3 == 0
        condition = "a_refaccionar" if need_work else "buen_estado"
        discount = 0.78 if need_work else 1.0
        price = int((base + covered * 45_000 + beds * 900_000) * discount)
        listings.append((20_000 + i, barrio, beds, covered, price))
        features[20_000 + i] = {"condition": condition, "floor": None, "expensas": None,
                                "orientation": None, "has_parking": False, "has_pool": False,
                                "has_gym": False, "has_terrace": False, "has_balcony": False,
                                "has_security": False}

    repo = _repo(db_path)
    _seed_sale_history(repo, listings, features=features)
    settings = _make_settings(db_path, min_train_samples=30)

    with_llm = run_backtest(settings, repo, use_llm_features=True)
    without_llm = run_backtest(settings, repo, use_llm_features=False)

    assert without_llm["metrics"]["mape"] > 0.15
    assert with_llm["metrics"]["mape"] < without_llm["metrics"]["mape"]
