"""Tests for the zone-baseline analysis stage (US2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from property_hunter.analyze import run_analyze
from property_hunter.config import Settings
from property_hunter.db import Repository, connect
from property_hunter.models import ListingRecord, PriceObservation

_seed_counter = [10_000]


def _observed_at(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_settings(db_path: Path, min_obs: int = 5) -> Settings:
    s = Settings.from_env()
    s.db_path = db_path
    s.baselines.min_observations_per_zone = min_obs
    return s


def _add_listing(repo: Repository, *, operation: str, region: str, barrio: str,
                 price_cents: int, covered: float | None = None,
                 property_type: str = "departamento", days_ago: float = 1.0) -> int:
    _seed_counter[0] += 1
    sid = _seed_counter[0]
    observed_at = _observed_at(days_ago)
    run_id = repo.create_run()
    lid = repo.upsert_listing(ListingRecord(
        source="inmoup", source_listing_id=sid,
        source_url=f"https://inmoup.com.ar/inmuebles/{sid}",
        operation=operation, property_type=property_type, barrio=barrio, region=region,
        covered_area_m2=covered, price_cents=price_cents, observed_at=observed_at,
    ))
    repo.insert_observation(PriceObservation(
        run_id=run_id, listing_id=lid, price_cents=price_cents, observed_at=observed_at,
    ))
    repo.finish_run(run_id, "ok", observed_at)
    repo.conn.commit()
    return lid


def _repo(db_path: Path) -> Repository:
    return Repository(connect(db_path))


def test_median_baseline(db_path: Path):
    repo = _repo(db_path)
    for price in (20_000_000, 30_000_000, 25_000_000):
        _add_listing(repo, operation="sale", region="Comuna 14", barrio="Palermo",
                     price_cents=price, covered=50.0)
    for price in (5_000_000, 7_000_000):
        _add_listing(repo, operation="rent", region="Comuna 5", barrio="Almagro",
                     price_cents=price, covered=50.0)

    counts = run_analyze(_make_settings(db_path, min_obs=2), repo)

    assert counts["baselines"] == 2
    assert counts["zones"] == 2
    assert counts["sufficient_zones"] == 2

    baselines = {(_["barrio"], _["operation"]): _ for _ in repo.latest_baselines()}
    palermo = baselines[("Palermo", "sale")]
    assert palermo["observation_count"] == 3
    assert palermo["median_price_cents"] == 25_000_000
    assert palermo["median_price_per_m2_cents"] == 500_000
    assert palermo["is_sufficient"] == 1

    almagro = baselines[("Almagro", "rent")]
    assert almagro["median_rent_cents"] == 6_000_000
    assert almagro["median_price_per_m2_cents"] == 120_000


def test_insufficient_zone(db_path: Path):
    repo = _repo(db_path)
    for price in (20_000_000, 22_000_000):
        _add_listing(repo, operation="sale", region="Comuna 14", barrio="Palermo",
                     price_cents=price, covered=50.0)

    run_analyze(_make_settings(db_path, min_obs=5), repo)

    baselines = repo.latest_baselines()
    assert len(baselines) == 1
    assert baselines[0]["is_sufficient"] == 0
    assert baselines[0]["median_price_cents"] == 21_000_000


def test_window_immutable(db_path: Path):
    repo = _repo(db_path)
    for price in (20_000_000, 30_000_000):
        _add_listing(repo, operation="sale", region="Comuna 14", barrio="Palermo",
                     price_cents=price, covered=50.0)

    run_analyze(_make_settings(db_path, min_obs=2), repo)
    run_analyze(_make_settings(db_path, min_obs=2), repo)

    zone = repo.conn.execute("SELECT id FROM zones WHERE barrio='Palermo'").fetchone()
    rows = list(repo.conn.execute(
        "SELECT * FROM baselines WHERE zone_id=? AND operation='sale' ORDER BY id", (zone["id"],)
    ))
    assert len(rows) == 2
    latest = repo.baseline_for(zone["id"], "sale")
    assert latest["id"] == rows[-1]["id"]


def test_perf_100k(db_path: Path):
    """SC-003: baseline computation over ~100k observations completes <60s."""
    import time

    repo = _repo(db_path)
    conn = repo.conn
    n_zones, n_listings = 200, 100_000
    observed_at = _observed_at(1)

    conn.executemany(
        "INSERT INTO zones (region, barrio) VALUES (?, ?)",
        [(f"R{r // 20:02d}", f"B{r % 20:02d}") for r in range(n_zones)],
    )
    run_id = repo.create_run()
    conn.executemany(
        """INSERT INTO listings (source, source_listing_id, source_url, operation, property_type,
               barrio, region, covered_area_m2, first_seen_at, last_seen_at, is_active, created_at, updated_at)
           VALUES ('inmoup', ?, ?, 'sale', 'departamento', ?, ?, 50.0, ?, ?, 1, ?, ?)""",
        [
            (i, f"https://inmoup.com.ar/inmuebles/{i}", f"B{i % 20:02d}", f"R{(i // 20) % 10:02d}",
             observed_at, observed_at, observed_at, observed_at)
            for i in range(n_listings)
        ],
    )
    conn.executemany(
        """INSERT INTO observations (run_id, listing_id, price_cents, currency, observed_at, is_active)
           VALUES (?, ?, ?, 'USD', ?, 1)""",
        [(run_id, i + 1, 5_000_000 + (i % n_zones) * 10_000, observed_at) for i in range(n_listings)],
    )
    repo.finish_run(run_id, "ok", observed_at)
    conn.commit()

    t0 = time.monotonic()
    counts = run_analyze(_make_settings(db_path, min_obs=5), repo)
    elapsed = time.monotonic() - t0

    assert counts["baselines"] == n_zones
    assert counts["observations"] == n_listings
    assert elapsed < 60.0
