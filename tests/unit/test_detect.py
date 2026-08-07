"""Tests for the opportunity detection stage (US3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from property_hunter.config import Settings
from property_hunter.db import Repository, connect
from property_hunter.detect import run_detect
from property_hunter.analyze import run_analyze
from property_hunter.models import ListingRecord, PredictionRecord, PriceObservation

_counter = [50_000]


def _observed_at(days_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_settings(db_path: Path) -> Settings:
    s = Settings.from_env()
    s.db_path = db_path
    s.baselines.min_observations_per_zone = 1
    return s


def _repo(db_path: Path) -> Repository:
    return Repository(connect(db_path))


def _add_sale(repo: Repository, *, barrio: str, price_cents: int, covered: float,
              beds: int = 2, days_ago: float = 1.0) -> int:
    _counter[0] += 1
    sid = _counter[0]
    observed_at = _observed_at(days_ago)
    run_id = repo.create_run()
    lid = repo.upsert_listing(ListingRecord(
        source="inmoup", source_listing_id=sid,
        source_url=f"https://inmoup.com.ar/inmuebles/{sid}",
        operation="sale", property_type="departamento",
        barrio=barrio, region="Comuna 14", beds=beds, covered_area_m2=covered,
        price_cents=price_cents, observed_at=observed_at,
    ))
    repo.insert_observation(PriceObservation(
        run_id=run_id, listing_id=lid, price_cents=price_cents, observed_at=observed_at,
    ))
    repo.finish_run(run_id, "ok", observed_at)
    repo.conn.commit()
    return lid


def _add_rent(repo: Repository, *, barrio: str, rent_cents: int, covered: float) -> int:
    _counter[0] += 1
    sid = _counter[0]
    observed_at = _observed_at()
    run_id = repo.create_run()
    lid = repo.upsert_listing(ListingRecord(
        source="inmoup", source_listing_id=sid,
        source_url=f"https://inmoup.com.ar/inmuebles/{sid}",
        operation="rent", property_type="departamento",
        barrio=barrio, region="Comuna 14", covered_area_m2=covered,
        price_cents=rent_cents, observed_at=observed_at,
    ))
    repo.insert_observation(PriceObservation(
        run_id=run_id, listing_id=lid, price_cents=rent_cents, observed_at=observed_at,
    ))
    repo.finish_run(run_id, "ok", observed_at)
    repo.conn.commit()
    return lid


def _seed_prediction(repo: Repository, listing_id: int, estimate_cents: int,
                     is_fallback: bool = False) -> int:
    run_id = repo.create_run(trigger="predict")
    mv_id = None
    if not is_fallback:
        from property_hunter.models import ModelVersionRecord
        mv_id = repo.insert_model_version(ModelVersionRecord(
            run_id=run_id, trained_at=_observed_at(),
            training_window_start=_observed_at(90), training_window_end=_observed_at(),
            training_count=1, blob=b"placeholder",
        ))
    repo.insert_prediction(PredictionRecord(
        listing_id=listing_id, model_version_id=mv_id,
        run_id=run_id, predicted_price_cents=estimate_cents, is_fallback=is_fallback,
        predicted_at=_observed_at(),
    ))
    repo.finish_run(run_id, "ok", _observed_at())
    repo.conn.commit()
    return repo.prediction_for(listing_id)["id"]


def test_undervaluation(db_path: Path):
    repo = _repo(db_path)
    lid = _add_sale(repo, barrio="Palermo", price_cents=8_500_000, covered=50.0)
    run_analyze(_make_settings(db_path), repo)
    pred_id = _seed_prediction(repo, lid, estimate_cents=10_000_000)

    counts = run_detect(_make_settings(db_path), repo)

    det = repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()
    assert counts["detections"] == 1
    assert det is not None
    assert det["prediction_id"] == pred_id
    signals = __import__("json").loads(det["signals"])
    uv = next(s for s in signals if s["type"] == "undervaluation")
    assert uv["satisfied"] is True
    assert uv["observed"] == 8_500_000
    assert uv["expected"] == 10_000_000
    assert uv["is_fallback"] is False
    assert uv["model_version_id"] == repo.current_model_version()["id"]


def test_undervaluation_fallback(db_path: Path):
    repo = _repo(db_path)
    lid = _add_sale(repo, barrio="Palermo", price_cents=8_500_000, covered=50.0)
    run_analyze(_make_settings(db_path), repo)
    _seed_prediction(repo, lid, estimate_cents=10_000_000, is_fallback=True)

    run_detect(_make_settings(db_path), repo)

    det = repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()
    signals = __import__("json").loads(det["signals"])
    uv = next(s for s in signals if s["type"] == "undervaluation")
    assert uv["satisfied"] is True
    assert uv["is_fallback"] is True


def test_yield(db_path: Path):
    repo = _repo(db_path)
    _add_rent(repo, barrio="Palermo", rent_cents=600_000, covered=50.0)  # 12000 USD/m2/yr
    lid = _add_sale(repo, barrio="Palermo", price_cents=10_000_000, covered=50.0)
    run_analyze(_make_settings(db_path), repo)

    run_detect(_make_settings(db_path), repo)

    det = repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()
    assert det is not None
    signals = __import__("json").loads(det["signals"])
    y = next(s for s in signals if s["type"] == "yield")
    assert y["satisfied"] is True
    annual = 600_000 * 12
    assert abs(y["observed"] - annual / 10_000_000) < 1e-9
    assert y["expected"] == 0.06


def test_price_drop(db_path: Path):
    repo = _repo(db_path)
    lid = _add_sale(repo, barrio="Palermo", price_cents=9_000_000, covered=50.0, days_ago=2)
    now = _observed_at(0)
    repo.insert_price_history(lid, 10_000_000, 9_000_000, "USD", _observed_at(5), 1)
    repo.conn.commit()
    run_analyze(_make_settings(db_path), repo)

    run_detect(_make_settings(db_path), repo)

    det = repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()
    assert det is not None
    signals = __import__("json").loads(det["signals"])
    pd = next(s for s in signals if s["type"] == "price_drop")
    assert pd["satisfied"] is True
    assert abs(pd["observed"] - 0.10) < 1e-9
    assert pd["expected"] == 0.05


def test_price_drop_old_does_not_fire(db_path: Path):
    repo = _repo(db_path)
    lid = _add_sale(repo, barrio="Palermo", price_cents=9_000_000, covered=50.0, days_ago=60)
    repo.insert_price_history(lid, 10_000_000, 9_000_000, "USD", _observed_at(60), 1)
    repo.conn.commit()
    run_analyze(_make_settings(db_path), repo)

    run_detect(_make_settings(db_path), repo)

    det = repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()
    assert det is None


def test_rule_configuration(db_path: Path):
    from property_hunter.config import RuleConfig

    repo = _repo(db_path)
    lid = _add_sale(repo, barrio="Palermo", price_cents=8_500_000, covered=50.0)
    run_analyze(_make_settings(db_path), repo)
    _seed_prediction(repo, lid, estimate_cents=10_000_000)

    settings = _make_settings(db_path)
    settings.rules = RuleConfig(undervaluation_enabled=False, yield_enabled=False,
                                price_drop_enabled=True, price_drop_threshold=0.05)
    run_detect(settings, repo)

    det = repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()
    assert det is None

    settings.rules = RuleConfig(undervaluation_enabled=True, yield_enabled=False,
                                price_drop_enabled=False, undervaluation_threshold=0.10)
    run_detect(settings, repo)
    det = repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()
    assert det is not None


def test_detection_supersede(db_path: Path):
    repo = _repo(db_path)
    lid = _add_sale(repo, barrio="Palermo", price_cents=8_500_000, covered=50.0)
    run_analyze(_make_settings(db_path), repo)
    _seed_prediction(repo, lid, estimate_cents=10_000_000)

    run_detect(_make_settings(db_path), repo)
    run_detect(_make_settings(db_path), repo)

    rows = list(repo.conn.execute(
        "SELECT * FROM detections WHERE listing_id=? ORDER BY id", (lid,)
    ))
    assert len(rows) == 2
    assert rows[0]["status"] == "superseded"
    assert rows[1]["status"] == "active"
    active = repo.active_detections()
    assert len(active) == 1
    assert active[0]["id"] == rows[1]["id"]


def test_insufficient_zone_skipped(db_path: Path):
    repo = _repo(db_path)
    lid = _add_sale(repo, barrio="Palermo", price_cents=8_500_000, covered=50.0)
    run_analyze(_make_settings(db_path), repo)
    repo.conn.execute("UPDATE baselines SET is_sufficient=0")
    repo.conn.commit()

    counts = run_detect(_make_settings(db_path), repo)

    assert counts["detections"] == 0
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM detections WHERE listing_id=?", (lid,)
    ).fetchone()[0] == 0
